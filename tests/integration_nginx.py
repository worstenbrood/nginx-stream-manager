"""Real Linux/Nginx/nft tests in a disposable container; never run on a router.

docker build -t nsm-test -f tests/Dockerfile .
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE -v "$PWD:/src:ro" nsm-test
"""
import copy
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/nginx-stream-manager/files'))
import nsm
import nsm_importer


class RealRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = nsm.RUN, nsm.LOG
        nsm.RUN, nsm.LOG = self.root, self.root / 'logs'
        nsm.LOG.mkdir()
        self.process = None
        self.sockets = []
        self.sections = [{'.type': 'global', '.name': 'main', 'enabled': '1', 'workers': '2'}]

    def tearDown(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process.stderr.close()
        for sock in self.sockets:
            sock.close()
        nsm.RUN, nsm.LOG = self.original
        self.temp.cleanup()

    def backend(self, family, tag):
        ip = '127.0.0.1' if family == 4 else '::1'
        af = socket.AF_INET if family == 4 else socket.AF_INET6
        tcp = socket.socket(af, socket.SOCK_STREAM)
        tcp.bind((ip, 0)); tcp.listen()
        port = tcp.getsockname()[1]
        udp = socket.socket(af, socket.SOCK_DGRAM)
        udp.bind((ip, port))
        self.sockets += [tcp, udp]
        def serve(sock, datagram):
            while True:
                try:
                    if datagram:
                        _, addr = sock.recvfrom(1024); sock.sendto(tag, addr)
                    else:
                        client, _ = sock.accept()
                        with client:
                            client.recv(1024); client.sendall(tag)
                except OSError:
                    return
        for sock, datagram in ((tcp, False), (udp, True)):
            threading.Thread(target=serve, args=(sock, datagram), daemon=True).start()
        return ip, port

    def service(self, name, listen, port, backend):
        self.sections.extend([
            {'.type': 'pool', '.name': name + '_pool'},
            {'.type': 'backend', '.name': name + '_backend', 'pool': name + '_pool',
             'address': backend[0], 'port': str(backend[1])},
            {'.type': 'service', '.name': name, 'pool': name + '_pool', 'listen_address': listen,
             'listen_port': str(port), 'protocol': 'both', 'timeout': '2', 'udp_responses': '1'}])

    def start(self):
        compiled = nsm.compile_config(self.sections)
        nsm.preflight(compiled)
        nsm.atomic(nsm.RUN / 'nginx.conf', compiled['nginx'])
        nsm.command([nsm.NGINX, '-t', '-p', str(nsm.RUN) + '/', '-c', str(nsm.RUN / 'nginx.conf')])
        self.process = subprocess.Popen([nsm.NGINX, '-p', str(nsm.RUN) + '/', '-c', str(nsm.RUN / 'nginx.conf')],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        nsm.lifecycle(True)
        return compiled

    def request(self, ip, port, kind):
        af = socket.AF_INET6 if ':' in ip else socket.AF_INET
        with socket.socket(af, kind) as client:
            client.settimeout(3)
            client.connect((ip, port)); client.send(b'probe')
            return client.recv(1024)

    def test_loopback_wildcard_tcp_udp_ipv4_ipv6_and_reload(self):
        for family, loop, wild in ((4, '127.0.0.1', '0.0.0.0'), (6, '::1', '::')):
            self.service('loop%d' % family, loop, 19053, self.backend(family, b'loop'))
            self.service('wild%d' % family, wild, 19053, self.backend(family, b'wild'))
        compiled = self.start()
        nsm.check_listeners(compiled)  # Our own sockets must be permitted.
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            self.assertEqual(self.request('127.0.0.1', 19053, kind), b'loop')
            self.assertEqual(self.request('127.0.0.2', 19053, kind), b'wild')
            self.assertEqual(self.request('::1', 19053, kind), b'loop')
        nsm.command(['ip', '-6', 'addr', 'add', 'fd00:1234::1/128', 'dev', 'lo', 'nodad'])
        try:
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                self.assertEqual(self.request('fd00:1234::1', 19053, kind), b'wild')
        finally:
            nsm.command(['ip', '-6', 'addr', 'del', 'fd00:1234::1/128', 'dev', 'lo'])
        master = nsm.pid(); before = nsm.workers(master)
        os.kill(master, signal.SIGHUP)
        nsm.confirm_reload(master, before)
        self.assertEqual(self.request('127.0.0.1', 19053, socket.SOCK_DGRAM), b'loop')

    def test_external_reuseport_listener_rejected(self):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with self.subTest(kind=kind), socket.socket(socket.AF_INET, kind) as other:
                other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                other.bind(('0.0.0.0', 19054))
                if kind == socket.SOCK_STREAM: other.listen()
                config = {'services': [{'listen': '127.0.0.1:19054', 'protocol': 'both'}]}
                with self.assertRaisesRegex(nsm.Invalid, 'external socket'):
                    nsm.check_listeners(config)

    def test_rejected_real_reload_then_confirmed_recovery(self):
        self.service('old', '127.0.0.1', 19058, self.backend(4, b'old'))
        compiled = self.start()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(('127.0.0.1', 19059)); blocker.listen()
            self.service('new', '127.0.0.1', 19059, self.backend(4, b'new'))
            candidate = nsm.compile_config(self.sections)
            nsm.atomic(nsm.RUN / 'nginx.conf', candidate['nginx'])
            # Syntax passes despite an occupied new port. Simulates a bind race
            # after our /proc preflight; no subprocess/network mocks are used.
            nsm.command([nsm.NGINX, '-t', '-p', str(nsm.RUN) + '/', '-c', str(nsm.RUN / 'nginx.conf')])
            master = nsm.pid(); before = nsm.workers(master)
            os.kill(master, signal.SIGHUP)
            with self.assertRaisesRegex(nsm.Invalid, 'did not start a new worker'):
                nsm.confirm_reload(master, before)
            self.assertEqual(self.request('127.0.0.1', 19058, socket.SOCK_STREAM), b'old')
            nsm.atomic(nsm.RUN / 'nginx.conf', compiled['nginx'])
            before = nsm.workers(master)
            os.kill(master, signal.SIGHUP)
            nsm.confirm_reload(master, before)
            self.assertEqual(self.request('127.0.0.1', 19058, socket.SOCK_STREAM), b'old')

    def test_detached_control_lock_and_error_with_stub_init(self):
        # Real child/parent processes and flock, deliberately stubbed init.
        # This checks the job protocol, not OpenWrt procd integration.
        init = Path(nsm.INIT)
        init.parent.mkdir(exist_ok=True)
        init.write_text('#!/bin/sh\nsleep 1\necho test-init-failure >&2\nexit 1\n')
        init.chmod(0o755)
        operation = Path('/var/run/nginx-stream-manager/operation.json')
        argv = [sys.executable, nsm.__file__, 'call', 'control']
        try:
            first = json.loads(nsm.command(argv, data='{"action":"enable"}').stdout)
            self.assertTrue(first['ok'])
            second = json.loads(nsm.command(argv, data='{"action":"enable"}').stdout)
            self.assertFalse(second['ok'])
            self.assertIn('Another service action', second['error'])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = json.loads(operation.read_text())
                if not result['pending']: break
                time.sleep(0.05)
            self.assertFalse(result['pending'])
            self.assertFalse(result['ok'])
            self.assertIn('test-init-failure', result['error'])
        finally:
            init.unlink(missing_ok=True)

    def test_real_uci_ignores_deltas_and_import_roundtrip(self):
        original = nsm.APP_CONFIG, nsm.SOURCE_CONFIG, nsm.IMPORT_BACKUPS
        try:
            nsm.APP_CONFIG = Path('/etc/config/nginx_stream_manager')
            nsm.APP_CONFIG.parent.mkdir(exist_ok=True)
            nsm.SOURCE_CONFIG = self.root / 'source.conf'
            nsm.IMPORT_BACKUPS = self.root / 'backups'
            current = [{'.type': 'global', '.name': 'main', 'enabled': '0'},
                       {'.type': 'pool', '.name': 'old', 'name': "John's pool", 'method': 'round_robin'}]
            raw = nsm_importer.uci_text(current).encode()
            nsm.APP_CONFIG.write_bytes(raw)
            nsm.command(['uci', 'set', 'nginx_stream_manager.main.enabled=1'])
            self.assertEqual(nsm.read_config(), current)
            self.assertEqual(nsm.command(['uci', 'get', 'nginx_stream_manager.main.enabled']).stdout.strip(), '1')
            nsm.SOURCE_CONFIG.write_bytes((Path('tests/fixtures/existing-nginx.conf')).read_bytes())
            self.assertFalse(nsm.import_preview()['can_import'])
            nsm.command(['uci', 'revert', 'nginx_stream_manager'])
            preview = nsm.import_preview()
            self.assertTrue(preview['can_import'], preview['blockers'])
            result = nsm.import_commit(preview['token'])
            self.assertEqual(result['services_imported'], 5)
            self.assertEqual(Path(result['backup']).read_bytes(), raw)
            imported = nsm.read_config()
            self.assertEqual(imported[:2], current)
            services = [s for s in imported if s['.type'] == 'service']
            self.assertEqual(len(services), 5)
            for service in services:
                self.assertEqual(nsm.command(['uci', 'get', 'nginx_stream_manager.' + service['.name'] + '.enabled']).stdout.strip(), '0')
            self.assertFalse(nsm.import_preview()['can_import'])
        finally:
            nsm.command(['uci', 'revert', 'nginx_stream_manager'], check=False)
            nsm.APP_CONFIG.unlink(missing_ok=True)
            nsm.APP_CONFIG, nsm.SOURCE_CONFIG, nsm.IMPORT_BACKUPS = original

    def test_real_nft_and_route_add_delete_preserve_other_routes(self):
        self.service('transparent', '0.0.0.0', 19055, ('192.0.2.2', 9000))
        self.sections[-1].update(transparent='1', return_interface=['eth0'], return_route_ack='1')
        c = nsm.compile_config(self.sections)
        nsm.command(['nft', '-c', '-f', '-'], data=c['nft'])
        nsm.command(['nft', '-f', '-'], data=c['nft'])
        self.assertIn('socket transparent 1', nsm.command(['nft', 'list', 'table', 'inet', 'nsm']).stdout)
        for family in (4, 6):
            nsm.route_add(family); nsm.route_add(family)
            self.assertTrue(all(nsm.own_route(r) for r in nsm.routing_entries(family)))
            nsm.route_del(family)
            self.assertFalse(nsm.routing_entries(family))
        nsm.command(['ip', 'route', 'add', '192.0.2.0/24', 'dev', 'lo', 'table', nsm.ROUTE_TABLE])
        with self.assertRaisesRegex(nsm.Invalid, 'collision'):
            nsm.route_add(4)
        nsm.route_del(4)
        self.assertEqual(nsm.routing_entries(4)[0]['dst'], '192.0.2.0/24')
        nsm.command(['ip', 'route', 'del', '192.0.2.0/24', 'dev', 'lo', 'table', nsm.ROUTE_TABLE])
        nsm.command(['nft', 'delete', 'table', 'inet', 'nsm'])

    @unittest.skipUnless(os.environ.get('NSM_NETNS') == '1', 'requires disposable container with SYS_ADMIN and NET_ADMIN')
    def test_transparent_tcp_udp_ipv4_ipv6_real_return_path(self):
        namespaces, backend = [], None
        try:
            for ns, router_dev, subnet in (('nsm-client', 'nsmc', 231), ('nsm-backend', 'nsmb', 232)):
                nsm.command(['ip', 'netns', 'add', ns]); namespaces.append(ns)
                nsm.command(['ip', 'link', 'add', router_dev, 'type', 'veth', 'peer', 'name', 'peer0'])
                nsm.command(['ip', 'link', 'set', 'peer0', 'netns', ns])
                nsm.command(['ip', 'link', 'set', router_dev, 'up'])
                nsm.command(['ip', 'addr', 'add', '10.%d.0.1/24' % subnet, 'dev', router_dev])
                nsm.command(['ip', '-6', 'addr', 'add', 'fd00:%d::1/64' % subnet, 'dev', router_dev, 'nodad'])
                prefix = ['ip', 'netns', 'exec', ns]
                for dev in ('lo', 'peer0'): nsm.command(prefix + ['ip', 'link', 'set', dev, 'up'])
                nsm.command(prefix + ['ip', 'addr', 'add', '10.%d.0.2/24' % subnet, 'dev', 'peer0'])
                nsm.command(prefix + ['ip', '-6', 'addr', 'add', 'fd00:%d::2/64' % subnet, 'dev', 'peer0', 'nodad'])
                nsm.command(prefix + ['ip', 'route', 'add', 'default', 'via', '10.%d.0.1' % subnet])
                nsm.command(prefix + ['ip', '-6', 'route', 'add', 'default', 'via', 'fd00:%d::1' % subnet])
            backend = subprocess.Popen(['ip', 'netns', 'exec', 'nsm-backend', 'python3', 'tests/network_peer.py', 'server'],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(backend.stdout.readline().strip(), 'ready')
            for family, ip, target in ((4, '10.231.0.1', '10.232.0.2'), (6, 'fd00:231::1', 'fd00:232::2')):
                self.service('source%d' % family, ip, 19056, (target, 19057))
                self.sections[-1].update(transparent='1', return_interface=['nsmb'], return_route_ack='1')
            compiled = nsm.compile_config(self.sections)
            self.start()
            for family in compiled['families']: nsm.route_add(family)
            # Separate INPUT filter verifies that return packets really carry our bit.
            source = (Path(__file__).resolve().parents[1] / 'packages/nginx-stream-manager/files/return.nft').read_text().split('# Runtime')[0]
            input_filter = 'table inet test_input {\n chain input { type filter hook input priority 0; policy accept;\n' + source + '\niifname "nsmb" meta l4proto { tcp, udp } counter drop\n }\n}\n'
            nsm.command(['nft', '-f', '-'], data=compiled['nft'] + input_filter)
            for target, expected in (('10.231.0.1', '10.231.0.2'), ('fd00:231::1', 'fd00:231::2')):
                for proto in ('tcp', 'udp'):
                    with self.subTest(target=target, proto=proto):
                        observed = nsm.command(['ip', 'netns', 'exec', 'nsm-client', 'python3', 'tests/network_peer.py', 'client', target, proto]).stdout.strip()
                        self.assertEqual(observed, expected)
            output = nsm.command(['nft', 'list', 'table', 'inet', 'nsm']).stdout
            self.assertNotIn('counter packets 0 bytes 0', output)
        finally:
            if self.process:
                self.process.terminate(); self.process.wait(timeout=10)
                self.process.stderr.close(); self.process = None
            if backend:
                backend.terminate(); backend.wait(timeout=10)
                backend.stdout.close(); backend.stderr.close()
            for family in (4, 6): nsm.route_del(family)
            for table in ('nsm', 'test_input'):
                nsm.command(['nft', 'delete', 'table', 'inet', table], check=False)
            for ns in namespaces: nsm.command(['ip', 'netns', 'del', ns], check=False)


if __name__ == '__main__':
    if not Path('/.dockerenv').exists():
        sys.exit('Run only in the disposable Docker test container.')
    unittest.main(verbosity=2)
