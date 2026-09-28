#!/usr/bin/python3
"""Nginx Stream Manager. No shell evaluation of configuration or RPC input."""
import contextlib
import fcntl
import ipaddress
import hashlib
import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

VERSION = '0.2.0'
RUN = Path('/var/run/nginx-stream-manager')
LOG = Path('/var/log/nginx-stream-manager')
CONFIG = 'nginx_stream_manager'
TABLE = 'nsm'
ROUTE_TABLE = '20100'
PRIORITY = '10100'
MARK = '0x40000000'
MASK = MARK + '/' + MARK
NGINX = '/usr/sbin/nginx'
MODULE = '/usr/lib/nginx/modules/ngx_stream_module.so'
INIT = '/etc/init.d/nginx-stream-manager'

class Invalid(ValueError):
    pass

def command(args, check=True, timeout=8, data=None):
    p = subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode:
        raise Invalid('%s: %s' % (args[0], (p.stderr or p.stdout).strip()[:3000]))
    return p

def integer(value, lo, hi, label):
    if not re.fullmatch(r'[0-9]+', str(value)) or not lo <= int(value) <= hi:
        raise Invalid('%s must be between %d and %d' % (label, lo, hi))
    return int(value)

def flag(obj, key, default='0'):
    v = obj.get(key, default)
    if str(v) not in ('0', '1'):
        raise Invalid('%s must be 0 or 1' % key)
    return str(v) == '1'

def values(obj, key):
    v = obj.get(key, [])
    return v if isinstance(v, list) else [v]

def ident(value):
    if not re.fullmatch(r'[A-Za-z0-9_]{1,48}', str(value)):
        raise Invalid('Invalid section identifier: %s' % value)
    return str(value)

def address(value):
    try:
        if '%' in str(value):
            raise ValueError()
        return ipaddress.ip_address(value)
    except ValueError:
        raise Invalid('Use a literal IPv4 or IPv6 address: %s' % value)

def endpoint(ip, port):
    return '[%s]:%d' % (ip, port) if ip.version == 6 else '%s:%d' % (ip, port)

def parse_uci(text):
    sections = []
    current = None
    for line in text.splitlines():
        parts = shlex.split(line, comments=True)
        if not parts or parts[0] == 'package':
            continue
        if parts[0] == 'config' and len(parts) in (2, 3):
            current = {'.type': parts[1], '.name': parts[2] if len(parts) == 3 else 'cfg%d' % len(sections)}
            sections.append(current)
        elif current is not None and len(parts) == 3 and parts[0] in ('option', 'list'):
            if parts[0] == 'option':
                current[parts[1]] = parts[2]
            else:
                current.setdefault(parts[1], []).append(parts[2])
        else:
            raise Invalid('Unsupported UCI line: ' + line[:100])
    return sections

def read_config():
    # Private empty savedir: ignore uncommitted CLI changes; LuCI must commit first.
    with tempfile.TemporaryDirectory(prefix='nsm-uci-') as td:
        return parse_uci(command(['uci', '-P', td, 'export', CONFIG]).stdout)

def compile_config(sections, module=True):
    names = [s['.name'] for s in sections]
    if len(names) != len(set(names)):
        raise Invalid('Duplicate UCI section names')
    if len(sections) > 512:
        raise Invalid('Maximum 512 configuration sections')
    gs = [s for s in sections if s['.type'] == 'global']
    if len(gs) != 1 or gs[0]['.name'] != 'main':
        raise Invalid('Exactly one global section named main is required')
    g = gs[0]
    enabled = flag(g, 'enabled')
    workers = g.get('workers', 'auto')
    if workers != 'auto':
        workers = str(integer(workers, 1, 64, 'Workers'))
    connections = integer(g.get('connections', 1024), 64, 65535, 'Worker connections')
    nofile = integer(g.get('nofile', 4096), 128, 1048576, 'File descriptor limit')
    if nofile < connections:
        raise Invalid('File descriptor limit must be >= worker connections')
    log_mb = integer(g.get('log_mb', 4), 1, 128, 'Log rotation size')
    pools, backends = {}, {}
    warnings = []
    for s in sections:
        sid = ident(s['.name'])
        if s['.type'] == 'pool':
            method = s.get('method', 'round_robin')
            if method not in ('round_robin', 'least_conn', 'hash_ip'):
                raise Invalid('Invalid pool method')
            pools[sid] = dict(s, method=method)
        elif s['.type'] == 'backend' and flag(s, 'enabled', '1'):
            ip = address(s.get('address', ''))
            if ip.is_unspecified or ip.is_multicast:
                raise Invalid('Backend must be a unicast address')
            b = {'ip': ip, 'port': integer(s.get('port', ''), 1, 65535, 'Backend port'),
                 'weight': integer(s.get('weight', 1), 1, 1000, 'Weight'),
                 'max_fails': integer(s.get('max_fails', 1), 0, 1000, 'Max failures'),
                 'fail_timeout': integer(s.get('fail_timeout', 10), 1, 86400, 'Failure timeout'),
                 'max_conns': integer(s.get('max_conns', 0), 0, 1000000, 'Max connections'),
                 'backup': flag(s, 'backup'), 'down': flag(s, 'down')}
            backends.setdefault(s.get('pool', ''), []).append(b)
    for name, bs in backends.items():
        if name not in pools:
            raise Invalid('Backend refers to missing pool: ' + name)
        if pools[name]['method'] == 'hash_ip' and any(b['backup'] for b in bs):
            raise Invalid('NGINX hash pools do not support backup backends')
    services, bound, returns, families, firewall = [], [], set(), set(), []
    for s in sections:
        if s['.type'] != 'service' or not flag(s, 'enabled', '1'):
            continue
        sid = ident(s['.name'])
        if sid == 'manager':
            raise Invalid('Service name manager is reserved for the manager log')
        pool = s.get('pool', '')
        bs = backends.get(pool, [])
        if pool not in pools or not any(not b['down'] and not b['backup'] for b in bs):
            raise Invalid('Service %s needs a pool with an active primary backend' % sid)
        ip = address(s.get('listen_address', '0.0.0.0'))
        if ip.is_multicast:
            raise Invalid('Multicast listeners are unsupported')
        port = integer(s.get('listen_port', ''), 1, 65535, 'Listen port')
        protocol = s.get('protocol', 'tcp')
        if protocol not in ('tcp', 'udp', 'both'):
            raise Invalid('Invalid protocol')
        protos = ['tcp', 'udp'] if protocol == 'both' else [protocol]
        transparent = flag(s, 'transparent')
        ifaces = values(s, 'return_interface')
        for iface in ifaces:
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', iface) or iface == 'lo':
                raise Invalid('Invalid backend return interface: ' + str(iface))
        if transparent:
            if not flag(s, 'return_route_ack'):
                raise Invalid('Confirm backend return routing for service ' + sid)
            if not ifaces:
                raise Invalid('Transparent service needs backend return interface(s)')
            if ip.is_loopback or any(b['ip'].is_loopback for b in bs):
                raise Invalid('Transparent mode requires non-loopback listeners and backends')
            if any(b['ip'].version != ip.version for b in bs):
                raise Invalid('Transparent listeners and all backends must use the same IP family')
            families.add(ip.version)
            for iface in ifaces:
                for b in bs:
                    for proto in protos:
                        returns.add((iface, ip.version, str(b['ip']), proto, b['port']))
        networks = []
        for net in values(s, 'allow_source'):
            try:
                n = ipaddress.ip_network(net, strict=False)
            except ValueError:
                raise Invalid('Invalid allowed client CIDR: ' + str(net))
            if n.version != ip.version:
                raise Invalid('Client CIDR family must match listener family')
            networks.append(str(n))
        if flag(s, 'open_firewall'):
            ingress = values(s, 'ingress_interface')
            if not ingress:
                raise Invalid('Opening the firewall requires explicit client ingress devices')
            for iface in ingress:
                if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', iface):
                    raise Invalid('Invalid client ingress interface')
            for proto in protos:
                rule = 'iifname { ' + ', '.join('"' + i + '"' for i in ingress) + ' } '
                family = 'ip' if ip.version == 4 else 'ip6'
                rule += 'meta nfproto ' + ('ipv4' if ip.version == 4 else 'ipv6') + ' '
                if not ip.is_unspecified:
                    rule += family + ' daddr ' + str(ip) + ' '
                rule += '%s dport %d ' % (proto, port)
                if networks:
                    rule += family + ' saddr { ' + ', '.join(networks) + ' } '
                firewall.append(rule + 'counter accept comment "nsm-listener-%s"' % sid)
        for proto in protos:
            for prev, pp, pproto in bound:
                if pp == port and pproto == proto and prev.version == ip.version and prev == ip:
                    raise Invalid('Overlapping listener: ' + endpoint(ip, port) + '/' + proto)
            bound.append((ip, port, proto))
        for b in bs:
            if b['port'] == port and (b['ip'] == ip or (ip.is_unspecified and b['ip'].is_loopback)):
                raise Invalid('Listener points back to itself: ' + sid)
        item = dict(s, sid=sid, ip=ip, port=port, protocol=protocol, protos=protos, transparent=transparent,
                    networks=networks, pool=pool)
        for key, default, lo, hi in [('connect_timeout', 5, 1, 300), ('timeout', 600, 1, 604800),
                                     ('next_upstream_tries', 0, 0, 100), ('upload_rate', 0, 0, 2147483647),
                                     ('download_rate', 0, 0, 2147483647), ('udp_requests', 0, 0, 1000000)]:
            item[key] = integer(s.get(key, default), lo, hi, key)
        response = s.get('udp_responses', '')
        item['udp_responses'] = None if response == '' else integer(response, 0, 1000000, 'UDP responses')
        for key in ('access_log', 'keepalive', 'half_close', 'proxy_protocol'):
            item[key] = flag(s, key)
        if item['proxy_protocol'] and 'udp' in protos:
            raise Invalid('PROXY protocol output is supported for TCP services only')
        if not networks:
            warnings.append('%s: all source IPs allowed by Nginx; firewall still controls access.' % sid)
        services.append(item)
    if len(services) > 128:
        raise Invalid('Maximum 128 enabled services')
    if enabled and not services:
        raise Invalid('Enable at least one service before enabling the manager')
    lines = ['# Generated by nginx-stream-manager; edits are overwritten.']
    if module:
        lines.append('load_module %s;' % MODULE)
    lines += ['user nobody nogroup;', 'worker_processes %s;' % workers,
              'worker_rlimit_nofile %d;' % nofile, 'worker_shutdown_timeout 15s;',
              'pid %s/nginx.pid;' % RUN, 'daemon off;',
              'error_log %s/manager.error.log warn;' % LOG,
              'events { worker_connections %d; }' % connections, 'stream {',
              "  log_format nsm '$remote_addr [$time_local] $protocol $status $bytes_sent $bytes_received $session_time upstream=$upstream_addr';"]
    used = {s['pool'] for s in services}
    for name in sorted(used):
        lines += ['  upstream pool_%s {' % name, '    zone pool_%s 64k;' % name]
        method = pools[name]['method']
        if method == 'least_conn':
            lines.append('    least_conn;')
        elif method == 'hash_ip':
            lines.append('    hash $remote_addr consistent;')
        for b in backends[name]:
            opts = 'weight=%d max_fails=%d fail_timeout=%ds max_conns=%d' % (b['weight'], b['max_fails'], b['fail_timeout'], b['max_conns'])
            opts += ' backup' if b['backup'] else ''
            opts += ' down' if b['down'] else ''
            lines.append('    server %s %s;' % (endpoint(b['ip'], b['port']), opts))
        lines.append('  }')
    for s in services:
        for proto in s['protos']:
            lines += ['  server {', '    listen %s%s%s;' % (endpoint(s['ip'], s['port']),
                      (' udp' if proto == 'udp' else '') + ' reuseport' + (' bind' if not s['ip'].is_unspecified else ''), ' ipv6only=on' if s['ip'].version == 6 else ''),
                      '    proxy_pass pool_%s;' % s['pool'],
                      '    proxy_connect_timeout %ds;' % s['connect_timeout'], '    proxy_timeout %ds;' % s['timeout'],
                      '    proxy_next_upstream on;', '    proxy_next_upstream_tries %d;' % s['next_upstream_tries'],
                      '    proxy_upload_rate %d;' % s['upload_rate'], '    proxy_download_rate %d;' % s['download_rate'],
                      '    error_log %s/%s.error.log warn;' % (LOG, s['sid'])]
            lines.append('    access_log %s/%s.access.log nsm;' % (LOG, s['sid']) if s['access_log'] else '    access_log off;')
            if s['transparent']:
                lines.append('    proxy_bind $remote_addr transparent;')
            if proto == 'udp':
                lines.append('    proxy_requests %d;' % s['udp_requests'])
                if s['udp_responses'] is not None:
                    lines.append('    proxy_responses %d;' % s['udp_responses'])
            else:
                lines += ['    proxy_socket_keepalive %s;' % ('on' if s['keepalive'] else 'off'),
                          '    proxy_half_close %s;' % ('on' if s['half_close'] else 'off')]
                if s['proxy_protocol']:
                    lines.append('    proxy_protocol on;')
            lines += ['    allow %s;' % n for n in s['networks']]
            if s['networks']:
                lines.append('    deny all;')
            lines.append('  }')
    lines.append('}')
    nft = ['table inet nsm {', '  chain return_path {', '    type filter hook prerouting priority -151; policy accept;']
    for iface, fam, ip, proto, port in sorted(returns):
        nft.append('    iifname "%s" %s saddr %s %s sport %d socket transparent 1 meta mark set meta mark | %s counter accept' %
                   (iface, 'ip' if fam == 4 else 'ip6', ip, proto, port, MARK))
    nft += ['  }', '}']
    return {'enabled': enabled, 'nginx': '\n'.join(lines) + '\n', 'nft': '\n'.join(nft) + '\n',
            'fw4': '\n'.join(firewall) + '\n',
            'families': sorted(families), 'warnings': warnings, 'log_mb': log_mb,
            'services': [{'id': s['sid'], 'listen': endpoint(s['ip'], s['port']), 'protocol': s['protocol'],
                          'pool': s['pool'], 'transparent': s['transparent']} for s in services]}

def atomic(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

@contextlib.contextmanager
def locked():
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUN / 'lock').open('w') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Invalid('Another operation is running; try again')
        yield

def state():
    try:
        return json.loads((RUN / 'state.json').read_text())
    except FileNotFoundError:
        return None

def pid():
    try:
        p = int((RUN / 'nginx.pid').read_text())
        args = Path('/proc/%d/cmdline' % p).read_bytes()
        if p > 1 and b'nginx' in args and str(RUN / 'nginx.conf').encode() in args:
            os.kill(p, 0)
            return p
    except (OSError, ValueError):
        pass
    return None

def nft_batch(config):
    exists = command(['nft', 'list', 'table', 'inet', TABLE], check=False).returncode == 0
    return ('delete table inet nsm\n' if exists else '') + config

def preflight(compiled):
    LOG.mkdir(parents=True, exist_ok=True, mode=0o750)
    old = state()
    if not old and command(['nft', 'list', 'table', 'inet', TABLE], check=False).returncode == 0:
        raise Invalid('nftables table inet nsm already exists without ownership state')
    for fam in compiled['families']:
        for service in read_config():
            if service['.type'] == 'service' and flag(service, 'enabled', '1') and flag(service, 'transparent'):
                for iface in values(service, 'return_interface'):
                    if not (Path('/sys/class/net') / iface).exists():
                        raise Invalid('Return interface does not exist: ' + iface)
        if not old or fam not in old['families']:
            rules = command(['ip', '-%d' % fam, 'rule', 'show']).stdout
            routes = command(['ip', '-%d' % fam, 'route', 'show', 'table', ROUTE_TABLE], check=False).stdout
            if re.search(r'^%s:' % PRIORITY, rules, re.M) or routes.strip():
                raise Invalid('Routing priority %s or table %s is already in use (IPv%d)' % (PRIORITY, ROUTE_TABLE, fam))
    test = RUN / 'candidate.conf'
    atomic(test, compiled['nginx'])
    command([NGINX, '-t', '-p', str(RUN) + '/', '-c', str(test)])
    command(['nft', '-c', '-f', '-'], data=nft_batch(compiled['nft']))
    check_rules = 'table inet nsm_check_%d { chain input {\n%s\n} }\n' % (os.getpid(), compiled['fw4'])
    command(['nft', '-c', '-f', '-'], data=check_rules)
    return compiled

def prepare():
    sections = read_config()
    c = compile_config(sections, module=Path(MODULE).exists())
    if not Path(MODULE).exists():
        version = command([NGINX, '-V']).stderr
        if not re.search(r'--with-stream(?:\s|$)', version):
            raise Invalid('Nginx stream module missing; install nginx-mod-stream')
    return preflight(c)

def route_add(fam):
    base = ['ip', '-%d' % fam]
    command(base + ['route', 'replace', 'local', '0.0.0.0/0' if fam == 4 else '::/0', 'dev', 'lo', 'table', ROUTE_TABLE])
    rules = command(base + ['rule', 'show']).stdout
    ours = [line for line in rules.splitlines() if line.startswith(PRIORITY + ':')]
    if ours:
        if len(ours) != 1 or ('fwmark ' + MASK) not in ours[0] or ('lookup ' + ROUTE_TABLE) not in ours[0]:
            raise Invalid('Routing priority collision: ' + ours[0])
    else:
        command(base + ['rule', 'add', 'priority', PRIORITY, 'fwmark', MASK, 'lookup', ROUTE_TABLE])

def route_del(fam):
    base = ['ip', '-%d' % fam]
    command(base + ['rule', 'del', 'priority', PRIORITY, 'fwmark', MASK, 'lookup', ROUTE_TABLE], check=False)
    command(base + ['route', 'del', 'local', '0.0.0.0/0' if fam == 4 else '::/0', 'dev', 'lo', 'table', ROUTE_TABLE], check=False)

def firewall_apply(text):
    path = RUN / 'fw4-input.nft'
    previous = path.read_text() if path.exists() else ''
    if text.strip() == previous.strip():
        return
    atomic(path, text)
    try:
        command(['fw4', 'check'])
        command(['/etc/init.d/firewall', 'reload'], timeout=10)
    except Exception:
        atomic(path, previous)
        raise

def network(c, old=None):
    for fam in c['families']:
        route_add(fam)
    command(['nft', '-f', '-'], data=nft_batch(c['nft']))
    firewall_apply(c.get('fw4', ''))
    for fam in (old or {}).get('families', []):
        if fam not in c['families']:
            route_del(fam)

def workers(master):
    try:
        children = Path('/proc/%d/task/%d/children' % (master, master)).read_text().split()
        result = set()
        for child in children:
            try:
                if b'nginx: worker process' in Path('/proc/%s/cmdline' % child).read_bytes():
                    result.add(int(child))
            except OSError:
                pass
        return result
    except OSError:
        # Some OpenWrt kernels omit /proc/.../children.
        result = set()
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[1]) == master and b'nginx: worker process' in (proc / 'cmdline').read_bytes():
                    result.add(int(proc.name))
            except (OSError, ValueError, IndexError):
                pass
        return result

def confirm_reload(master, previous_workers):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if pid() != master:
            raise Invalid('Nginx master exited during reload')
        if workers(master) - previous_workers:
            return
        time.sleep(0.1)
    detail = tail_file(LOG / 'manager.error.log', 8)['text']
    raise Invalid('Nginx did not start a new worker generation after reload. ' + detail)

def apply(reload=False):
    with locked():
        c = prepare()
        if not c['enabled']:
            raise Invalid('Manager is disabled; enable it in configuration first')
        old = state()
        previous = (RUN / 'nginx.conf').read_text() if (RUN / 'nginx.conf').exists() else None
        oldpid = pid()
        if reload and not oldpid:
            raise Invalid('Manager is not running; use Start')
        before = workers(oldpid) if reload else set()
        try:
            network(c, old)
            atomic(RUN / 'nginx.conf', c['nginx'])
            if reload:
                os.kill(oldpid, signal.SIGHUP)
                confirm_reload(oldpid, before)
            atomic(RUN / 'state.json', json.dumps(c))
        except Exception as error:
            # Restore the configuration before attempting any recovery reload.
            if previous is not None:
                atomic(RUN / 'nginx.conf', previous)
            try:
                if old:
                    network(old, c)
                    atomic(RUN / 'state.json', json.dumps(old))
                else:
                    firewall_apply('')
                    command(['nft', 'delete', 'table', 'inet', TABLE], check=False)
                    for fam in c['families']:
                        route_del(fam)
                    (RUN / 'state.json').unlink(missing_ok=True)
                if reload and oldpid and previous is not None and pid() == oldpid:
                    os.kill(oldpid, signal.SIGHUP)
            except Exception as recovery:
                raise Invalid('%s; recovery also failed: %s. Inspect routing and logs.' % (error, recovery)) from error
            raise
        return {'ok': True, 'message': 'Configuration applied; new Nginx workers observed.' if reload else 'Configuration prepared.',
                'warnings': c['warnings'][:20]}

def cleanup():
    with locked():
        old = state()
        if old:
            firewall_apply('')
            command(['nft', 'delete', 'table', 'inet', TABLE], check=False)
            for fam in old['families']:
                route_del(fam)
            (RUN / 'state.json').unlink(missing_ok=True)
    return {'ok': True}

def restore_network():
    with locked():
        old = state()
        if old and pid():
            network(old, old)
    return {'ok': True}

def tail_file(path, lines=500, limit=8000):
    # Seek backwards: never read the complete file just to show its tail.
    try:
        with Path(path).open('rb') as f:
            f.seek(0, 2)
            pos, chunks, count, size = f.tell(), [], 0, 0
            while pos > 0 and count <= lines and size < limit:
                take = min(4096, pos, limit - size)
                pos -= take
                f.seek(pos)
                block = f.read(take)
                chunks.append(block)
                count += block.count(b'\n')
                size += take
            data = b''.join(reversed(chunks))
            if pos and count <= lines:
                data = data.split(b'\n', 1)[-1] if b'\n' in data else data
            text = b'\n'.join(data.splitlines()[-lines:]).decode('utf-8', 'replace')
            return {'text': text, 'byte_limited': pos > 0 and size >= limit}
    except FileNotFoundError:
        return {'text': '', 'byte_limited': False}

def logs(args):
    name = args.get('service', 'manager')
    if name != 'manager':
        ids = {s['.name'] for s in read_config() if s['.type'] == 'service'}
        if name not in ids:
            raise Invalid('Unknown service')
        ident(name)
    kind = args.get('kind', 'error')
    if kind not in ('error', 'access') or (name == 'manager' and kind != 'error'):
        raise Invalid('Invalid log type')
    lines = integer(args.get('lines', 500), 1, 1000, 'Lines')
    return dict(ok=True, **tail_file(LOG / (name + '.' + kind + '.log'), lines))

def rotate():
    with locked():
        old = state()
        if not old:
            return {'ok': True}
        changed = False
        for p in LOG.glob('*.log'):
            if p.is_file() and p.stat().st_size > old['log_mb'] * 1024 * 1024:
                os.replace(p, str(p) + '.1')
                changed = True
        p = pid()
        if changed and p:
            os.kill(p, signal.SIGUSR1)
    return {'ok': True}

def status():
    old = state()
    warnings = []
    if old and old['families']:
        fw = command(['nft', 'list', 'chain', 'inet', 'fw4', 'input'], check=False).stdout
        if 'nsm-transparent-return' not in fw:
            warnings.append('Firewall4 return-socket rule missing; reload firewall.')
        for s in read_config():
            if s['.type'] == 'service' and flag(s, 'enabled', '1') and flag(s, 'transparent'):
                for iface in values(s, 'return_interface'):
                    for scope in ('all', iface):
                        p = Path('/proc/sys/net/ipv4/conf') / scope / 'rp_filter'
                        if p.exists() and p.read_text().strip() == '1':
                            warnings.append('Strict rp_filter on %s may reject asymmetric returns.' % scope)
    return {'ok': True, 'version': VERSION, 'running': bool(pid()), 'pid': pid(),
            'boot_enabled': command([INIT, 'enabled'], check=False).returncode == 0,
            'services': (old or {}).get('services', []), 'warnings': warnings,
            'binaries': {x: bool(shutil.which(x)) for x in ('nginx', 'nft', 'ip', 'uci')},
            'stream_module': Path(MODULE).exists(),
            'last_error': (RUN / 'last-error').read_text() if (RUN / 'last-error').exists() else ''}

SOURCE_CONFIG = Path('/etc/nginx/nginx.conf')
APP_CONFIG = Path('/etc/config/nginx_stream_manager')
IMPORT_BACKUPS = Path('/etc/nginx-stream-manager/import-backups')

def import_plan():
    from nsm_importer import plan
    with SOURCE_CONFIG.open('rb') as f:
        raw = f.read(262145)
    if len(raw) > 262144:
        raise Invalid('nginx.conf exceeds the 256 KiB import limit')
    current_bytes = APP_CONFIG.read_bytes()
    current = read_config()
    result = plan(raw.decode('utf-8'), current)
    if pid() or any(s['.type'] == 'global' and flag(s, 'enabled') for s in current):
        result['blockers'].append('Stop and disable the manager before importing. Existing Nginx may keep running.')
    if command(['uci', 'changes', CONFIG]).stdout.strip():
        result['blockers'].append('Uncommitted UCI changes exist; save or discard them first.')
    # Validate even though imported services will be stored disabled.
    candidate = copy.deepcopy(current + result['sections'])
    for section in candidate:
        if section['.type'] == 'service':
            section['enabled'] = '1' if section['.name'] in {x['id'] for x in result['services']} else '0'
            if flag(section, 'transparent'):
                section['return_interface'] = ['nsm_check']
                section['return_route_ack'] = '1'
    try:
        compile_config(candidate)
    except (Invalid, ValueError) as e:
        result['blockers'].append('Generated configuration: ' + str(e))
    result['token'] = hashlib.sha256(raw + b'\0' + current_bytes).hexdigest()
    return result, current, current_bytes

def import_preview():
    with locked():
        result, _, _ = import_plan()
        public = {k: v for k, v in result.items() if k != 'sections'}
        public.update(ok=True, can_import=not result['blockers'])
        if len(json.dumps(public).encode()) > 48000:
            raise Invalid('Import preview exceeds the response limit; split the source configuration')
        return public

def import_commit(token):
    from nsm_importer import uci_text
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token):
        raise Invalid('A fresh preview token is required')
    with locked():
        result, current, previous = import_plan()
        if token != result['token']:
            raise Invalid('Source or app configuration changed after preview; preview again')
        if result['blockers']:
            raise Invalid('Import blocked: ' + '; '.join(result['blockers']))
        candidate = current + result['sections']
        serialized = uci_text(candidate)
        if parse_uci(serialized) != candidate:
            raise Invalid('UCI serialization verification failed')
        IMPORT_BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        if len(list(IMPORT_BACKUPS.glob('*.uci'))) >= 20:
            raise Invalid('20 import backups retained; archive old backups before importing again')
        backup = IMPORT_BACKUPS / ('before-' + time.strftime('%Y%m%d-%H%M%S') + '-' + token[:8] + '.uci')
        atomic(backup, previous.decode('utf-8'))
        # Check once more just before replacement; do not overwrite intervening edits.
        fresh, _, _ = import_plan()
        if fresh['token'] != token or fresh['blockers']:
            raise Invalid('Configuration or manager state changed while importing; preview again')
        atomic(APP_CONFIG, serialized)
        return {'ok': True, 'message': 'Imported %d disabled services. Existing Nginx was not modified or reloaded.' % len(result['services']),
                'backup': str(backup), 'services_imported': len(result['services'])}

def rpc(method, args):
    if method == 'import_preview':
        return import_preview()
    if method == 'import_commit':
        return import_commit(args.get('token'))
    if method == 'status':
        return status()
    if method == 'logs':
        return logs(args)
    if method in ('preview', 'validate'):
        with locked():
            c = prepare() if method == 'validate' else compile_config(read_config(), Path(MODULE).exists())
            if method == 'validate':
                return {'ok': True, 'message': 'Nginx and nftables syntax checked.', 'warnings': c['warnings'][:20]}
            # Keep the ubus response bounded even with many services.
            return {'ok': True, 'nginx': c['nginx'][:18000], 'nft': c['nft'][:8000], 'fw4': c['fw4'][:8000],
                    'truncated': len(c['nginx']) > 18000 or len(c['nft']) > 8000 or len(c['fw4']) > 8000}
    if method == 'control':
        action = args.get('action')
        if action not in ('start', 'stop', 'reload', 'enable', 'disable'):
            raise Invalid('Unsupported control action')
        if action in ('start', 'reload'):
            with locked():
                prepare()
        result = command([INIT, action], timeout=12)
        return {'ok': True, 'message': result.stdout.strip() or 'Command completed: ' + action}
    raise Invalid('Unknown method')

SIGNATURES = {'import_preview': {}, 'import_commit': {'token': ''}, 'status': {}, 'preview': {}, 'validate': {},
              'logs': {'service': 'manager', 'kind': 'error', 'lines': 500},
              'control': {'action': 'status'}}

def main():
    try:
        op = sys.argv[1] if len(sys.argv) > 1 else 'status'
        if op == 'list':
            result = SIGNATURES
        elif op == 'call':
            args = json.loads(sys.stdin.read(8192) or '{}')
            result = rpc(sys.argv[2], args)
        elif op == 'prepare':
            result = apply()
        elif op == 'reload':
            result = apply(reload=True)
        elif op == 'cleanup':
            result = cleanup()
        elif op == 'restore':
            result = restore_network()
        elif op == 'rotate':
            result = rotate()
        else:
            result = rpc(op, {})
        if op in ('prepare', 'reload'):
            (RUN / 'last-error').unlink(missing_ok=True)
        print(json.dumps(result))
        return 0
    except Exception as e:
        if len(sys.argv) > 1 and sys.argv[1] in ('prepare', 'reload'):
            atomic(RUN / 'last-error', str(e))
        print(json.dumps({'ok': False, 'error': str(e)}))
        # rpcd must receive the JSON even when the operation failed.
        return 0 if len(sys.argv) > 1 and sys.argv[1] == 'call' else 1

if __name__ == '__main__':
    sys.exit(main())
