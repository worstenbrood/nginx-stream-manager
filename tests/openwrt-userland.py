"""Real OpenWrt UCI/rpcd/procd/firewall4 checks; Docker supplies the kernel."""
import json
from pathlib import Path
import socket
import socketserver
import subprocess
import threading
import time

if not Path('/.dockerenv').is_file() or not Path('/src/tests/openwrt-userland.sh').is_file():
    raise SystemExit('Run only inside the disposable OpenWrt test container.')


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=45)


def rpc(method, **args):
    return json.loads(run('ubus', 'call', 'luci.nginx-stream-manager', method, json.dumps(args)))


def control(action, expected_ok=True):
    result = rpc('control', action=action)
    assert result['ok'], result
    for _ in range(100):
        status = rpc('status')
        operation = status.get('operation')
        if operation and not operation['pending']:
            assert operation['ok'] is expected_ok, operation
            return status
        time.sleep(.1)
    raise AssertionError('Control action timed out: ' + action)


def check_forwarding():
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(family, kind) as client:
                client.settimeout(3)
                client.connect((address, 19060))
                client.send(b'openwrt-test')
                assert client.recv(1024) == b'openwrt-test'


assert not rpc('status')['running']

class TCPReply(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(1024))

class UDPReply(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(data, self.client_address)

class TCPServer6(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6

class UDPServer6(socketserver.ThreadingUDPServer):
    address_family = socket.AF_INET6

servers = (
    socketserver.ThreadingTCPServer(('127.0.0.1', 19061), TCPReply),
    socketserver.ThreadingUDPServer(('127.0.0.1', 19061), UDPReply),
    socketserver.ThreadingTCPServer(('127.0.0.1', 19062), TCPReply),
    socketserver.ThreadingUDPServer(('127.0.0.1', 19062), UDPReply),
    TCPServer6(('::1', 19061), TCPReply),
    UDPServer6(('::1', 19061), UDPReply),
)
for server in servers:
    threading.Thread(target=server.serve_forever, daemon=True).start()

config = Path('/etc/config/nginx_stream_manager')
config.write_text("""config global 'main'
 option enabled '1'
 option workers '1'
config pool 'testpool'
config backend 'testbackend'
 option pool 'testpool'
 option address '127.0.0.1'
 option port '19061'
config service 'testservice'
 option pool 'testpool'
 option protocol 'both'
 option listen_address '127.0.0.1'
 option listen_port '19060'
 option open_firewall '1'
 option traffic_history '1'
 option udp_responses '1'
 list ingress_interface 'eth0'
config backend 'testbackend2'
 option pool 'testpool'
 option address '127.0.0.1'
 option port '19062'
config pool 'testpool6'
config backend 'testbackend6'
 option pool 'testpool6'
 option address '::1'
 option port '19061'
config service 'testservice6'
 option pool 'testpool6'
 option protocol 'both'
 option listen_address '::1'
 option listen_port '19060'
 option traffic_history '1'
 option udp_responses '1'
""")
assert rpc('validate')['ok']
assert control('start')['running']
check_forwarding()
check_forwarding()
for _ in range(30):
    metrics = rpc('traffic', service='testservice', hours=1, protocol='all', upstream='')
    if len(metrics.get('series', [])) == 2 and sum(s['attempts'] for s in metrics['series']) == 4:
        break
    time.sleep(.1)
assert metrics['ok'] and len(metrics['series']) == 2, metrics
assert all(s['sent'] > 0 and s['received'] > 0 for s in metrics['series']), metrics
for service in ('testservice', 'testservice6'):
    for protocol in ('TCP', 'UDP'):
        measured = rpc('traffic', service=service, protocol=protocol)['series']
        assert sum(s['sent'] for s in measured) == 2 * len(b'openwrt-test'), measured
        assert sum(s['received'] for s in measured) == 2 * len(b'openwrt-test'), measured
        assert sum(s['attempts'] for s in measured) == 2, measured
assert rpc('traffic', service='testservice6')['series'][0]['upstream'] == '[::1]:19061'
assert not metrics['limited'] and not metrics['pending'], metrics
assert all(s['connect_samples'] > 0 and s['sessions'] > 0 for s in metrics['series']), metrics
assert 'nsm_24h_sent_bytes' in rpc('metrics')['text']
snapshots = rpc('config_tools', action='list')
assert snapshots['ok'] and any(s['label'] == 'working' for s in snapshots['items']), snapshots
working_id = next(s['id'] for s in snapshots['items'] if s['label'] == 'working')
exported = rpc('config_tools', action='export')
assert json.loads(exported['content'])['format'] == 'nsm-config-1'
preview = rpc('config_tools', action='duplicate', source='testservice', name='service_copy')
assert preview['ok'] and 'service_copy' in preview['diff'], preview
assert not rpc('config_tools', action='commit', token=preview['token'])['ok']
run('uci', 'set', 'nginx_stream_manager.testbackend.health_check=tcp')
run('uci', 'set', 'nginx_stream_manager.main.health_rise=1')
run('uci', 'commit', 'nginx_stream_manager')
assert rpc('monitor_refresh')['ok']
health = rpc('monitor')
assert any(b['id'] == 'testbackend' and b['status'] == 'up' for b in health['backends']), health
assert rpc('diagnostics')['ok']
assert control('reload')['running']
run('/etc/init.d/firewall', 'reload')
assert rpc('status')['running']
assert 'nsm-listener-testservice' in run('nft', 'list', 'chain', 'inet', 'fw4', 'input')
# A committed invalid backend must be rejected while the old runtime survives.
run('uci', 'set', 'nginx_stream_manager.testbackend.port=0')
run('uci', 'commit', 'nginx_stream_manager')
assert not rpc('validate')['ok']
assert control('reload', expected_ok=False)['running']
check_forwarding()
before = config.read_bytes()
upgrade = subprocess.run(['sh', '/src/install.sh'], capture_output=True, timeout=15)
assert upgrade.returncode != 0 and config.read_bytes() == before
run('/etc/init.d/nginx-stream-manager', 'start')  # idempotent, keeps old runtime
assert rpc('status')['running']
assert not control('stop')['running']
assert not Path('/var/run/nginx-stream-manager/state.json').exists()
assert 'table inet nsm' not in run('nft', 'list', 'tables')
assert 'nsm-listener-testservice' not in run('nft', 'list', 'chain', 'inet', 'fw4', 'input')
assert not rpc('control', action='reboot')['ok']
# Restore a validated working snapshot while stopped, through the real RPC transport.
preview = rpc('config_tools', action='restore', source=working_id)
assert preview['ok'], preview
restored = rpc('config_tools', action='commit', token=preview['token'])
assert restored['ok'] and not rpc('status')['running'], restored
assert rpc('validate')['ok']
for source, name in [('testpool', 'renamed_pool'), ('testservice', 'renamed_service')]:
    preview = rpc('config_tools', action='rename', source=source, name=name)
    assert preview['ok'] and name in preview['diff'], preview
    renamed = rpc('config_tools', action='commit', token=preview['token'])
    assert renamed['ok'] and not rpc('status')['running'], renamed
assert run('uci', 'get', 'nginx_stream_manager.testbackend.pool').strip() == 'renamed_pool'
assert run('uci', 'get', 'nginx_stream_manager.renamed_service.pool').strip() == 'renamed_pool'
assert control('start')['running']
check_forwarding()
assert 'nsm-listener-renamed_service' in run('nft', 'list', 'chain', 'inet', 'fw4', 'input')
assert not control('stop')['running']
for server in servers:
    server.shutdown(); server.server_close()
print('Actual UCI/rpcd/procd, traffic history per upstream, TCP/UDP IPv4/IPv6, rejected reload/upgrade, firewall4 permits and cleanup: OK')
