import copy
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_nsm import nsm, sample
from test_traffic import record, NOW
import nsm_ops


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sections = sample()
        for key, value in [('RUN', self.root/'run'), ('LOG', self.root/'log'), ('APP_CONFIG', self.root/'config')]:
            p = patch.object(nsm, key, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(nsm_ops, 'HISTORY', self.root/'snapshots'); p.start(); self.addCleanup(p.stop)
        nsm.RUN.mkdir(); nsm.LOG.mkdir()
        original = nsm.command
        def command(args, **kwargs):
            if args == ['uci', 'changes', nsm.CONFIG]:
                return subprocess.CompletedProcess(args, 0, '', '')
            return original(args, **kwargs)
        p = patch.object(nsm, 'command', side_effect=command); p.start(); self.addCleanup(p.stop)
        self.write()

    def write(self):
        from nsm_importer import uci_text
        nsm.APP_CONFIG.write_text(uci_text(self.sections))

    def test_sni_longest_wildcard_exact_and_fallback(self):
        self.sections[-1].update(tls_mode='preread', routing='sni')
        for i, match in enumerate(('*.example.com', '*.sub.example.com', 'app.sub.example.com')):
            self.sections.append({'.type': 'route', '.name': 'r'+str(i), 'service': 'application_tcp', 'match': match, 'pool': 'application'})
        self.write()
        self.assertIn('hostnames;', nsm.compile_config(self.sections)['nginx'])
        op = nsm.ops()
        for host, expected in [('APP.SUB.EXAMPLE.COM', 'app.sub.example.com'), ('x.sub.example.com', '*.sub.example.com'), ('x.example.com', '*.example.com'), ('example.com', 'default')]:
            self.assertEqual(op.route_test({'service': 'application_tcp', 'name': host})['match'], expected)
        for invalid in ('*', 'foo.*', '*.*.example.com', '*.example.com;return'):
            with self.assertRaises(nsm.Invalid):
                nsm_ops.sni_name(nsm, invalid)

    def test_health_config_validation_and_defaults(self):
        b = next(s for s in self.sections if s['.type'] == 'backend')
        b.update(health_check='tls')
        with self.assertRaises(nsm.Invalid): nsm.compile_config(self.sections)
        b.update(health_tls_name='app.example.com'); nsm.compile_config(self.sections)
        b.update(address='224.0.0.1')
        with self.assertRaises(nsm.Invalid): nsm.compile_config(self.sections)
        b.update(address='unix:/tmp/test', health_check='dns')
        with self.assertRaises(nsm.Invalid): nsm.compile_config(self.sections)
        self.sections[0]['history_path'] = '/etc/nsm'
        with self.assertRaises(nsm.Invalid): nsm.compile_config(self.sections)

    def test_snapshot_dedup_export_import_preview_and_stale_guard(self):
        op = nsm.ops()
        first = op.backup({'action': 'snapshot'})['id']
        self.assertEqual(first, op.backup({'action': 'snapshot'})['id'])
        self.assertEqual(len(op.backup({'action': 'list'})['items']), 1)
        exported = op.backup({'action': 'export'})['content']
        preview = op.backup({'action': 'import', 'content': exported})
        nsm.APP_CONFIG.write_text(nsm.APP_CONFIG.read_text() + '\n')
        with self.assertRaisesRegex(nsm.Invalid, 'changed'):
            op.backup({'action': 'commit', 'token': preview['token']})

    def test_duplicate_pool_includes_backends_service_disabled_with_routes(self):
        op = nsm.ops()
        preview = op.backup({'action': 'duplicate', 'source': 'application', 'name': 'copied_pool'})
        pending = json.loads((nsm.RUN/'config-preview.json').read_text())
        candidate = nsm.parse_uci(pending['config'])
        self.assertEqual(sum(s['.type'] == 'backend' and s['pool'] == 'copied_pool' for s in candidate), 2)
        preview = op.backup({'action': 'duplicate', 'source': 'application_tcp', 'name': 'copied_service'})
        candidate = nsm.parse_uci(json.loads((nsm.RUN/'config-preview.json').read_text())['config'])
        self.assertEqual(next(s for s in candidate if s['.name'] == 'copied_service')['enabled'], '0')
        with patch.object(nsm, 'pid', return_value=42):
            with self.assertRaisesRegex(nsm.Invalid, 'Stop'):
                op = nsm.ops(); op.backup({'action': 'commit', 'token': preview['token']})
        with patch.object(nsm, 'preflight'):
            result = nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        self.assertTrue(result['ok'])
        self.assertIn('copied_service', nsm.APP_CONFIG.read_text())
        self.assertEqual(len(list(nsm_ops.HISTORY.glob('*.json'))), 1)

    def test_rename_pool_updates_all_references_without_changing_settings(self):
        self.sections[-1].update(tls_mode='preread', routing='sni')
        self.sections.append({'.type': 'route', '.name': 'rule', 'service': 'application_tcp', 'pool': 'application', 'match': '*.example.com'})
        self.sections.append({'.type': 'service', '.name': 'disabled', 'enabled': '0', 'pool': 'application'})
        self.sections.append({'.type': 'backend', '.name': 'disabled_backend', 'enabled': '0', 'pool': 'application'})
        self.write()
        before = nsm.APP_CONFIG.read_bytes()
        preview = nsm.ops().backup({'action': 'rename', 'source': 'application', 'name': 'renamed_pool'})
        self.assertEqual(nsm.APP_CONFIG.read_bytes(), before)
        candidate = nsm.parse_uci(json.loads((nsm.RUN/'config-preview.json').read_text())['config'])
        expected = copy.deepcopy(self.sections)
        for s in expected:
            if s['.name'] == 'application': s['.name'] = 'renamed_pool'
            if s.get('pool') == 'application': s['pool'] = 'renamed_pool'
        self.assertEqual(candidate, expected)
        with patch.object(nsm, 'preflight') as preflight:
            nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        preflight.assert_called_once()
        self.assertEqual(nsm.read_config(), expected)
        self.assertEqual(json.loads(next(nsm_ops.HISTORY.glob('*.json')).read_text())['config'].encode(), before)

    def test_rename_service_updates_routes_and_preserves_history(self):
        self.sections[-1].update(tls_mode='preread', routing='sni')
        self.sections.append({'.type': 'route', '.name': 'rule', 'service': 'application_tcp', 'pool': 'application', 'match': '*.example.com'})
        self.sections.append({'.type': 'route', '.name': 'disabled_rule', 'enabled': '0', 'service': 'application_tcp', 'pool': 'application', 'match': 'unused.example.com'})
        self.write()
        history = nsm.RUN/'traffic.json'; history.write_text('{"series":{}}')
        preview = nsm.ops().backup({'action': 'rename', 'source': 'application_tcp', 'name': 'renamed_service'})
        expected = copy.deepcopy(self.sections)
        for s in expected:
            if s['.name'] == 'application_tcp': s['.name'] = 'renamed_service'
            if s.get('service') == 'application_tcp': s['service'] = 'renamed_service'
        with patch.object(nsm, 'preflight'):
            nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        self.assertEqual(nsm.read_config(), expected)
        self.assertEqual(history.read_text(), '{"series":{}}')
        self.assertEqual(nsm.ops().route_test({'service': 'renamed_service', 'name': 'app.example.com'})['pool'], 'application')

    def test_rename_rejects_collisions_invalid_ids_and_wrong_section_types(self):
        before = nsm.APP_CONFIG.read_bytes()
        for source, name in [('application', 'application'), ('application', 'node_a'), ('application', 'main'),
                             ('application', ''), ('application', 'bad-name'), ('application', 'x'*49),
                             ('application_tcp', 'manager'), ('missing', 'new'), ('main', 'new')]:
            with self.subTest(source=source, name=name), self.assertRaises(nsm.Invalid):
                nsm.ops().backup({'action': 'rename', 'source': source, 'name': name})
        self.assertEqual(nsm.APP_CONFIG.read_bytes(), before)

    def test_rename_commit_rejects_running_manager_and_stale_configuration(self):
        preview = nsm.ops().backup({'action': 'rename', 'source': 'application', 'name': 'new_pool'})
        before = nsm.APP_CONFIG.read_bytes()
        with patch.object(nsm, 'pid', return_value=42), self.assertRaisesRegex(nsm.Invalid, 'Stop'):
            nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        self.assertEqual(nsm.APP_CONFIG.read_bytes(), before)
        nsm.APP_CONFIG.write_bytes(before + b'\n')
        with self.assertRaisesRegex(nsm.Invalid, 'changed'):
            nsm.ops().backup({'action': 'commit', 'token': preview['token']})

    def test_rename_imported_backend_changes_only_its_id_and_exports_new_id(self):
        backend = next(s for s in self.sections if s['.type'] == 'backend')
        backend.update({'.name': 'imp_deadbeef_pool_1_b1', 'health_check': 'tcp'})
        self.write()
        listing = nsm.ops().backup({'action': 'list'})['sections']
        item = next(s for s in listing if s['id'] == backend['.name'])
        self.assertEqual((item['type'], item['pool'], item['address'], item['port']),
                         ('backend', backend['pool'], backend['address'], backend['port']))
        before = nsm.APP_CONFIG.read_bytes()
        history = nsm.RUN/'traffic.json'; history.write_text('{"series":{}}')
        preview = nsm.ops().backup({'action': 'rename', 'source': backend['.name'], 'name': 'primary_backend'})
        self.assertEqual(nsm.APP_CONFIG.read_bytes(), before)
        with patch.object(nsm, 'preflight'):
            nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        expected = copy.deepcopy(self.sections)
        next(s for s in expected if s['.name'] == backend['.name'])['.name'] = 'primary_backend'
        self.assertEqual(nsm.read_config(), expected)
        exported = json.loads(nsm.ops().backup({'action': 'export'})['content'])['config']
        self.assertNotIn(backend['.name'], exported)
        self.assertEqual(nsm.parse_uci(exported), expected)
        self.assertEqual(history.read_text(), '{"series":{}}')
        saved = json.loads(next(nsm_ops.HISTORY.glob('*.json')).read_text())
        self.assertEqual(saved['config'].encode(), before)
        monitor = nsm.ops().monitor()
        self.assertEqual(next(b for b in monitor['backends'] if b['id'] == 'primary_backend')['status'], 'unknown')

    def test_backend_rename_rejects_bad_names_and_duplicate_backend_action(self):
        before = nsm.APP_CONFIG.read_bytes()
        for name in ('node_a', 'node_b', 'application', 'application_tcp', '', 'bad-id', 'x'*49):
            with self.subTest(name=name), self.assertRaises(nsm.Invalid):
                nsm.ops().backup({'action': 'rename', 'source': 'node_a', 'name': name})
        with self.assertRaises(nsm.Invalid):
            nsm.ops().backup({'action': 'duplicate', 'source': 'node_a', 'name': 'new_backend'})
        self.assertEqual(nsm.APP_CONFIG.read_bytes(), before)

    def test_disabled_backend_can_be_renamed_without_becoming_enabled(self):
        backend = next(s for s in self.sections if s['.type'] == 'backend')
        backend['enabled'] = '0'; self.write()
        preview = nsm.ops().backup({'action': 'rename', 'source': backend['.name'], 'name': 'offline_backend'})
        with patch.object(nsm, 'preflight'):
            nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        renamed = next(s for s in nsm.read_config() if s['.name'] == 'offline_backend')
        self.assertEqual(renamed, dict(backend, **{'.name': 'offline_backend'}))

    def test_invalid_import_does_not_modify_config(self):
        before = nsm.APP_CONFIG.read_bytes()
        for content in ('{}', '{"format":"nsm-config-1","config":"evil command"}', 'x'*65537):
            with self.assertRaises((nsm.Invalid, ValueError)):
                nsm.ops().backup({'action': 'import', 'content': content})
        self.assertEqual(before, nsm.APP_CONFIG.read_bytes())

    def test_restore_failed_validation_keeps_original(self):
        preview = nsm.ops().backup({'action': 'duplicate', 'source': 'application_tcp', 'name': 'copy'})
        before = nsm.APP_CONFIG.read_bytes()
        with patch.object(nsm, 'preflight', side_effect=nsm.Invalid('bad cert')):
            with self.assertRaises(nsm.Invalid):
                nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        self.assertEqual(before, nsm.APP_CONFIG.read_bytes())

    def test_health_hysteresis_recovery_dedup_and_stale(self):
        b = next(s for s in self.sections if s['.type'] == 'backend')
        b['health_check'] = 'tcp'; self.sections[0].update(health_interval='30', health_fall='2', health_rise='2'); self.write()
        with patch.object(nsm_ops.Operations, 'run_probe', return_value={'ok': False, 'error': 'refused'}), patch('nsm_ops.time.time', return_value=NOW):
            nsm.ops().tick()
            self.assertEqual(nsm.ops().monitor()['backends'][0]['status'], 'unknown')
        with patch.object(nsm_ops.Operations, 'run_probe', return_value={'ok': False, 'error': 'refused'}), patch('nsm_ops.time.time', return_value=NOW+31):
            nsm.ops().tick()
            self.assertEqual(nsm.ops().monitor()['backends'][0]['status'], 'down')
            self.assertEqual(len(nsm.ops().monitor()['events']), 1)
        for offset in (62, 93):
            with patch.object(nsm_ops.Operations, 'run_probe', return_value={'ok': True, 'latency_ms': 1}), patch('nsm_ops.time.time', return_value=NOW+offset):
                nsm.ops().tick()
        with patch('nsm_ops.time.time', return_value=NOW+94):
            status = nsm.ops().monitor()
            self.assertEqual(status['backends'][0]['status'], 'up')
            self.assertEqual(status['events'][-1]['level'], 'recovery')
            self.assertIn('nsm_backend_up{backend="node_a"} 1', nsm.ops().metrics()['text'])
        with patch('nsm_ops.time.time', return_value=NOW+400):
            self.assertEqual(nsm.ops().monitor()['backends'][0]['status'], 'stale')
        b['address'] = '192.0.2.123'; self.write()
        self.assertEqual(nsm.ops().monitor()['backends'][0]['status'], 'unknown')

    def test_real_tcp_and_dns_probe_and_invalid_reply(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0)); server.listen()
            result = nsm_ops.probe({'address': '127.0.0.1', 'port': server.getsockname()[1], 'health_check': 'tcp'})
            self.assertTrue(result['ok'])
        for valid in (True, False):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
                server.bind(('127.0.0.1', 0)); server.settimeout(2)
                def reply():
                    data, peer = server.recvfrom(1024)
                    server.sendto(data[:2] + b'\x81' + (b'\x80' if valid else b'\x82') + data[4:], peer)
                worker = threading.Thread(target=reply); worker.start()
                args = {'address': '127.0.0.1', 'port': server.getsockname()[1], 'health_check': 'dns', 'health_dns_name': 'example.com'}
                if valid:
                    self.assertTrue(nsm_ops.probe(args)['ok'])
                else:
                    with self.assertRaises(ValueError): nsm_ops.probe(args)
                worker.join(3)

    def test_real_verified_tls_and_certificate_metadata(self):
        cert, key = self.root/'cert.pem', self.root/'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '2', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0)); server.listen(); server.settimeout(3)
            def accept():
                for _ in range(2):
                    conn, _ = server.accept()
                    try:
                        with context.wrap_socket(conn, server_side=True): pass
                    except ssl.SSLError: conn.close()
            worker = threading.Thread(target=accept); worker.start()
            args = {'address': '127.0.0.1', 'port': server.getsockname()[1], 'health_check': 'tls', 'health_tls_name': 'localhost', 'health_ca': str(cert)}
            self.assertTrue(nsm_ops.probe(args)['ok'])
            args['health_tls_name'] = 'wrong.example'
            with self.assertRaises(ssl.SSLError): nsm_ops.probe(args)
            worker.join(4)
        self.sections[-1]['ssl_certificate'] = str(cert)
        metadata = nsm.ops().certificates(self.sections)[0]
        self.assertTrue(metadata['ok']); self.assertIn('DNS:localhost', metadata['details'])
        self.assertNotIn('PRIVATE KEY', json.dumps(metadata))

    def test_extended_traffic_retries_errors_and_timing_alignment(self):
        raw = record(service='application_tcp', upstream='192.0.2.1:53, 192.0.2.2:53', sent='0, 30', received='0, 40', connect='-, 0.025', session='1.234', status='200')
        (nsm.LOG/'traffic.log').write_bytes(raw)
        with nsm.locked(): data = nsm.collect_traffic(NOW)
        cells = [next(iter(v.values())) for v in data['series'].values()]
        self.assertEqual(cells[0], [0, 0, 1, 1, 1, 0, 0, 0, 0])
        self.assertEqual(cells[1], [30, 40, 1, 0, 0, 25, 1, 1234, 1])
        with self.assertRaises(nsm.Invalid):
            nsm.traffic_record(record(connect='nan', session='2'), NOW, detailed=True)

    def test_missing_history_mount_and_symlink_rejected(self):
        with patch('nsm_ops.os.path.ismount', return_value=False):
            with self.assertRaisesRegex(nsm.Invalid, 'not mounted'):
                nsm_ops.archive_path(nsm, {'history_path': '/mnt/absent/nsm'})
        with self.assertRaises(nsm.Invalid): nsm_ops.archive_path(nsm, {'history_path': '/etc/nsm'})

    def test_archive_checkpoint_restore_and_retention(self):
        path = self.root/'archive.json'
        with patch.object(nsm_ops, 'archive_path', return_value=path), patch.object(nsm_ops, 'archive_directory', side_effect=lambda *a: contextlib.nullcontext(self.root)):
            (nsm.LOG/'traffic.log').write_bytes(record(service='application_tcp'))
            with nsm.locked(): data = nsm.collect_traffic(NOW)
            first = path.read_bytes()
            with nsm.locked(): nsm.collect_traffic(NOW+1)
            self.assertEqual(first, path.read_bytes())
            (nsm.RUN/'traffic.json').unlink()
            with nsm.locked(): restored = nsm.collect_traffic(NOW+2)
            self.assertEqual(data['series'], restored['series'])
            with nsm.locked(): nsm.collect_traffic(NOW+8*86400)
            self.assertEqual(json.loads(path.read_text())['hours'], {})

    def test_archive_descriptor_write_and_mount_loss(self):
        mount = self.root/'mounted'; mount.mkdir()
        with patch('nsm_ops.os.path.ismount', side_effect=lambda p: Path(p) == mount):
            with nsm_ops.archive_directory(nsm, mount/'nsm', True) as directory:
                nsm.atomic(directory/'history.json', '{}')
        self.assertEqual((mount/'nsm/history.json').read_text(), '{}')
        with patch('nsm_ops.os.path.ismount', return_value=False):
            with self.assertRaises(nsm.Invalid):
                with nsm_ops.archive_directory(nsm, mount/'nsm', True): pass

    def test_commit_rejects_pending_control_and_expired_preview(self):
        preview = nsm.ops().backup({'action': 'duplicate', 'source': 'application_tcp', 'name': 'copy'})
        with (nsm.RUN/'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(nsm.Invalid, 'service action'):
                nsm.ops().backup({'action': 'commit', 'token': preview['token']})
        with patch('nsm_ops.time.time', return_value=time.time()+601):
            with self.assertRaisesRegex(nsm.Invalid, 'expired'):
                nsm.ops().backup({'action': 'commit', 'token': preview['token']})

    def test_working_snapshot_uses_applied_not_later_committed_config(self):
        applied = nsm.APP_CONFIG.read_text()
        (nsm.RUN/'state.json').write_text(json.dumps({'source_config': applied}))
        self.sections[0]['workers'] = '3'; self.write()
        nsm.working_snapshot()
        saved = json.loads(next(nsm_ops.HISTORY.glob('*.json')).read_text())
        self.assertEqual(saved['config'], applied)
        self.assertEqual(saved['label'], 'working')

    def test_metrics_do_not_collect_or_include_client_identifiers(self):
        (nsm.RUN/'traffic.json').write_text(json.dumps({'series': {'svc|TCP|192.0.2.1:9': {str(int(time.time())): [3, 4, 1, 0, 0]}}}))
        result = nsm.ops().metrics()['text']
        self.assertIn('nsm_24h_sent_bytes{service="svc",protocol="TCP",upstream="192.0.2.1:9"} 3', result)
        self.assertNotIn('client', result)
