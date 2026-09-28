"""0.2.1 regressions. Runtime commands are mocked in this file."""
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_nsm import nsm, sample
from test_import import imp, CURRENT, SIMPLE
import test_import


class ImportRegressionTests(unittest.TestCase):
    def test_duplicate_singletons_blocked(self):
        for directive in ('proxy_timeout 1s;', 'proxy_bind off;', 'proxy_protocol off;'):
            with self.subTest(directive=directive):
                self.assertTrue(imp.plan(SIMPLE.replace('listen 9000;', 'listen 9000; ' + directive * 2), CURRENT)['blockers'])

    def test_invalid_stream_context_blocked(self):
        for directive in ('listen 1234;', 'proxy_pass 192.0.2.1:90;'):
            self.assertTrue(imp.plan(SIMPLE.replace('stream {', 'stream { ' + directive), CURRENT)['blockers'])

    def test_conditional_log_not_silently_made_unconditional(self):
        self.assertTrue(imp.plan(SIMPLE.replace('listen 9000;', 'listen 9000; access_log /tmp/a nsm if=$flag;'), CURRENT)['blockers'])

    def test_duplicate_balancing_blocked(self):
        source = 'stream { upstream p { server 192.0.2.1:9001; least_conn; hash $remote_addr consistent; } server {listen 9000; proxy_pass p;} }'
        self.assertTrue(imp.plan(source, CURRENT)['blockers'])

    def test_server_acl_and_bind_override_inherited_settings(self):
        source = SIMPLE.replace('stream {', 'stream { proxy_bind $remote_addr transparent; allow 192.0.2.0/24; deny all;')
        source = source.replace('listen 9000;', 'listen 9000; proxy_bind off; allow all;')
        p = imp.plan(source, CURRENT)
        self.assertFalse(p['blockers'])
        s = next(s for s in p['sections'] if s['.type'] == 'service')
        self.assertEqual(s['transparent'], '0')
        self.assertNotIn('allow_source', s)

    def test_saved_config_is_read_under_unique_package_name(self):
        raw = b"config global 'main'\n option enabled '0'\n"
        def export(args):
            self.assertEqual(args[:2], ['uci', '-c'])
            name = args[-1]
            self.assertNotEqual(name, nsm.CONFIG)
            self.assertEqual((Path(args[2]) / name).read_bytes(), raw)
            return SimpleNamespace(stdout=raw.decode())
        with patch.object(nsm, 'command', side_effect=export):
            self.assertEqual(nsm.read_config(raw), CURRENT)


class ConcurrentImportTests(unittest.TestCase):
    setUp = test_import.WorkflowTests.setUp
    tearDown = test_import.WorkflowTests.tearDown
    def test_change_during_backup_preserves_intervening_edit(self):
        token = nsm.import_preview()['token']
        original = nsm.atomic
        changed = self.app.read_bytes() + b'# concurrent edit\n'
        def write(path, value, mode=0o600):
            original(path, value, mode)
            if Path(path).parent == self.root / 'backups':
                self.app.write_bytes(changed)
        with patch.object(nsm, 'atomic', side_effect=write):
            with self.assertRaisesRegex(nsm.Invalid, 'changed while importing'):
                nsm.import_commit(token)
        self.assertEqual(self.app.read_bytes(), changed)

    def test_backup_preserves_crlf_bytes(self):
        self.app.write_bytes(self.app.read_bytes().replace(b'\n', b'\r\n'))
        original = self.app.read_bytes()
        result = nsm.import_commit(nsm.import_preview()['token'])
        self.assertEqual(Path(result['backup']).read_bytes(), original)


class RecoveryRegressionTests(unittest.TestCase):
    def test_failed_firewall_reload_reloads_previous_snippet(self):
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)):
            path = Path(td) / 'fw4-input.nft'; path.write_text('# old\n')
            seen = []
            def command(args, **kwargs):
                seen.append((args, path.read_text()))
                if len(seen) == 2: raise nsm.Invalid('reload failed after mutation')
            with patch.object(nsm, 'command', side_effect=command):
                with self.assertRaisesRegex(nsm.Invalid, 'reload failed'):
                    nsm.firewall_apply('# new\n')
            self.assertEqual(len(seen), 4)
            self.assertEqual(seen[-1], (['/etc/init.d/firewall', 'reload'], '# old\n'))

    def test_force_firewall_restore_does_not_skip_identical_file(self):
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), patch.object(nsm, 'command') as command:
            nsm.atomic(Path(td) / 'fw4-input.nft', '# old')
            nsm.firewall_apply('# old', force=True)
            self.assertEqual(command.call_count, 2)

    def test_priority_collision_checked_before_route_mutation(self):
        with patch.object(nsm, 'command', return_value=SimpleNamespace(stdout='10100: from all lookup 99\n')) as command:
            with self.assertRaisesRegex(nsm.Invalid, 'collision'): nsm.route_add(4)
            self.assertEqual(command.call_count, 1)

    def test_rule_prefix_and_extra_selectors_not_owned(self):
        base = '10100: from all fwmark 0x40000000/0x40000000 lookup 20100'
        self.assertTrue(nsm.own_rule(base))
        for line in (base + '0', base + ' suppress_prefixlength 0', base.replace('from all', 'from 192.0.2.0/24')):
            self.assertFalse(nsm.own_rule(line))

    def test_foreign_route_not_replaced_or_removed(self):
        foreign = {'dst': 'default', 'gateway': '192.0.2.1', 'dev': 'eth0'}
        with patch.object(nsm, 'routing_entries', return_value=[foreign]), patch.object(nsm, 'command', return_value=SimpleNamespace(stdout='')) as command:
            with self.assertRaisesRegex(nsm.Invalid, 'collision'): nsm.route_add(4)
            nsm.route_del(4)
            self.assertTrue(all(c.args[0][-2:] == ['rule', 'show'] for c in command.call_args_list))

    def test_live_process_prevents_cleanup(self):
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), patch.object(nsm, 'pid', return_value=100), patch.object(nsm, 'command') as command:
            with self.assertRaisesRegex(nsm.Invalid, 'still running'): nsm.cleanup()
            command.assert_not_called()

    def test_recovery_failure_leaves_cleanup_journal(self):
        c = nsm.compile_config(sample()); c['enabled'] = True
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), patch.object(nsm, 'pid', return_value=None), patch.object(nsm, 'prepare', return_value=c), patch.object(nsm, 'network', side_effect=nsm.Invalid('network failed')), patch.object(nsm, 'firewall_apply', side_effect=nsm.Invalid('restore failed')):
            with self.assertRaisesRegex(nsm.Invalid, 'recovery also failed'): nsm.apply()
            self.assertTrue((Path(td) / 'pending.json').exists())
            with self.assertRaisesRegex(nsm.Invalid, 'interrupted apply'): nsm.apply()

    def test_rollback_reload_is_confirmed(self):
        c = nsm.compile_config(sample()); c['enabled'] = True
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), patch.object(nsm, 'pid', return_value=100), patch.object(nsm, 'prepare', return_value=c), patch.object(nsm, 'network'), patch.object(nsm, 'workers', return_value={101}), patch.object(nsm.os, 'kill'), patch.object(nsm, 'confirm_reload', side_effect=[nsm.Invalid('rejected'), None]) as confirm:
            nsm.atomic(Path(td) / 'state.json', json.dumps(c))
            nsm.atomic(Path(td) / 'nginx.conf', c['nginx'])
            with self.assertRaisesRegex(nsm.Invalid, 'rejected'): nsm.apply(reload=True)
            self.assertEqual(confirm.call_count, 2)
            self.assertFalse((Path(td) / 'pending.json').exists())


class RPCRegressionTests(unittest.TestCase):
    def test_wrong_types_and_unknown_fields_are_rejected(self):
        for method, args in [('status', []), ('status', {'path': '/etc/shadow'}), ('logs', {'lines': True}), ('control', {'action': []}), ('import_commit', {'token': 1})]:
            with self.subTest(method=method, args=args), self.assertRaises(nsm.Invalid): nsm.rpc(method, args)

    def test_oversized_rpc_input_returns_json_error(self):
        with patch.object(nsm.sys, 'argv', ['nsm', 'call', 'status']), patch.object(nsm.sys, 'stdin', io.StringIO(' ' * 8193)), patch.object(nsm.sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertEqual(nsm.main(), 0)
            self.assertIn('exceeds', json.loads(output.getvalue())['error'])

    def test_control_queues_detached_job_without_waiting_on_init(self):
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), patch.object(nsm.subprocess, 'Popen') as launch, patch.object(nsm, 'command') as command:
            result = nsm.rpc('control', {'action': 'reload'})
            self.assertTrue(result['ok'])
            self.assertTrue(launch.call_args.kwargs['start_new_session'])
            self.assertTrue(json.loads((Path(td) / 'operation.json').read_text())['pending'])
            command.assert_not_called()


if __name__ == '__main__': unittest.main()
