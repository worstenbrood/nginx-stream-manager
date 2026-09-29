"""Bounded monitoring and configuration tools; never evaluates shell input."""
import concurrent.futures
import contextlib
import copy
import difflib
import hashlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time

HISTORY = Path('/etc/nginx-stream-manager/history')
MAX_CONFIG = 32768
METRICS = ('sent', 'received', 'attempts', 'failures', 'retries', 'connect_ms', 'connect_samples', 'session_ms', 'sessions')

def sni_name(n, value):
    return '*.' + n.hostname(value[2:]) if value.startswith('*.') else n.hostname(value)

def validate(n, sections):
    g = next((s for s in sections if s['.type'] == 'global'), {})
    for key, default, lo, hi in (('health_interval', 60, 30, 3600), ('health_fall', 3, 1, 10),
                               ('health_rise', 2, 1, 10), ('certificate_warn_days', 30, 1, 90),
                               ('history_days', 7, 1, 30)):
        n.integer(g.get(key, default), lo, hi, key)
    n.flag(g, 'alert_syslog')
    path = g.get('history_path', '')
    if path and (not n.file_path(path).startswith('/mnt/') or len(Path(path).parts) < 4):
        raise n.Invalid('History directory must be below a mounted drive, e.g. /mnt/usb/nsm')
    for b in sections:
        if b['.type'] != 'backend':
            continue
        mode = n.choice(b, 'health_check', ('off', 'tcp', 'tls', 'dns'), 'off')
        if mode == 'off':
            continue
        raw = b.get('address', '')
        if raw.startswith('unix:'):
            n.file_path(raw[5:])
            if mode != 'tcp':
                raise n.Invalid('UNIX health checks support TCP connect only')
        else:
            try:
                ip = n.address(raw)
            except n.Invalid:
                n.hostname(raw)
            else:
                if ip.is_unspecified or ip.is_multicast:
                    raise n.Invalid('Health target must be unicast')
            n.integer(b.get('port', ''), 1, 65535, 'Health port')
        if mode == 'tls':
            n.hostname(b.get('health_tls_name', ''))
            if b.get('health_ca'):
                n.file_path(b['health_ca'])
        if mode == 'dns':
            n.hostname(b.get('health_dns_name', 'example.com'))

def probe(b):
    """Runs in a killable subprocess; DNS resolution shares its four-second budget."""
    started = time.monotonic()
    mode, host = b['health_check'], b['address']
    if host.startswith('unix:'):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        with sock:
            sock.connect(host[5:])
    elif mode == 'dns':
        addr = socket.getaddrinfo(host, int(b['port']), type=socket.SOCK_DGRAM)[0]
        query_id = os.urandom(2)
        name = b.get('health_dns_name', 'example.com').rstrip('.')
        question = b''.join(bytes([len(label)]) + label.encode('ascii') for label in name.split('.')) + b'\0\0\1\0\1'
        query = query_id + struct.pack('!HHHHH', 0x100, 1, 0, 0, 0) + question
        with socket.socket(addr[0], socket.SOCK_DGRAM) as sock:
            sock.settimeout(2); sock.connect(addr[4]); sock.send(query)
            reply = sock.recv(4096)
        if len(reply) < 12 + len(question) or reply[:2] != query_id or not reply[2] & 0x80 or reply[3] & 15 or reply[4:6] != b'\0\1' or reply[12:12+len(question)].lower() != question.lower():
            raise ValueError('DNS response ID/question/rcode did not validate')
    else:
        with socket.create_connection((host, int(b['port'])), timeout=2) as sock:
            if mode == 'tls':
                context = ssl.create_default_context(cafile=b.get('health_ca') or None)
                with context.wrap_socket(sock, server_hostname=b['health_tls_name']):
                    pass
    return {'ok': True, 'latency_ms': round((time.monotonic() - started) * 1000, 2)}

def read_json(n, path, default, limit=4*1024*1024):
    try:
        return json.loads(n.bounded_read(path, limit))
    except FileNotFoundError:
        return copy.deepcopy(default)

def archive_path(n, settings):
    raw = settings.get('history_path', '')
    if not raw:
        return None
    path = Path(n.file_path(raw))
    if not raw.startswith('/mnt/') or len(path.parts) < 4 or path.resolve() != path:
        raise n.Invalid('History requires a literal directory below /mnt without symlinks')
    if not any(os.path.ismount(p) for p in path.parents if str(p) not in ('/', '/mnt')):
        raise n.Invalid('History drive is not mounted; refusing to write to router flash')
    return path / 'history.json'

@contextlib.contextmanager
def archive_directory(n, path, create):
    """Pin the mount with directory descriptors; an unmount cannot redirect writes to flash."""
    mount = next((p for p in path.parents if str(p) not in ('/', '/mnt') and os.path.ismount(p)), None)
    if mount is None:
        raise n.Invalid('History mount disappeared')
    descriptors = []
    try:
        fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        opened, current = os.fstat(fd), os.stat(mount)
        if not os.path.ismount(mount) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise n.Invalid('History mount changed during access')
        for part in path.relative_to(mount).parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        yield Path('/proc/self/fd') / str(fd)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)

def archive(n, data=None, now=None):
    settings = (n.state() or {}).get('observability', {})
    path = archive_path(n, settings)
    if path is None:
        return None
    try:
        with archive_directory(n, path.parent, data is not None) as directory:
            return archive_file(n, directory / path.name, settings, data, now)
    except FileNotFoundError:
        if data is None:
            return None
        raise

def archive_file(n, path, settings, data=None, now=None):
    stored = read_json(n, path, {'version': 1, 'hours': {}, 'updated': 0}, 8*1024*1024)
    if stored.get('version') != 1:
        raise n.Invalid('Unsupported archive version')
    if data is None:
        return stored
    now = int(now or time.time())
    if now - stored['updated'] < 300:
        return stored
    cutoff = now - n.integer(settings.get('history_days', 7), 1, 30, 'History days') * 86400
    for key, bins in data['series'].items():
        hours = {}
        for stamp, values in bins.items():
            hour = str(int(stamp) // 3600 * 3600)
            cell = hours.setdefault(hour, [0] * 9)
            for i, value in enumerate(values):
                cell[i] += value
        target = stored['hours'].setdefault(key, {})
        for stamp, cell in hours.items():
            # Never overwrite the oldest partially expired hour with an incomplete aggregate.
            if int(stamp) >= now - n.TRAFFIC_RETENTION + 3600 or stamp not in target:
                target[stamp] = cell
    for key in list(stored['hours']):
        stored['hours'][key] = {t: c for t, c in stored['hours'][key].items() if int(t) >= cutoff}
        if not stored['hours'][key]:
            del stored['hours'][key]
    cells = sorted((int(t), k) for k, bins in stored['hours'].items() for t in bins)
    stored['limited'] = stored.get('limited', False) or len(cells) > 32768 or bool(data.get('limited_at'))
    for t, k in cells[:-32768]:
        del stored['hours'][k][str(t)]
    stored['recent'] = data
    stored['boot'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    stored['updated'] = now
    text = json.dumps(stored, separators=(',', ':'))
    if len(text.encode()) > 8*1024*1024:
        raise n.Invalid('Archive exceeds 8 MiB; shorten retention')
    n.atomic(path, text)
    return stored

class Operations:
    def __init__(self, n):
        self.n = n

    def snapshot(self, label='manual', raw=None):
        n = self.n
        raw = n.bounded_read(n.APP_CONFIG, MAX_CONFIG) if raw is None else raw
        if len(raw) > MAX_CONFIG:
            raise n.Invalid('Snapshot exceeds 32 KiB')
        digest = hashlib.sha256(raw).hexdigest()
        HISTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = HISTORY / (digest + '.json')
        current = read_json(n, path, {})
        if not current or (label == 'working' and current.get('label') != 'working'):
            n.atomic(path, json.dumps({'id': digest, 'time': int(time.time()), 'label': label, 'config': raw.decode()}))
        files = sorted(HISTORY.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[10:]:
            old.unlink()
        return digest

    def backup(self, args):
        n = self.n
        from nsm_importer import uci_text
        action = args.get('action', 'list')
        with n.locked(), contextlib.ExitStack() as stack:
            if action == 'commit':
                control = stack.enter_context((n.RUN / 'control.lock').open('a'))
                try:
                    fcntl.flock(control, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise n.Invalid('A service action is in progress; wait before committing')
            raw = n.bounded_read(n.APP_CONFIG, MAX_CONFIG)
            current = n.read_config(raw)
            if action == 'list':
                items = [read_json(n, p, {}, MAX_CONFIG*2) for p in HISTORY.glob('*.json')]
                return {'ok': True, 'items': [{k: s[k] for k in ('id', 'time', 'label')} for s in sorted(items, key=lambda s: s['time'], reverse=True)[:10]],
                        'sections': [{'id': s['.name'], 'type': s['.type']} for s in current if s['.type'] in ('service', 'pool')]}
            if action == 'export':
                return {'ok': True, 'content': json.dumps({'format': 'nsm-config-1', 'config': raw.decode()}, indent=2)}
            if action == 'snapshot':
                return {'ok': True, 'id': self.snapshot(), 'message': 'Snapshot saved.'}
            if action == 'commit':
                pending = read_json(n, n.RUN / 'config-preview.json', {}, MAX_CONFIG*3)
                if not pending or args.get('token') != pending['token'] or time.time() > pending['expires'] or hashlib.sha256(raw).hexdigest() != pending['base']:
                    raise n.Invalid('Preview expired or configuration changed; preview again')
                if n.pid() or n.command(['uci', 'changes', n.CONFIG]).stdout.strip():
                    raise n.Invalid('Stop the manager and save/discard pending LuCI edits before restoring/importing/duplicating/renaming')
                candidate = n.read_config(pending['config'].encode())
                n.preflight(n.compile_config(candidate))
                self.snapshot('before-change')
                # Check again after validation before replacing the committed configuration.
                if n.bounded_read(n.APP_CONFIG, MAX_CONFIG) != raw or n.command(['uci', 'changes', n.CONFIG]).stdout.strip() or n.pid():
                    raise n.Invalid('Configuration changed during validation')
                n.atomic(n.APP_CONFIG, pending['config'])
                (n.RUN / 'config-preview.json').unlink(missing_ok=True)
                return {'ok': True, 'message': 'Configuration saved. Refresh Configuration, review settings, then start explicitly.'}
            if action == 'restore':
                source = args.get('source', '')
                if not re.fullmatch(r'[0-9a-f]{64}', source):
                    raise n.Invalid('Invalid snapshot ID')
                candidate = n.read_config(read_json(n, HISTORY / (source + '.json'), {}, MAX_CONFIG*2)['config'].encode())
            elif action == 'import':
                content = args.get('content', '')
                if len(content.encode()) > MAX_CONFIG*2:
                    raise n.Invalid('Backup is too large')
                imported = json.loads(content)
                if imported.get('format') != 'nsm-config-1' or not isinstance(imported.get('config'), str):
                    raise n.Invalid('Expected an exported nsm-config-1 backup')
                candidate = n.read_config(imported['config'].encode())
            elif action == 'rename':
                name = n.ident(args.get('name', ''))
                source = next((s for s in current if s['.name'] == args.get('source')), None)
                if not source or source['.type'] not in ('pool', 'service') or name in {s['.name'] for s in current}:
                    raise n.Invalid('Choose an existing service/pool and a unique new name')
                if source['.type'] == 'service' and name == 'manager':
                    raise n.Invalid('Service name manager is reserved for the manager log')
                candidate = copy.deepcopy(current)
                old = source['.name']
                # Update references in disabled sections too, before validating the full candidate.
                references = {'backend': 'pool', 'service': 'pool', 'route': 'pool'} if source['.type'] == 'pool' else {'route': 'service'}
                for section in candidate:
                    if section['.name'] == old:
                        section['.name'] = name
                    ref = references.get(section['.type'])
                    if ref and section.get(ref) == old:
                        section[ref] = name
            elif action == 'duplicate':
                name = n.ident(args.get('name', ''))
                source = next((s for s in current if s['.name'] == args.get('source')), None)
                if not source or source['.type'] not in ('pool', 'service') or name in {s['.name'] for s in current}:
                    raise n.Invalid('Choose an existing service/pool and a unique new name')
                candidate = copy.deepcopy(current)
                clone = dict(copy.deepcopy(source), **{'.name': name})
                clone['name'] = name
                if clone['.type'] == 'service':
                    clone['enabled'] = '0'
                candidate.append(clone)
                occupied = {s['.name'] for s in candidate}
                for child in current:
                    ref = 'pool' if source['.type'] == 'pool' else 'service'
                    kind = 'backend' if ref == 'pool' else 'route'
                    if child['.type'] == kind and child.get(ref) == source['.name']:
                        new_id = 'copy_' + hashlib.sha256((name + child['.name']).encode()).hexdigest()[:20]
                        if new_id in occupied:
                            raise n.Invalid('Duplicate child ID collision')
                        occupied.add(new_id)
                        candidate.append(dict(copy.deepcopy(child), **{'.name': new_id, ref: name}))
            else:
                raise n.Invalid('Unsupported configuration action')
            n.compile_config(candidate)
            text = uci_text(candidate)
            if len(text.encode()) > MAX_CONFIG or n.parse_uci(text) != candidate:
                raise n.Invalid('Configuration is too large or cannot round-trip')
            # Compare canonical UCI so harmless quoting/spacing does not hide real changes.
            diff = ''.join(difflib.unified_diff(uci_text(current).splitlines(True), text.splitlines(True), fromfile='current', tofile='candidate'))
            token = os.urandom(24).hex()
            n.atomic(n.RUN / 'config-preview.json', json.dumps({'token': token, 'base': hashlib.sha256(raw).hexdigest(), 'config': text, 'expires': time.time()+600}))
            return {'ok': True, 'token': token, 'diff': diff[:18000], 'truncated': len(diff) > 18000,
                    'message': 'Preview only. Commit requires a stopped manager and no pending LuCI edits. Certificates and keys are not included in backups.'}

    def route_test(self, args):
        n = self.n
        sections = n.read_config()
        service = next((s for s in sections if s['.type'] == 'service' and s['.name'] == args.get('service')), None)
        if not service or service.get('routing') != 'sni':
            raise n.Invalid('Select a service with SNI routing')
        name = n.hostname(args.get('name', ''))
        routes = [s for s in sections if s['.type'] == 'route' and s.get('service') == service['.name'] and n.flag(s, 'enabled', '1')]
        matches = []
        for r in routes:
            match = sni_name(n, r.get('match', ''))
            if name == match:
                matches.append((1, len(match), r))
            elif match.startswith('*.') and name.endswith(match[1:]) and name != match[2:]:
                matches.append((0, len(match), r))
        winner = max(matches, key=lambda v: v[:2])[2] if matches else None
        return {'ok': True, 'pool': winner['pool'] if winner else service['pool'], 'match': winner['match'] if winner else 'default', 'name': name}

    def certificates(self, sections, selected=None):
        n = self.n
        result = []
        paths = {}
        for s in sections:
            if s['.type'] == 'service':
                for key in ('ssl_certificate', 'proxy_ssl_certificate'):
                    if s.get(key):
                        paths.setdefault(s[key], []).append(s['.name'])
        for path, services in paths.items():
            if selected is not None and path not in selected:
                continue
            item = {'path': path, 'services': services, 'checked': int(time.time())}
            try:
                raw = n.bounded_read(n.file_path(path), 65536)
                with tempfile.NamedTemporaryFile() as f:
                    f.write(raw); f.flush()
                    decoded = n.command(['openssl', 'x509', '-in', f.name, '-noout', '-dates', '-subject', '-issuer', '-ext', 'subjectAltName'], timeout=2).stdout
                expiry = re.search(r'^notAfter=(.+)$', decoded, re.M)
                begins = re.search(r'^notBefore=(.+)$', decoded, re.M)
                item.update(ok=True, details=decoded[:800], expires=ssl.cert_time_to_seconds(expiry[1]),
                            not_before=ssl.cert_time_to_seconds(begins[1]))
                item['days_left'] = int((item['expires'] - time.time()) // 86400)
            except Exception as error:
                item.update(ok=False, error=str(error)[:300])
            result.append(item)
        return result

    def run_probe(self, b):
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), json.dumps(b)], capture_output=True, text=True, timeout=4)
            if proc.returncode:
                return {'ok': False, 'error': proc.stderr[-300:] or 'Probe failed'}
            return json.loads(proc.stdout)
        except Exception as error:
            return {'ok': False, 'error': str(error)[:300]}

    def tick(self):
        n = self.n
        sections = n.read_config()
        validate(n, sections)
        g = next(s for s in sections if s['.type'] == 'global')
        now = int(time.time())
        path = n.RUN / 'monitor.json'
        data = read_json(n, path, {'backends': {}, 'events': [], 'conditions': {}, 'certificates': [], 'cert_checked': 0})
        enabled = [b for b in sections if b['.type'] == 'backend' and n.flag(b, 'enabled', '1') and b.get('health_check', 'off') != 'off']
        allowed = {b['.name'] for b in enabled}
        data['backends'] = {k: v for k, v in data['backends'].items() if k in allowed}
        # Oldest first avoids starving later backends when there are more than 16.
        due = sorted(enabled, key=lambda b: data['backends'].get(b['.name'], {}).get('checked', 0))
        due = [b for b in due if now - data['backends'].get(b['.name'], {}).get('checked', 0) >= int(g.get('health_interval', 60))][:16]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(self.run_probe, due))
        for b, result in zip(due, results):
            signature = hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()
            old = data['backends'].get(b['.name'], {})
            if old.get('signature') != signature:
                old = {}
            success = result.get('ok') is True
            streak = old.get('streak', 0) + 1 if old.get('success') == success else 1
            status = old.get('status', 'unknown')
            if streak >= int(g.get('health_rise' if success else 'health_fall', 2 if success else 3)):
                status = 'up' if success else 'down'
            data['backends'][b['.name']] = dict(result, signature=signature, status=status, success=success, streak=streak,
                                               checked=now, mode=b['health_check'], address=b['address'], pool=b.get('pool', ''))
        configured_certs = {s[key] for s in sections if s['.type'] == 'service' for key in ('ssl_certificate', 'proxy_ssl_certificate') if s.get(key)}
        cached = {c['path']: c for c in data['certificates'] if c['path'] in configured_certs}
        due_certs = sorted(configured_certs, key=lambda p: cached.get(p, {}).get('checked', 0))
        due_certs = [p for p in due_certs if now-cached.get(p, {}).get('checked', 0) >= 3600][:4]
        cached.update({c['path']: c for c in self.certificates(sections, due_certs)})
        data['certificates'] = list(cached.values())
        data['cert_checked'] = now
        conditions = {}
        for name, b in data['backends'].items():
            if b['status'] == 'down':
                conditions['backend:' + name] = 'Backend %s health check is down: %s' % (name, b.get('error', 'unreachable'))
        for c in data['certificates']:
            if c['ok']:
                c['days_left'] = int((c['expires'] - now) // 86400)
            if not c['ok'] or c['days_left'] <= int(g.get('certificate_warn_days', 30)) or c['not_before'] > now:
                conditions['certificate:' + c['path']] = 'Certificate %s: %s' % (c['path'], c.get('error', 'expires in %s days' % c.get('days_left', '?')))
        last_error = n.tail_file(n.RUN / 'last-error', 10, 1000)['text']
        if last_error:
            conditions['reload'] = 'Last manager action failed: ' + last_error
        storage_error = n.tail_file(n.RUN / 'archive-error', 5, 500)['text']
        if storage_error:
            conditions['storage'] = storage_error
        previous = data['conditions']
        for key in set(conditions) | set(previous):
            message = conditions.get(key)
            if message != previous.get(key):
                event = {'time': now, 'key': key, 'level': 'warning' if message else 'recovery', 'message': message or ('Recovered: ' + key)}
                data['events'].append(event)
                if n.flag(g, 'alert_syslog'):
                    n.command(['logger', '-t', 'nginx-stream-manager', '--', event['message']], check=False)
        data.update(conditions=conditions, events=data['events'][-100:], updated=now)
        n.atomic(path, json.dumps(data))
        return {'ok': True, 'checked': len(due)}

    def monitor(self, page=0, bounded=True):
        n = self.n
        data = read_json(n, n.RUN / 'monitor.json', {'backends': {}, 'events': [], 'conditions': {}, 'certificates': []})
        sections = n.read_config()
        configured = {b['.name']: b for b in sections if b['.type'] == 'backend'}
        rows = []
        for name, b in configured.items():
            cached = data['backends'].get(name, {})
            if cached.get('signature') != hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest():
                cached = {}
            row = dict(cached, id=name, address=b.get('address', ''), mode=b.get('health_check', 'off'), pool=b.get('pool', ''))
            if not n.flag(b, 'enabled', '1') or row['mode'] == 'off':
                row['status'] = 'disabled'
            elif not row.get('checked') or time.time() - row['checked'] > 2*int(next(s for s in sections if s['.type'] == 'global').get('health_interval', 60)) + 120:
                row['status'] = 'stale' if row.get('checked') else 'unknown'
            rows.append(row)
        data['backends'] = rows
        data['last_error'] = n.tail_file(n.RUN / 'last-error', 10, 1000)['text']
        data['snapshot_error'] = n.tail_file(n.RUN / 'snapshot-error', 5, 500)['text']
        data['archive_error'] = n.tail_file(n.RUN / 'archive-error', 5, 500)['text']
        data['active_alerts'] = len(data['conditions'])
        if bounded:
            page = n.integer(page, 0, 31, 'Page')
            data['pages'] = max(1, (max(len(rows), len(data['certificates']))+15)//16)
            data['backends'] = rows[page*16:(page+1)*16]
            data['certificates'] = data['certificates'][page*16:(page+1)*16]
            data['events'] = data['events'][-20:]
            data['conditions'] = dict(list(data['conditions'].items())[:20])
        data['ok'] = True
        return data

    def diagnostics(self):
        n = self.n
        checks = []
        try:
            listeners = []
            for protocol in ('tcp', 'udp'):
                for family in (4, 6):
                    path = Path('/proc/net/' + protocol + ('6' if family == 6 else ''))
                    if not path.exists():
                        continue
                    for line in n.bounded_read(path, 262144).decode().splitlines()[1:]:
                        fields = line.split()
                        if protocol == 'tcp' and fields[3] != '0A':
                            continue
                        if protocol == 'udp' and int(fields[2].replace(':', ''), 16):
                            continue
                        host, port = fields[1].split(':')
                        packed = b''.join(int(host[i:i+8], 16).to_bytes(4, sys.byteorder) for i in range(0, len(host), 8))
                        ip = socket.inet_ntop(socket.AF_INET if family == 4 else socket.AF_INET6, packed)
                        listeners.append('%s IPv%d %s:%d' % (protocol, family, ip, int(port, 16)))
            checks.append({'title': 'Listening TCP/UDP sockets', 'ok': True, 'text': '\n'.join(listeners[:128])[:3500] or 'No listening sockets found.'})
        except Exception as error:
            checks.append({'title': 'Listening TCP/UDP sockets', 'ok': False, 'text': str(error)[:500]})
        for title, args in [('Nginx build', [n.NGINX, '-V']),
                            ('IPv4 policy rules', ['ip', '-4', 'rule']), ('IPv6 policy rules', ['ip', '-6', 'rule']),
                            ('Return routes IPv4', ['ip', '-4', 'route', 'show', 'table', n.ROUTE_TABLE]),
                            ('Return routes IPv6', ['ip', '-6', 'route', 'show', 'table', n.ROUTE_TABLE])]:
            try:
                p = n.command(args, check=False, timeout=2)
                checks.append({'title': title, 'ok': p.returncode == 0, 'text': (p.stdout + p.stderr)[:3500]})
            except Exception as error:
                checks.append({'title': title, 'ok': False, 'text': str(error)[:500]})
        # Syntax probe uses the installed stream module, not merely nginx -V flags.
        for directive in ('ssl_preread on;', 'set_real_ip_from 127.0.0.1;', 'limit_conn_zone $binary_remote_addr zone=diag:32k;'):
            outside = directive.startswith('limit_conn_zone')
            config = ('load_module %s;\n' % n.MODULE if Path(n.MODULE).exists() else '') + 'error_log stderr; pid /tmp/nsm-diagnostic.pid; events {} stream { ' + (directive if outside else '') + ' server { listen 127.0.0.1:19999; ' + ('' if outside else directive) + ' proxy_pass 127.0.0.1:19998; } }'
            with tempfile.TemporaryDirectory(prefix='nsm-diag-') as td:
                p = Path(td) / 'nginx.conf'; p.write_text(config)
                result = n.command([n.NGINX, '-t', '-p', td+'/', '-c', str(p)], check=False, timeout=2)
                checks.append({'title': directive, 'ok': result.returncode == 0, 'text': result.stderr[-1200:]})
        for b in n.read_config():
            if b['.type'] == 'backend' and not b.get('address', '').startswith('unix:'):
                try:
                    n.address(b.get('address', ''))
                    continue
                except n.Invalid:
                    pass
                if len(checks) >= 13:
                    break
                # killable resolver, fixed program and validated argument; never shell interpolation.
                host = n.hostname(b.get('address', ''))
                try:
                    p = n.command([sys.executable, '-c', 'import socket,sys; print("\\n".join(sorted({x[4][0] for x in socket.getaddrinfo(sys.argv[1],None)})))', host], timeout=2)
                    checks.append({'title': 'DNS: ' + host, 'ok': True, 'text': p.stdout[:500]})
                except Exception as error:
                    checks.append({'title': 'DNS: ' + host, 'ok': False, 'text': str(error)[:500]})
        return {'ok': True, 'checks': checks, 'note': 'Router-side inspection cannot prove the backend return route or end-to-end forwarding. Nginx -t does not bind sockets. Missing return tables are expected when transparent mode is unused.'}

    def metrics(self):
        data = self.monitor(bounded=False)
        lines = ['# TYPE nsm_backend_up gauge', '# TYPE nsm_backend_probe_latency_seconds gauge', '# TYPE nsm_backend_probe_timestamp_seconds gauge']
        for b in data['backends'][:128]:
            label = '{backend=' + json.dumps(b['id']) + '}'
            if b.get('status') in ('up', 'down'):
                lines.append('nsm_backend_up%s %d' % (label, b['status'] == 'up'))
            if b.get('latency_ms') is not None:
                lines.append('nsm_backend_probe_latency_seconds%s %s' % (label, b['latency_ms']/1000))
            lines.append('nsm_backend_probe_timestamp_seconds%s %s' % (label, b.get('checked', 0)))
        lines += ['# TYPE nsm_alerts gauge', 'nsm_alerts %d' % len(data['conditions'])]
        # Rolling-window gauges, not monotonic counters; no log collection on scrape.
        traffic = read_json(self.n, self.n.RUN / 'traffic.json', {'series': {}})
        keys = sorted(traffic['series'])
        lines += ['# TYPE nsm_traffic_omitted_series gauge', 'nsm_traffic_omitted_series %d' % max(0, len(keys)-24),
                  '# TYPE nsm_backend_omitted gauge', 'nsm_backend_omitted %d' % max(0, len(data['backends'])-128)]
        for metric in ('sent_bytes', 'received_bytes', 'attempts', 'failures', 'retries'):
            lines.append('# TYPE nsm_24h_' + metric + ' gauge')
        exported = 0
        for key in keys[:24]:
            service, protocol, upstream = key.split('|')
            label = '{service=%s,protocol=%s,upstream=%s}' % tuple(json.dumps(v) for v in (service, protocol, upstream))
            total = [0]*9
            for stamp, cell in traffic['series'][key].items():
                if int(stamp) >= time.time()-86400:
                    for i, value in enumerate(cell):
                        total[i] += value
            addition = ['nsm_24h_%s%s %s' % (metric, label, total[i]) for i, metric in enumerate(('sent_bytes', 'received_bytes', 'attempts', 'failures', 'retries'))]
            if len(json.dumps('\n'.join(lines + addition)).encode()) > 51000:
                break
            lines += addition
            exported += 1
        lines = [('nsm_traffic_omitted_series %d' % (len(keys)-exported)) if l.startswith('nsm_traffic_omitted_series ') else l for l in lines]
        return {'ok': True, 'text': '\n'.join(lines) + '\n'}

if __name__ == '__main__':
    try:
        print(json.dumps(probe(json.loads(sys.argv[1]))))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)[:300]}))
