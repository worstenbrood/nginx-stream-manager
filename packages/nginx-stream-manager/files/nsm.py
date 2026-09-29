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
import socket
import subprocess
import sys
import tempfile
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nsm_ops

VERSION = '0.6.0'
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

def ops():
    return nsm_ops.Operations(types.SimpleNamespace(**globals()))

def working_snapshot():
    try:
        raw = (state() or {}).get('source_config')
        if raw is not None:
            ops().snapshot('working', raw.encode())
            (RUN / 'snapshot-error').unlink(missing_ok=True)
    except Exception as error:
        atomic(RUN / 'snapshot-error', str(error)[:500])

def monitor_tick():
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'ok': True, 'message': 'Monitoring is already running.'}
        return ops().tick()

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

def read_config(raw=None):
    # -P adds a delta path; it does NOT remove /tmp/.uci. A unique package
    # name in a private confdir prevents loading deltas for the real package.
    raw = bounded_read(APP_CONFIG, 1048576) if raw is None else raw
    with tempfile.TemporaryDirectory(prefix='nsm-uci-') as td:
        name = Path(td).name.replace('-', '_')
        (Path(td) / name).write_bytes(raw)
        return parse_uci(command(['uci', '-c', td, '-P', td, 'export', name]).stdout)

def bounded_read(path, limit):
    with Path(path).open('rb') as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise Invalid('%s exceeds the %d byte limit' % (path, limit))
    return data


def hostname(value):
    text = str(value).lower().rstrip('.')
    if len(text) > 253 or not text or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in text.split('.')):
        raise Invalid('Invalid DNS name: ' + str(value))
    return text


def file_path(value):
    # Literal paths only: no variables, whitespace, quotes or config delimiters.
    if not re.fullmatch(r'/[A-Za-z0-9_./:@+-]{1,240}', str(value)) or '..' in str(value).split('/'):
        raise Invalid('Use an absolute literal file path without spaces')
    return str(value)


def choice(obj, key, allowed, default):
    value = obj.get(key, default)
    if value not in allowed:
        raise Invalid('Invalid ' + key)
    return value


def stream_options(s):
    """Typed options supported by the router's OSS Nginx 1.26.3 baseline."""
    server, stream, listen = [], [], []
    tcp = s['protocol'] == 'tcp'
    sid = s['sid']
    def emit(name, value):
        server.append('    %s %s;' % (name, value))
    for key, default, lo, hi, suffix in (
            ('next_upstream_timeout', 0, 0, 604800, 's'),
            ('proxy_buffer_size', 16384, 1024, 16777216, ''),
            ('preread_buffer_size', 16384, 1024, 16777216, ''),
            ('preread_timeout', 30, 1, 300, 's')):
        directive = 'proxy_' + key if key == 'next_upstream_timeout' else key
        emit(directive, str(integer(s.get(key, default), lo, hi, key)) + suffix)
    emit('proxy_next_upstream', 'on' if flag(s, 'next_upstream', '1') else 'off')
    emit('tcp_nodelay', 'on' if flag(s, 'tcp_nodelay', '1') else 'off')
    bind = s.get('bind_address', '')
    if bind:
        if s['transparent']:
            raise Invalid('Local source bind and transparent mode cannot be combined')
        ip = address(bind)
        if ip.is_unspecified or ip.is_multicast:
            raise Invalid('Local source bind must be unicast')
        emit('proxy_bind', str(ip))
    for key in ('rcvbuf', 'sndbuf', 'backlog', 'fastopen'):
        if s.get('listen_' + key, '') != '':
            if key in ('backlog', 'fastopen') and not tcp:
                raise Invalid(key + ' requires a TCP-only service')
            listen.append('%s=%d' % (key, integer(s['listen_' + key], 1, 16777216 if key.endswith('buf') else 65535, key)))
    if flag(s, 'listen_deferred'):
        if not tcp:
            raise Invalid('Deferred accept requires TCP')
        listen.append('deferred')
    keepalive = choice(s, 'listen_keepalive', ('default', 'on', 'off'), 'default')
    if keepalive != 'default':
        if not tcp:
            raise Invalid('Listener keepalive requires TCP')
        listen.append('so_keepalive=' + keepalive)
    incoming = flag(s, 'accept_proxy_protocol')
    trusted = values(s, 'trusted_proxy')
    if incoming:
        if not tcp:
            raise Invalid('Incoming PROXY protocol requires TCP')
        listen.append('proxy_protocol')
        emit('proxy_protocol_timeout', '%ds' % integer(s.get('proxy_protocol_timeout', 30), 1, 300, 'PROXY timeout'))
    if trusted and not incoming:
        raise Invalid('Trusted proxies require incoming PROXY protocol')
    for net in trusted:
        try:
            network = ipaddress.ip_network(net, strict=False)
        except ValueError:
            raise Invalid('Invalid trusted proxy CIDR')
        if network.prefixlen == 0:
            raise Invalid('Trusted proxies must not trust every address')
        emit('set_real_ip_from', str(network))
    for key, variable in (('limit_connections', '$server_addr'), ('limit_per_client', '$binary_remote_addr')):
        count = integer(s.get(key, 0), 0, 65535, key)
        if count:
            name = key + '_' + sid
            size = integer(s.get('limit_zone_kb', 64), 32, 16384, 'Limit zone KiB')
            stream.append('  limit_conn_zone %s zone=%s:%dk;' % (variable, name, size))
            emit('limit_conn', '%s %d' % (name, count))
    if s.get('limit_connections', '0') != '0' or s.get('limit_per_client', '0') != '0':
        emit('limit_conn_dry_run', 'on' if flag(s, 'limit_dry_run') else 'off')
    tls = choice(s, 'tls_mode', ('off', 'terminate', 'preread'), 'off')
    if tls != 'off' and not tcp:
        raise Invalid('TLS requires a TCP-only service')
    if tls == 'preread':
        emit('ssl_preread', 'on')
    if tls == 'terminate':
        listen.append('ssl')
        for key in ('ssl_certificate', 'ssl_certificate_key'):
            emit(key, file_path(s.get(key, '')))
        emit('ssl_handshake_timeout', '%ds' % integer(s.get('ssl_handshake_timeout', 60), 1, 300, 'TLS handshake timeout'))
        emit('ssl_session_cache', 'shared:tls_%s:%dk' % (sid, integer(s.get('ssl_session_cache_kb', 1024), 64, 16384, 'TLS cache KiB')))
        emit('ssl_session_timeout', '%ds' % integer(s.get('ssl_session_timeout', 300), 1, 86400, 'TLS session timeout'))
        emit('ssl_session_tickets', 'on' if flag(s, 'ssl_session_tickets') else 'off')
        emit('ssl_prefer_server_ciphers', 'on' if flag(s, 'ssl_prefer_server_ciphers') else 'off')
        verify = choice(s, 'ssl_verify_client', ('off', 'on', 'optional', 'optional_no_ca'), 'off')
        emit('ssl_verify_client', verify)
        if verify != 'off':
            emit('ssl_client_certificate', file_path(s.get('ssl_client_certificate', '')))
            emit('ssl_verify_depth', integer(s.get('ssl_verify_depth', 2), 1, 20, 'Client certificate depth'))
        for key in ('ssl_dhparam', 'ssl_crl', 'ssl_trusted_certificate', 'ssl_password_file', 'ssl_session_ticket_key'):
            if s.get(key):
                emit(key, file_path(s[key]))
        for key in ('ssl_alpn', 'ssl_ecdh_curve'):
            if s.get(key):
                if not re.fullmatch(r'[A-Za-z0-9_./:+ -]{1,256}', s[key]):
                    raise Invalid('Invalid ' + key)
                emit(key, s[key])
    upstream_tls = flag(s, 'upstream_tls')
    if upstream_tls:
        if not tcp:
            raise Invalid('Upstream TLS requires TCP')
        emit('proxy_ssl', 'on')
        emit('proxy_ssl_server_name', 'on')
        emit('proxy_ssl_name', hostname(s.get('proxy_ssl_name', '')))
        verify = flag(s, 'proxy_ssl_verify', '1')
        emit('proxy_ssl_verify', 'on' if verify else 'off')
        if verify:
            emit('proxy_ssl_trusted_certificate', file_path(s.get('proxy_ssl_trusted_certificate', '')))
        emit('proxy_ssl_verify_depth', integer(s.get('proxy_ssl_verify_depth', 2), 1, 20, 'Upstream certificate depth'))
        emit('proxy_ssl_session_reuse', 'on' if flag(s, 'proxy_ssl_session_reuse', '1') else 'off')
        if s.get('proxy_ssl_certificate') or s.get('proxy_ssl_certificate_key'):
            for key in ('proxy_ssl_certificate', 'proxy_ssl_certificate_key'):
                emit(key, file_path(s.get(key, '')))
        for key in ('proxy_ssl_crl', 'proxy_ssl_password_file'):
            if s.get(key):
                emit(key, file_path(s[key]))
    for prefix, active in (('ssl_', tls == 'terminate'), ('proxy_ssl_', upstream_tls)):
        if active:
            protocols = choice(s, prefix + 'protocols', ('TLSv1.2', 'TLSv1.3', 'TLSv1.2 TLSv1.3'), 'TLSv1.2 TLSv1.3')
            emit(prefix + 'protocols', protocols)
            ciphers = s.get(prefix + 'ciphers', 'HIGH:!aNULL:!MD5')
            if not re.fullmatch(r'[A-Za-z0-9_:+!@=.-]{1,512}', ciphers):
                raise Invalid('Invalid TLS cipher list')
            emit(prefix + 'ciphers', ciphers)
    return stream, server, listen

def compile_config(sections, module=True):
    nsm_ops.validate(types.SimpleNamespace(**globals()), sections)
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
    shutdown_timeout = integer(g.get('shutdown_timeout', 15), 0, 604800, 'Worker shutdown timeout')
    pools, backends = {}, {}
    warnings = []
    for s in sections:
        sid = ident(s['.name'])
        if s['.type'] == 'pool':
            method = s.get('method', 'round_robin')
            if method not in ('round_robin', 'least_conn', 'hash_ip', 'hash', 'hash_consistent', 'random', 'random_two'):
                raise Invalid('Invalid pool method')
            pools[sid] = dict(s, method=method)
            pools[sid]['zone_kb'] = integer(s.get('zone_kb', 64), 32, 16384, 'Upstream zone KiB')
            if method in ('hash', 'hash_consistent'):
                pools[sid]['hash_key'] = choice(s, 'hash_key', ('$remote_addr', '$remote_addr:$remote_port', '$server_addr:$server_port', '$ssl_preread_server_name', '$ssl_server_name', '$proxy_protocol_addr'), '$remote_addr')
        elif s['.type'] == 'backend' and flag(s, 'enabled', '1'):
            raw = s.get('address', '')
            ip, port = None, 0
            if raw.startswith('unix:'):
                target = 'unix:' + file_path(raw[5:])
                if len(raw[5:]) > 100:
                    raise Invalid('UNIX socket path is too long')
            else:
                port = integer(s.get('port', ''), 1, 65535, 'Backend port')
                try:
                    ip = address(raw)
                except Invalid:
                    target = hostname(raw) + ':' + str(port)
                    warnings.append('%s: hostname resolved by Nginx at start/reload only' % sid)
                else:
                    if ip.is_unspecified or ip.is_multicast:
                        raise Invalid('Backend must be a unicast address')
                    target = endpoint(ip, port)
            b = {'ip': ip, 'port': port, 'target': target,
                 'weight': integer(s.get('weight', 1), 1, 1000, 'Weight'),
                 'max_fails': integer(s.get('max_fails', 1), 0, 1000, 'Max failures'),
                 'fail_timeout': integer(s.get('fail_timeout', 10), 1, 86400, 'Failure timeout'),
                 'max_conns': integer(s.get('max_conns', 0), 0, 1000000, 'Max connections'),
                 'backup': flag(s, 'backup'), 'down': flag(s, 'down')}
            backends.setdefault(s.get('pool', ''), []).append(b)
    for name, bs in backends.items():
        if name not in pools:
            raise Invalid('Backend refers to missing pool: ' + name)
        if pools[name]['method'] in ('hash_ip', 'hash', 'hash_consistent', 'random', 'random_two') and any(b['backup'] for b in bs):
            raise Invalid('NGINX hash/random pools do not support backup backends')
    routes = {}
    service_names = {s['.name'] for s in sections if s['.type'] == 'service'}
    for route in sections:
        if route['.type'] != 'route' or not flag(route, 'enabled', '1'):
            continue
        service = route.get('service', '')
        if service not in service_names or route.get('pool', '') not in pools:
            raise Invalid('Route refers to a missing service or pool')
        routes.setdefault(service, []).append(route)
    services, bound, returns, families, firewall = [], [], set(), set(), []
    for s in sections:
        if s['.type'] != 'service' or not flag(s, 'enabled', '1'):
            continue
        sid = ident(s['.name'])
        if sid == 'manager':
            raise Invalid('Service name manager is reserved for the manager log')
        pool = s.get('pool', '')
        targets = {pool} | {r['pool'] for r in routes.get(sid, [])}
        bs = []
        for target in targets:
            group = backends.get(target, [])
            if target not in pools or not any(not b['backup'] for b in group) or not any(not b['down'] for b in group):
                raise Invalid('Service %s needs each pool to have a primary backend and an available server' % sid)
            bs.extend(group)
        ip = address(s.get('listen_address', '0.0.0.0'))
        if ip.is_multicast:
            raise Invalid('Multicast listeners are unsupported')
        port = integer(s.get('listen_port', ''), 1, 65535, 'Listen port')
        protocol = s.get('protocol', 'tcp')
        if protocol not in ('tcp', 'udp', 'both'):
            raise Invalid('Invalid protocol')
        protos = ['tcp', 'udp'] if protocol == 'both' else [protocol]
        transparent = flag(s, 'transparent')
        if transparent and any(b['ip'] is None for b in bs):
            raise Invalid('Transparent mode requires literal IP backends, including routed pools')
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
        denied = []
        for net in values(s, 'deny_source'):
            try:
                n = ipaddress.ip_network(net, strict=False)
            except ValueError:
                raise Invalid('Invalid denied client CIDR')
            if n.version != ip.version:
                raise Invalid('Denied CIDR family must match listener family')
            denied.append(str(n))
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
                firewall_networks = networks
                if flag(s, 'accept_proxy_protocol') and values(s, 'trusted_proxy'):
                    try:
                        peers = [ipaddress.ip_network(n, strict=False) for n in values(s, 'trusted_proxy')]
                    except ValueError:
                        raise Invalid('Invalid trusted proxy CIDR')
                    firewall_networks = [str(n) for n in peers if n.version == ip.version]
                    if not firewall_networks:
                        raise Invalid('Firewall needs a trusted proxy matching the listener IP family')
                if firewall_networks:
                    rule += family + ' saddr { ' + ', '.join(firewall_networks) + ' } '
                firewall.append(rule + 'counter accept comment "nsm-listener-%s"' % sid)
        for proto in protos:
            for prev, pp, pproto in bound:
                if pp == port and pproto == proto and prev.version == ip.version and prev == ip:
                    raise Invalid('Overlapping listener: ' + endpoint(ip, port) + '/' + proto)
            bound.append((ip, port, proto))
        for b in bs:
            if b['ip'] is not None and b['port'] == port and (b['ip'] == ip or (ip.is_unspecified and b['ip'].is_loopback)):
                raise Invalid('Listener points back to itself: ' + sid)
            if b['target'].startswith('unix:') and protocol != 'tcp':
                raise Invalid('UNIX upstreams require a TCP-only service')
        item = dict(s, sid=sid, ip=ip, port=port, protocol=protocol, protos=protos, transparent=transparent,
                    networks=networks, denied=denied, pool=pool)
        item['error_level'] = choice(s, 'error_level', ('debug', 'info', 'notice', 'warn', 'error', 'crit', 'alert', 'emerg'), 'warn')
        for key, default, lo, hi in [('connect_timeout', 5, 1, 300), ('timeout', 600, 1, 604800),
                                     ('next_upstream_tries', 0, 0, 100), ('upload_rate', 0, 0, 2147483647),
                                     ('download_rate', 0, 0, 2147483647), ('udp_requests', 0, 0, 1000000)]:
            item[key] = integer(s.get(key, default), lo, hi, key)
        response = s.get('udp_responses', '')
        item['udp_responses'] = None if response == '' else integer(response, 0, 1000000, 'UDP responses')
        for key in ('access_log', 'traffic_history', 'keepalive', 'half_close', 'proxy_protocol'):
            item[key] = flag(s, key)
        if item['proxy_protocol'] and 'udp' in protos:
            raise Invalid('PROXY protocol output is supported for TCP services only')
        item['extras'] = stream_options(item)
        item['targets'] = targets
        routing = choice(s, 'routing', ('none', 'sni', 'alpn', 'source', 'tls_protocol'), 'none')
        if routes.get(sid) and routing == 'none':
            raise Invalid('Enable routing before adding routes to service ' + sid)
        if routing in ('sni', 'alpn', 'tls_protocol') and (protocol != 'tcp' or s.get('tls_mode', 'off') == 'off'):
            raise Invalid('TLS routing requires TLS termination or preread on a TCP service')
        item['routing_lines'] = []
        if routing != 'none':
            variable = {'source': '$remote_addr', 'sni': '$ssl_server_name', 'alpn': '$ssl_alpn_protocol', 'tls_protocol': '$ssl_protocol'}[routing]
            if s.get('tls_mode') == 'preread' and routing != 'source':
                variable = {'sni': '$ssl_preread_server_name', 'alpn': '$ssl_preread_alpn_protocols', 'tls_protocol': '$ssl_preread_protocol'}[routing]
            item['routing_lines'] = ['  %s %s $route_%s {' % ('geo' if routing == 'source' else 'map', variable, sid), '    default pool_%s;' % pool]
            if routing == 'sni':
                item['routing_lines'].insert(1, '    hostnames;')
            seen = set()
            for route in routes.get(sid, []):
                match = route.get('match', '')
                if routing == 'source':
                    try:
                        match = str(ipaddress.ip_network(match, strict=False))
                    except ValueError:
                        raise Invalid('Source route needs an IP or CIDR')
                elif routing == 'sni':
                    match = nsm_ops.sni_name(types.SimpleNamespace(**globals()), match)
                elif routing == 'alpn':
                    if not re.fullmatch(r'[A-Za-z0-9/.-]{1,64}', match):
                        raise Invalid('Invalid ALPN route')
                    if s.get('tls_mode') == 'preread':
                        # Restrict tokens above; dots are escaped with a character class.
                        match = '~(?:^|,)' + match.replace('.', '[.]') + '(?:,|$)'
                else:
                    if match not in ('none', 'TLSv1', 'TLSv1.1', 'TLSv1.2', 'TLSv1.3'):
                        raise Invalid('Invalid TLS protocol route')
                    if match == 'none':
                        match = '""'
                if match in ('default', 'hostnames', 'include', 'volatile'):
                    raise Invalid('Route match is a reserved Nginx map keyword')
                if match in seen:
                    raise Invalid('Duplicate route match for service ' + sid)
                seen.add(match)
                item['routing_lines'].append('    %s pool_%s;' % (match, route['pool']))
            item['routing_lines'].append('  }')
        if item.get('bind_address'):
            local = address(item['bind_address'])
            if any(b['ip'] is not None and b['ip'].version != local.version for b in bs):
                raise Invalid('Source bind and upstream IP families must match')
        if item.get('upstream_tls') == '1' and not flag(item, 'proxy_ssl_verify', '1'):
            warnings.append('%s: upstream TLS certificate verification is disabled' % sid)
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
              'worker_rlimit_nofile %d;' % nofile, 'worker_shutdown_timeout %ds;' % shutdown_timeout,
              'pid %s/nginx.pid;' % RUN, 'daemon off;',
              'error_log %s/manager.error.log warn;' % LOG,
              'events { worker_connections %d; multi_accept %s; accept_mutex %s; }' % (connections, 'on' if flag(g, 'multi_accept') else 'off', 'on' if flag(g, 'accept_mutex') else 'off'), 'stream {',
              "  log_format nsm '$remote_addr [$time_local] $protocol $status $bytes_sent $bytes_received $session_time upstream=$upstream_addr';"]
    if any(s['routing_lines'] and s.get('routing') != 'source' for s in services):
        # OpenWrt ARM may default to 32-byte buckets; even ordinary SNI names
        # do not fit. 512 accommodates a full 253-byte DNS name plus overhead.
        lines += ['  map_hash_bucket_size 512;', '  map_hash_max_size 4096;']
    used = {pool for s in services for pool in s['targets']}
    for name in sorted(used):
        lines += ['  upstream pool_%s {' % name, '    zone pool_%s %dk;' % (name, pools[name]['zone_kb'])]
        method = pools[name]['method']
        if method == 'least_conn':
            lines.append('    least_conn;')
        elif method == 'hash_ip':
            lines.append('    hash $remote_addr consistent;')
        elif method in ('hash', 'hash_consistent'):
            lines.append('    hash %s%s;' % (pools[name]['hash_key'], ' consistent' if method == 'hash_consistent' else ''))
        elif method in ('random', 'random_two'):
            lines.append('    random%s;' % (' two least_conn' if method == 'random_two' else ''))
        for b in backends[name]:
            opts = 'weight=%d max_fails=%d fail_timeout=%ds max_conns=%d' % (b['weight'], b['max_fails'], b['fail_timeout'], b['max_conns'])
            opts += ' backup' if b['backup'] else ''
            opts += ' down' if b['down'] else ''
            lines.append('    server %s %s;' % (b['target'], opts))
        lines.append('  }')
    for s in services:
        lines += s['extras'][0] + s['routing_lines']
        if s['traffic_history']:
            lines.append('  log_format traffic_%s escape=json \'{"service":"%s","time":"$msec","protocol":"$protocol","upstream":"$upstream_addr","sent":"$upstream_bytes_sent","received":"$upstream_bytes_received","connect":"$upstream_connect_time","session":"$session_time","status":"$status"}\';' % (s['sid'], s['sid']))
        for proto in s['protos']:
            lines += ['  server {', '    listen %s%s%s;' % (endpoint(s['ip'], s['port']),
                      (' udp' if proto == 'udp' else '') + ' reuseport' + (' bind' if not s['ip'].is_unspecified else '') + ''.join(' ' + opt for opt in s['extras'][2]), ' ipv6only=on' if s['ip'].version == 6 else ''),
                      '    proxy_pass %s;' % ('$route_' + s['sid'] if s['routing_lines'] else 'pool_' + s['pool']),
                      '    proxy_connect_timeout %ds;' % s['connect_timeout'], '    proxy_timeout %ds;' % s['timeout'],
                      '    proxy_next_upstream_tries %d;' % s['next_upstream_tries'],
                      '    proxy_upload_rate %d;' % s['upload_rate'], '    proxy_download_rate %d;' % s['download_rate'],
                      '    error_log %s/%s.error.log %s;' % (LOG, s['sid'], s['error_level'])]
            lines += s['extras'][1]
            if s['access_log']:
                lines.append('    access_log %s/%s.access.log nsm;' % (LOG, s['sid']))
            if s['traffic_history']:
                lines.append('    access_log %s/traffic.log traffic_%s;' % (LOG, s['sid']))
            if not s['access_log'] and not s['traffic_history']:
                lines.append('    access_log off;')
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
            lines += ['    deny %s;' % n for n in s['denied']]
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
            'return_interfaces': sorted({r[0] for r in returns}),
            'families': sorted(families), 'warnings': warnings, 'log_mb': log_mb,
            'traffic_services': [s['sid'] for s in services if s['traffic_history']],
            'observability': {k: g[k] for k in ('history_path', 'history_days') if k in g},
            'services': [{'id': s['sid'], 'listen': endpoint(s['ip'], s['port']), 'protocol': s['protocol'],
                          'pool': s['pool'], 'transparent': s['transparent']} for s in services]}

def atomic(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb' if isinstance(text, bytes) else 'w') as f:
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

def delete_nft_table():
    result = command(['nft', 'list', 'table', 'inet', TABLE], check=False)
    if result.returncode == 0:
        command(['nft', 'delete', 'table', 'inet', TABLE])
    elif 'No such file or directory' not in result.stderr:
        raise Invalid('Cannot inspect nftables table during cleanup: ' + result.stderr[:1000])

def check_listeners(compiled):
    """Reject external overlapping sockets even when SO_REUSEPORT permits bind.

    This is a preflight snapshot, not a reservation against a later bind race.
    """
    master = pid()
    owned = set()
    for process in ({master} | workers(master)) if master else set():
        for fd in Path('/proc/%d/fd' % process).iterdir():
            try:
                target = os.readlink(fd)
                if target.startswith('socket:['):
                    owned.add(target[8:-1])
            except FileNotFoundError:
                pass
    for proto in ('tcp', 'udp'):
        for family in (4, 6):
            path = Path('/proc/net/' + proto + ('6' if family == 6 else ''))
            if not path.exists():
                continue
            for line in path.read_text().splitlines()[1:]:
                fields = line.split()
                if fields[9] in owned or (proto == 'tcp' and fields[3] != '0A'):
                    continue
                if proto == 'udp' and int(fields[2].replace(':', ''), 16):
                    continue  # Connected upstream socket, not a listener.
                host, port = fields[1].split(':')
                packed = b''.join(int(host[i:i+8], 16).to_bytes(4, sys.byteorder) for i in range(0, len(host), 8))
                ip = ipaddress.ip_address(socket.inet_ntop(socket.AF_INET if family == 4 else socket.AF_INET6, packed))
                for service in compiled['services']:
                    addr, number = service['listen'].rsplit(':', 1)
                    wanted = address(addr.strip('[]'))
                    effective = ip
                    if family == 6 and wanted.version == 4:
                        effective = ip.ipv4_mapped or (address('0.0.0.0') if ip.is_unspecified else ip)
                    if (service['protocol'] in (proto, 'both') and int(number) == int(port, 16)
                            and wanted.version == effective.version
                            and (wanted == effective or wanted.is_unspecified or effective.is_unspecified)):
                        raise Invalid('Listener conflicts with an external socket: %s/%s' % (service['listen'], proto))

def preflight(compiled):
    LOG.mkdir(parents=True, exist_ok=True, mode=0o750)
    old = state()
    if not old and command(['nft', 'list', 'table', 'inet', TABLE], check=False).returncode == 0:
        raise Invalid('nftables table inet nsm already exists without ownership state')
    for iface in compiled.get('return_interfaces', []):
        if not (Path('/sys/class/net') / iface).exists():
            raise Invalid('Return interface does not exist: ' + iface)
    for fam in compiled['families']:
        if not old or fam not in old['families']:
            rules = command(['ip', '-%d' % fam, 'rule', 'show']).stdout
            routes = command(['ip', '-%d' % fam, 'route', 'show', 'table', ROUTE_TABLE], check=False).stdout
            if re.search(r'^%s:' % PRIORITY, rules, re.M) or routes.strip():
                raise Invalid('Routing priority %s or table %s is already in use (IPv%d)' % (PRIORITY, ROUTE_TABLE, fam))
    check_listeners(compiled)
    test = RUN / 'candidate.conf'
    atomic(test, compiled['nginx'])
    checked = command([NGINX, '-t', '-p', str(RUN) + '/', '-c', str(test)], check=False)
    if checked.returncode:
        error = (checked.stderr or checked.stdout).strip()[:3000]
        if 'set_real_ip_from' in error and ('not allowed here' in error or 'unknown directive' in error):
            raise Invalid('Installed stream module lacks stream real-IP support. nginx -V may describe a different module build. Use a matching module with --with-stream_realip_module or remove trusted proxies. ' + error)
        raise Invalid('Nginx validation failed: ' + error)
    command(['nft', '-c', '-f', '-'], data=nft_batch(compiled['nft']))
    check_rules = 'table inet nsm_check_%d {\n chain input {\n%s\n }\n}\n' % (os.getpid(), compiled['fw4'])
    command(['nft', '-c', '-f', '-'], data=check_rules)
    return compiled

def prepare():
    raw = bounded_read(APP_CONFIG, 1048576)
    sections = read_config(raw)
    c = compile_config(sections, module=Path(MODULE).exists())
    c['source_config'] = raw.decode()
    if not Path(MODULE).exists():
        version = command([NGINX, '-V']).stderr
        if not re.search(r'--with-stream(?:\s|$)', version):
            raise Invalid('Nginx stream module missing; install nginx-mod-stream')
    return preflight(c)

def route_add(fam):
    base = ['ip', '-%d' % fam]
    rules = command(base + ['rule', 'show']).stdout
    ours = [line for line in rules.splitlines() if line.startswith(PRIORITY + ':')]
    if ours:
        if len(ours) != 1 or not own_rule(ours[0]):
            raise Invalid('Routing priority collision: ' + ours[0])
    routes = routing_entries(fam)
    if any(not own_route(r) for r in routes) or len(routes) > 1:
        raise Invalid('Routing table collision (IPv%d)' % fam)
    if not routes:
        command(base + ['route', 'add', 'local', '0.0.0.0/0' if fam == 4 else '::/0', 'dev', 'lo', 'table', ROUTE_TABLE])
    if not ours:
        command(base + ['rule', 'add', 'priority', PRIORITY, 'fwmark', MASK, 'lookup', ROUTE_TABLE])

def own_rule(line):
    return bool(re.fullmatch(r'%s:\s+from all fwmark %s lookup %s\s*' % (PRIORITY, MASK, ROUTE_TABLE), line))

def routing_entries(fam):
    result = command(['ip', '-%d' % fam, '-j', 'route', 'show', 'table', ROUTE_TABLE], check=False)
    if result.returncode and 'FIB table does not exist' not in result.stderr:
        raise Invalid('Cannot inspect routing table: ' + result.stderr[:1000])
    return json.loads(result.stdout or '[]')

def own_route(route):
    return (route.get('type') == 'local' and route.get('dst') in ('default', '0.0.0.0/0', '::/0')
            and route.get('dev') == 'lo' and route.get('protocol', 'boot') == 'boot'
            and route.get('scope', 'host') == 'host' and route.get('metric', 0) in (0, 1024)
            and route.get('pref', 'medium') == 'medium'
            and not route.get('flags') and not (set(route) - {
                'type', 'dst', 'dev', 'protocol', 'scope', 'metric', 'flags', 'pref', 'table'}))

def route_del(fam):
    base = ['ip', '-%d' % fam]
    for line in command(base + ['rule', 'show']).stdout.splitlines():
        if own_rule(line):
            command(base + ['rule', 'del', 'priority', PRIORITY, 'from', 'all', 'fwmark', MASK, 'lookup', ROUTE_TABLE])
    for route in routing_entries(fam):
        if own_route(route):
            command(base + ['route', 'del', 'local', '0.0.0.0/0' if fam == 4 else '::/0', 'dev', 'lo', 'proto', 'boot', 'table', ROUTE_TABLE]
                    + (['metric', str(route['metric'])] if 'metric' in route else []))

def firewall_apply(text, force=False):
    path = RUN / 'fw4-input.nft'
    previous = path.read_text() if path.exists() else ''
    if not force and text.strip() == previous.strip():
        return
    atomic(path, text)
    try:
        command(['fw4', 'check'])
        command(['/etc/init.d/firewall', 'reload'], timeout=10)
    except Exception as error:
        atomic(path, previous)
        try:
            command(['fw4', 'check'])
            command(['/etc/init.d/firewall', 'reload'], timeout=10)
        except Exception as recovery:
            raise Invalid('%s; firewall recovery failed: %s' % (error, recovery)) from error
        raise

def network(c, old=None, force_firewall=False):
    for fam in c['families']:
        route_add(fam)
    command(['nft', '-f', '-'], data=nft_batch(c['nft']))
    firewall_apply(c.get('fw4', ''), force=force_firewall)
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
    candidate, since = set(), None
    while time.monotonic() < deadline:
        if pid() != master:
            raise Invalid('Nginx master exited during reload')
        current = workers(master) - previous_workers
        if current and current == candidate:
            if time.monotonic() - since >= 0.5:
                return
        else:
            candidate, since = current, time.monotonic()
        time.sleep(0.1)
    detail = tail_file(LOG / 'manager.error.log', 8)['text']
    raise Invalid('Nginx did not start a new worker generation after reload. ' + detail)

def apply(reload=False):
    with locked():
        if (RUN / 'pending.json').exists():
            raise Invalid('An interrupted apply needs cleanup; stop the manager before retrying')
        c = prepare()
        if not c['enabled']:
            raise Invalid('Manager is disabled; enable it in configuration first')
        old = state()
        previous = (RUN / 'nginx.conf').read_text() if (RUN / 'nginx.conf').exists() else None
        oldpid = pid()
        if oldpid and not reload:
            raise Invalid('Manager is already running; use Reload')
        if reload and not oldpid:
            raise Invalid('Manager is not running; use Start')
        before = workers(oldpid) if reload else set()
        atomic(RUN / 'pending.json', json.dumps({'families': sorted(set(c['families']) | set((old or {}).get('families', [])))}))
        try:
            network(c, old)
            atomic(RUN / 'nginx.conf', c['nginx'])
            if reload:
                os.kill(oldpid, signal.SIGHUP)
                confirm_reload(oldpid, before)
            atomic(RUN / 'state.json', json.dumps(c))
            (RUN / 'pending.json').unlink()
        except Exception as error:
            # Restore the configuration before attempting any recovery reload.
            if previous is not None:
                atomic(RUN / 'nginx.conf', previous)
            else:
                (RUN / 'nginx.conf').unlink(missing_ok=True)
            try:
                if old:
                    network(old, c, force_firewall=True)
                    atomic(RUN / 'state.json', json.dumps(old))
                else:
                    firewall_apply('', force=True)
                    delete_nft_table()
                    for fam in c['families']:
                        route_del(fam)
                    (RUN / 'state.json').unlink(missing_ok=True)
                if reload and oldpid and previous is not None:
                    if pid() != oldpid:
                        raise Invalid('Original Nginx master unavailable for recovery reload')
                    recovery_workers = workers(oldpid)
                    os.kill(oldpid, signal.SIGHUP)
                    confirm_reload(oldpid, recovery_workers)
                (RUN / 'pending.json').unlink(missing_ok=True)
            except Exception as recovery:
                raise Invalid('%s; recovery also failed: %s. Inspect routing and logs.' % (error, recovery)) from error
            raise
        if reload:
            working_snapshot()
        return {'ok': True, 'message': 'Configuration applied; new Nginx workers observed.' if reload else 'Configuration prepared.',
                'warnings': c['warnings'][:20]}

def cleanup():
    with locked():
        if pid():
            raise Invalid('Nginx is still running; refusing to remove its network state')
        old = state()
        pending_path = RUN / 'pending.json'
        pending = json.loads(pending_path.read_text()) if pending_path.exists() else {}
        if old or pending:
            firewall_apply('', force=True)
            delete_nft_table()
            for fam in set((old or {}).get('families', [])) | set(pending.get('families', [])):
                route_del(fam)
            (RUN / 'state.json').unlink(missing_ok=True)
            pending_path.unlink(missing_ok=True)
    return {'ok': True}

def lifecycle(started):
    deadline = time.monotonic() + (5 if started else 22)
    while time.monotonic() < deadline:
        master = pid()
        if started and master:
            confirm_reload(master, set())
            working_snapshot()
            return {'ok': True, 'message': 'Nginx workers observed; forwarding is not probed.'}
        if not started and not master:
            return cleanup()
        time.sleep(0.1)
    raise Invalid('Nginx did not %s in time; inspect manager logs' % ('start' if started else 'stop'))

def restore_network():
    with locked():
        if (RUN / 'pending.json').exists():
            raise Invalid('Interrupted apply: stop the manager to clean up before restoring')
        old = state()
        if old and pid():
            network(old, old)
        (RUN / 'restore-needed').unlink(missing_ok=True)
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

TRAFFIC_CELLS = 8192
TRAFFIC_SERIES = 256
TRAFFIC_READ = 2 * 1024 * 1024
TRAFFIC_RETENTION = 86400


def traffic_record(raw, now, detailed=False):
    record = json.loads(raw)
    sid = ident(record['service'])
    proto = record['protocol']
    if proto not in ('TCP', 'UDP'):
        raise Invalid('Invalid traffic protocol')
    timestamp = float(record['time'])
    if not now - TRAFFIC_RETENTION <= timestamp <= now + 60:
        return []
    minute = int(timestamp) // 60 * 60
    peers = record['upstream'].split(', ')
    sent, received = record['sent'].split(', '), record['received'].split(', ')
    if not len(peers) == len(sent) == len(received) or len(peers) > 100:
        raise Invalid('Unaligned upstream counters')
    rows = []
    connects = record.get('connect', '').split(', ')
    measured = 'connect' in record
    if measured and len(connects) != len(peers):
        raise Invalid('Unaligned connection timings')
    def milliseconds(value):
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,3})?', value):
            raise Invalid('Invalid duration')
        return integer(round(float(value) * 1000), 0, 2**53-1, 'Duration')
    for index, (peer, tx, rx) in enumerate(zip(peers, sent, received)):
        if peer == '-' or peer.startswith('pool_'):
            continue  # No upstream was selected.
        if peer.startswith('unix:'):
            target = 'unix:' + file_path(peer[5:])
        else:
            match = re.fullmatch(r'(?:\[([^\]]+)\]|([^:]+)):([0-9]+)', peer)
            if not match:
                raise Invalid('Invalid traffic endpoint')
            target = endpoint(address(match[1] or match[2]), integer(match[3], 1, 65535, 'Port'))
        # Missing values represent unsuccessful/unavailable measurements, not zero bytes.
        if tx == '-' or rx == '-':
            raise Invalid('Missing upstream counter')
        row = (sid + '|' + proto + '|' + target, str(minute),
               integer(tx, 0, 2**63 - 1, 'Sent bytes'), integer(rx, 0, 2**63 - 1, 'Received bytes'))
        if detailed and measured:
            final = index == len(peers)-1
            missing = connects[index] == '-'
            row += (int(missing or (final and record.get('status') in ('500', '502', '503'))), int(not final),
                    0 if missing else milliseconds(connects[index]), int(not missing),
                    milliseconds(record['session']) if final else 0, int(final))
        rows.append(row)
    return rows


def trim_traffic(data, now):
    for key in list(data['series']):
        bins = data['series'][key]
        for stamp in list(bins):
            if int(stamp) < now - TRAFFIC_RETENTION or int(stamp) > now + 60:
                del bins[stamp]
        if not bins:
            del data['series'][key]
    if len(data['series']) > TRAFFIC_SERIES:
        keys = sorted(data['series'], key=lambda k: max(map(int, data['series'][k])))
        for key in keys[:len(keys) - TRAFFIC_SERIES]:
            del data['series'][key]
        data['limited_at'] = now
    cells = [(int(stamp), key) for key, bins in data['series'].items() for stamp in bins]
    if len(cells) > TRAFFIC_CELLS:
        for stamp, key in sorted(cells)[:len(cells) - TRAFFIC_CELLS]:
            del data['series'][key][str(stamp)]
            if not data['series'][key]:
                del data['series'][key]
        data['limited_at'] = now


def collect_traffic(now=None):
    """Called under locked(). Cursor and aggregates commit together, including across rename rotation."""
    now = int(time.time() if now is None else now)
    path = RUN / 'traffic.json'
    try:
        data = json.loads(bounded_read(path, 2 * 1024 * 1024))
        if data.get('version') != 1:
            raise ValueError('Unknown traffic state version')
    except FileNotFoundError:
        data = {'version': 1, 'series': {}, 'cursors': {}, 'started': now, 'limited_at': 0}
        try:
            saved = nsm_ops.archive(types.SimpleNamespace(**globals()))
            if saved and saved.get('recent'):
                data = saved['recent']
                if saved.get('boot') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
                    data['cursors'] = {}
        except Exception as error:
            atomic(RUN / 'archive-error', str(error)[:500])
    trim_traffic(data, now)
    remaining, visible = TRAFFIC_READ, set()
    for log in (LOG / 'traffic.log.1', LOG / 'traffic.log'):
        try:
            stream = log.open('rb')
        except FileNotFoundError:
            continue
        with stream:
            stat = os.fstat(stream.fileno())
            key = '%s:%s' % (stat.st_dev, stat.st_ino)
            visible.add(key)
            cursor = data['cursors'].setdefault(key, {'offset': 0, 'size': 0, 'discard': False})
            anchor = cursor.get('anchor', 0)
            changed = anchor and hashlib.sha256(stream.read(anchor)).hexdigest() != cursor.get('digest')
            if stat.st_size < cursor['offset'] or changed:
                cursor.update(offset=0, discard=False)
                data['limited_at'] = now
            if not anchor or changed or stat.st_size < anchor:
                stream.seek(0)
                prefix = stream.read(min(128, stat.st_size))
                cursor.update(anchor=len(prefix), digest=hashlib.sha256(prefix).hexdigest())
            cursor['size'] = stat.st_size
            stream.seek(cursor['offset'])
            while remaining > 4096:
                start = stream.tell()
                raw = stream.readline(4097)
                if not raw:
                    break
                remaining -= len(raw)
                if cursor['discard']:
                    cursor['offset'] = stream.tell()
                    cursor['discard'] = not raw.endswith(b'\n')
                    continue
                if len(raw) > 4096:
                    cursor.update(offset=stream.tell(), discard=not raw.endswith(b'\n'))
                    data['limited_at'] = now
                    continue
                if not raw.endswith(b'\n'):
                    stream.seek(start)  # Retry an unfinished write on the next collection.
                    break
                cursor['offset'] = stream.tell()
                try:
                    rows = traffic_record(raw, now, detailed=True)
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    data['limited_at'] = now
                    continue
                for row in rows:
                    series, minute, tx, rx = row[:4]
                    cell = data['series'].setdefault(series, {}).setdefault(minute, [0, 0, 0])
                    cell[0] += tx
                    cell[1] += rx
                    cell[2] += 1  # Upstream attempts; retries may create more than one per session.
                    if len(row) > 4:
                        cell.extend([0] * (9-len(cell)))
                        for i, value in enumerate(row[4:], 3):
                            cell[i] += value
                # Keep working memory bounded even during a large log catch-up.
                if len(data['series']) > TRAFFIC_SERIES or sum(len(b) for b in data['series'].values()) > TRAFFIC_CELLS + 100:
                    trim_traffic(data, now)
    for key in list(data['cursors']):
        if key not in visible:
            if data['cursors'][key]['offset'] < data['cursors'][key]['size']:
                data['limited_at'] = now
            del data['cursors'][key]
    trim_traffic(data, now)
    atomic(path, json.dumps(data, separators=(',', ':')))
    try:
        nsm_ops.archive(types.SimpleNamespace(**globals()), data, now)
        (RUN / 'archive-error').unlink(missing_ok=True)
    except Exception as error:
        atomic(RUN / 'archive-error', str(error)[:500])
    return data


def traffic(args):
    sid = ident(args.get('service', ''))
    if sid not in {s['.name'] for s in read_config() if s['.type'] == 'service'}:
        raise Invalid('Unknown service')
    hours, proto, selected = args.get('hours', 1), args.get('protocol', 'all'), args.get('upstream', '')
    if hours not in (1, 6, 24, 168, 720) or proto not in ('all', 'TCP', 'UDP') or len(selected) > 256:
        raise Invalid('Invalid traffic selection')
    now = int(time.time())
    step = {1: 60, 6: 300, 24: 900, 168: 3600, 720: 21600}[hours]
    end = now // step * step
    start = end - hours * 3600 + step
    with locked():
        data = collect_traffic(now)
        active = sid in (state() or {}).get('traffic_services', [])
        if hours > 24:
            saved = nsm_ops.archive(types.SimpleNamespace(**globals()))
            if not saved:
                raise Invalid('Configure a mounted history drive for periods longer than 24 hours')
            data = dict(data, series=saved['hours'])
    peers = {}
    for key, bins in data['series'].items():
        service, protocol, peer = key.split('|')
        if service != sid or (proto != 'all' and protocol != proto):
            continue
        result = peers.setdefault(peer, dict.fromkeys(nsm_ops.METRICS, 0) | {'upstream': peer, 'points': {}})
        for stamp, values_ in bins.items():
            bucket = int(stamp) // step * step
            if start <= bucket <= end:
                cell = result['points'].setdefault(bucket, [0]*9)
                for i, value in enumerate(values_):
                    result[nsm_ops.METRICS[i]] += value
                    cell[i] += value
    names = sorted(peers, key=lambda p: (-peers[p]['sent'], p))
    if selected and selected not in peers:
        names_to_show = []
    else:
        names_to_show = [selected] if selected else names[:8]
    series = []
    for peer in names_to_show:
        result = peers[peer]
        result['points'] = [result['points'].get(t, [0]*9) for t in range(start, end + 1, step)]
        series.append(result)
    result = {'ok': True, 'enabled': active, 'start': start, 'step': step, 'now': now,
            'started': data['started'], 'endpoints': names[:64], 'endpoint_omitted': max(0, len(names)-64), 'series': series,
            'omitted': max(0, len(names) - len(series)),
            'limited': data['limited_at'] > 0 and data['limited_at'] >= now - TRAFFIC_RETENTION,
            'pending': any(c['offset'] < c['size'] for c in data['cursors'].values())}
    if hours > 24:
        result['limited'] = result['limited'] or saved.get('limited', False)
    while len(json.dumps(result).encode()) > 54000 and result['series']:
        result['series'].pop()
        result['omitted'] += 1
    return result


def rotate():
    with locked():
        collect_traffic()
        old = state()
        if not old:
            return {'ok': True}
        changed = False
        for p in LOG.glob('*.log'):
            limit = 512 * 1024 if p.name == 'traffic.log' else old['log_mb'] * 1024 * 1024
            if p.is_file() and p.stat().st_size > limit:
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
        for scope in ['all'] + old.get('return_interfaces', []):
            p = Path('/proc/sys/net/ipv4/conf') / scope / 'rp_filter'
            if p.exists() and p.read_text().strip() == '1':
                warnings.append('Strict rp_filter on %s may reject asymmetric returns.' % scope)
    if (RUN / 'pending.json').exists():
        warnings.append('Interrupted apply or incomplete recovery: stop the manager to clean up before retrying.')
    operation = RUN / 'operation.json'
    operation_state = json.loads(operation.read_text()) if operation.exists() else None
    if operation_state and operation_state.get('pending'):
        with (RUN / 'control.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                operation_state = dict(operation_state, pending=False, ok=False,
                                       error='Service action was interrupted; inspect runtime state before retrying.')
            except BlockingIOError:
                pass
    return {'ok': True, 'version': VERSION, 'running': bool(pid()), 'pid': pid(),
            'operation': operation_state,
            'boot_enabled': command([INIT, 'enabled'], check=False).returncode == 0,
            'services': (old or {}).get('services', [])[:128], 'warnings': list(dict.fromkeys(warnings))[:20],
            'binaries': {x: bool(shutil.which(x)) for x in ('nginx', 'nft', 'ip', 'uci')},
            'stream_module': Path(MODULE).exists(),
            'last_error': tail_file(RUN / 'last-error', 30, 3000)['text']}

SOURCE_CONFIG = Path('/etc/nginx/nginx.conf')
APP_CONFIG = Path('/etc/config/nginx_stream_manager')
IMPORT_BACKUPS = Path('/etc/nginx-stream-manager/import-backups')

def import_plan():
    from nsm_importer import plan
    with SOURCE_CONFIG.open('rb') as f:
        raw = f.read(262145)
    if len(raw) > 262144:
        raise Invalid('nginx.conf exceeds the 256 KiB import limit')
    current_bytes = bounded_read(APP_CONFIG, 1048576)
    current = read_config(current_bytes)
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
        atomic(backup, previous)
        # Check once more just before replacement; do not overwrite intervening edits.
        fresh, _, _ = import_plan()
        if fresh['token'] != token or fresh['blockers']:
            raise Invalid('Configuration or manager state changed while importing; preview again')
        atomic(APP_CONFIG, serialized)
        return {'ok': True, 'message': 'Imported %d disabled services. Existing Nginx was not modified or reloaded.' % len(result['services']),
                'backup': str(backup), 'services_imported': len(result['services'])}

CONTROL_ACTIONS = ('start', 'stop', 'reload', 'enable', 'disable')

def queue_control(action):
    if action not in CONTROL_ACTIONS:
        raise Invalid('Unsupported control action')
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUN / 'control.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Invalid('Another service action is running')
        atomic(RUN / 'operation.json', json.dumps({'action': action, 'pending': True}))
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'control-job', action, str(lock.fileno())],
                             pass_fds=(lock.fileno(),), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            (RUN / 'operation.json').unlink(missing_ok=True)
            raise
    return {'ok': True, 'message': 'Action accepted: %s. Overview shows completion or failure.' % action}

def control_job(action, fd):
    # The inherited descriptor keeps control.lock locked after the RPC exits.
    with os.fdopen(fd, 'w'):
        result = {'action': action, 'pending': False}
        try:
            if action not in CONTROL_ACTIONS:
                raise Invalid('Unsupported control action')
            if action == 'start' and not flag(next(s for s in read_config() if s['.type'] == 'global'), 'enabled'):
                raise Invalid('Manager is disabled; enable it in Configuration first')
            if action in ('start', 'reload'):
                (RUN / 'last-error').unlink(missing_ok=True)
            command([INIT, action], timeout=90)
            if action in ('start', 'reload') and (RUN / 'last-error').exists():
                raise Invalid(tail_file(RUN / 'last-error', 30, 3000)['text'])
            result.update(ok=True, message='Command completed: ' + action)
        except Exception as error:
            result.update(ok=False, error=str(error)[:3000])
        atomic(RUN / 'operation.json', json.dumps(result))
    return result

def rpc(method, args):
    if not isinstance(args, dict):
        raise Invalid('RPC arguments must be a JSON object')
    # uhttpd/rpcd inject this transport metadata for authenticated LuCI calls.
    # Authorization remains rpcd's ACL responsibility, not a caller-supplied ID.
    if 'ubus_rpc_session' in args:
        args = dict(args)
        session = args.pop('ubus_rpc_session')
        if not isinstance(session, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', session):
            raise Invalid('Invalid RPC session metadata')
    if method not in SIGNATURES or set(args) - set(SIGNATURES[method]):
        raise Invalid('Unknown method or argument')
    for key, value in args.items():
        if type(value) is not type(SIGNATURES[method][key]):
            raise Invalid('Invalid argument type: ' + key)
    if method == 'import_preview':
        return import_preview()
    if method == 'import_commit':
        return import_commit(args.get('token'))
    if method == 'status':
        return status()
    if method == 'logs':
        return logs(args)
    if method == 'traffic':
        return traffic(args)
    if method == 'monitor':
        return ops().monitor(args.get('page', 0))
    if method == 'monitor_refresh':
        return monitor_tick()
    if method == 'diagnostics':
        return ops().diagnostics()
    if method == 'metrics':
        return ops().metrics()
    if method == 'config_tools':
        return ops().backup(args)
    if method == 'route_test':
        return ops().route_test(args)
    if method in ('preview', 'validate'):
        with locked():
            c = prepare() if method == 'validate' else compile_config(read_config(), Path(MODULE).exists())
            if method == 'validate':
                return {'ok': True, 'message': 'Nginx and nftables syntax checked.', 'warnings': c['warnings'][:20]}
            # Keep the ubus response bounded even with many services.
            return {'ok': True, 'nginx': c['nginx'][:18000], 'nft': c['nft'][:8000], 'fw4': c['fw4'][:8000],
                    'truncated': len(c['nginx']) > 18000 or len(c['nft']) > 8000 or len(c['fw4']) > 8000}
    if method == 'control':
        return queue_control(args.get('action'))
    raise Invalid('Unknown method')

SIGNATURES = {'import_preview': {}, 'import_commit': {'token': ''}, 'status': {}, 'preview': {}, 'validate': {},
              'monitor': {'page': 0}, 'monitor_refresh': {}, 'diagnostics': {}, 'metrics': {},
              'config_tools': {'action': 'list', 'source': '', 'name': '', 'content': '', 'token': ''},
              'route_test': {'service': '', 'name': ''},
              'traffic': {'service': '', 'hours': 1, 'protocol': 'all', 'upstream': ''},
              'logs': {'service': 'manager', 'kind': 'error', 'lines': 500},
              'control': {'action': 'status'}}

def main():
    try:
        op = sys.argv[1] if len(sys.argv) > 1 else 'status'
        if op == 'list':
            result = SIGNATURES
        elif op == 'control-job':
            result = control_job(sys.argv[2], int(sys.argv[3]))
        elif op == 'call':
            limit = 65536 if len(sys.argv) > 2 and sys.argv[2] == 'config_tools' else 8192
            raw = sys.stdin.read(limit + 1)
            if len(raw.encode()) > limit:
                raise Invalid('RPC input exceeds %d bytes' % limit)
            args = json.loads(raw or '{}')
            result = rpc(sys.argv[2], args)
        elif op == 'prepare':
            result = apply()
        elif op == 'reload':
            result = apply(reload=True)
        elif op == 'cleanup':
            result = cleanup()
        elif op in ('started', 'stopped'):
            result = lifecycle(op == 'started')
        elif op == 'running':
            return 0 if pid() else 1
        elif op == 'configured':
            globals_ = [s for s in read_config() if s['.type'] == 'global' and s['.name'] == 'main']
            if len(globals_) != 1:
                raise Invalid('Exactly one global main section is required')
            print('1' if flag(globals_[0], 'enabled') else '0')
            return 0
        elif op == 'restore':
            atomic(RUN / 'restore-needed', '1')
            result = restore_network()
        elif op == 'rotate':
            result = rotate()
        elif op == 'monitor-tick':
            result = monitor_tick()
        else:
            result = rpc(op, {})
        if op in ('prepare', 'reload', 'started'):
            (RUN / 'last-error').unlink(missing_ok=True)
        output = json.dumps(result)
        if len(output.encode()) > 60000:
            raise Invalid('Response exceeds 60000 bytes; reduce configuration or requested output')
        print(output)
        return 0
    except Exception as e:
        if len(sys.argv) > 1 and sys.argv[1] in ('prepare', 'reload', 'started', 'stopped', 'restore', 'configured'):
            atomic(RUN / 'last-error', str(e)[:3000])
        print(json.dumps({'ok': False, 'error': str(e)[:3000]}))
        # rpcd must receive the JSON even when the operation failed.
        return 0 if len(sys.argv) > 1 and sys.argv[1] == 'call' else 1

if __name__ == '__main__':
    sys.exit(main())
