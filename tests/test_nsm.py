import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nsm', ROOT / 'packages/nginx-stream-manager/files/nsm.py')
nsm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nsm)

def sample():
    return nsm.parse_uci((ROOT / 'examples/generic.uci').read_text())

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.sections = sample()
        self.s = self.sections[-1]

    def compile(self):
        return nsm.compile_config(self.sections)

    def transparent(self):
        self.s.update(transparent='1', return_route_ack='1', return_interface=['br-lan'])

    def test_tcp_pool_and_passive_failover(self):
        c = self.compile()
        self.assertIn('least_conn;', c['nginx'])
        self.assertIn('192.0.2.10:9000 weight=2 max_fails=2 fail_timeout=10s', c['nginx'])
        self.assertIn('allow 192.0.2.0/24;', c['nginx'])
        self.assertIn('deny all;', c['nginx'])
        self.assertNotIn('proxy_bind', c['nginx'])
        self.assertEqual(c['families'], [])

    def test_udp_does_not_assume_one_response(self):
        self.s['protocol'] = 'udp'
        c = self.compile()
        self.assertIn('udp reuseport;', c['nginx'])
        self.assertNotIn('proxy_responses', c['nginx'])
        self.assertNotIn('proxy_half_close', c['nginx'])

    def test_udp_zero_is_distinct_from_empty(self):
        self.s.update(protocol='udp', udp_responses='0')
        self.assertIn('proxy_responses 0;', self.compile()['nginx'])

    def test_both_protocols(self):
        self.s['protocol'] = 'both'
        c = self.compile()['nginx']
        self.assertIn('listen 0.0.0.0:19000 reuseport;', c)
        self.assertIn('listen 0.0.0.0:19000 udp reuseport;', c)

    def test_transparent_return_scoped_and_other_marks_preserved(self):
        self.transparent()
        c = self.compile()
        self.assertIn('proxy_bind $remote_addr transparent;', c['nginx'])
        self.assertIn('iifname "br-lan" ip saddr 192.0.2.10 tcp sport 9000 socket transparent 1', c['nft'])
        self.assertIn('meta mark set meta mark | 0x40000000', c['nft'])
        self.assertNotIn('ct mark', c['nft'])
        self.assertNotIn('tproxy to', c['nft'])
        self.assertEqual(c['families'], [4])

    def test_return_route_ack_required(self):
        self.transparent(); self.s['return_route_ack'] = '0'
        with self.assertRaisesRegex(nsm.Invalid, 'Confirm backend'):
            self.compile()

    def test_return_device_required(self):
        self.transparent(); self.s['return_interface'] = []
        with self.assertRaisesRegex(nsm.Invalid, 'return interface'):
            self.compile()

    def test_ipv6_transparent(self):
        self.transparent(); self.s.update(listen_address='::', allow_source=['2001:db8::/32'])
        self.sections[2]['address'] = '2001:db8::10'
        self.sections[3]['address'] = '2001:db8::11'
        c = self.compile()
        self.assertIn('listen [::]:19000 reuseport ipv6only=on;', c['nginx'])
        self.assertIn('server [2001:db8::10]:9000', c['nginx'])
        self.assertIn('ip6 saddr 2001:db8::10', c['nft'])
        self.assertEqual(c['families'], [6])

    def test_cross_family_transparent_rejected(self):
        self.transparent(); self.sections[2]['address'] = '2001:db8::10'
        with self.assertRaisesRegex(nsm.Invalid, 'same IP family'):
            self.compile()

    def test_wildcard_specific_listener_overlap(self):
        other = dict(self.s, **{'.name': 'other', 'listen_address': '192.0.2.1'})
        self.sections.append(other)
        c = self.compile()['nginx']
        self.assertIn('listen 192.0.2.1:19000 reuseport bind;', c)
        self.assertIn('listen 0.0.0.0:19000 reuseport;', c)

    def test_separate_specific_ips_allowed(self):
        self.s['listen_address'] = '192.0.2.1'
        self.sections.append(dict(self.s, **{'.name': 'other', 'listen_address': '192.0.2.2'}))
        self.assertEqual(len(self.compile()['services']), 2)

    def test_invalid_ip_injection(self):
        self.sections[2]['address'] = '192.0.2.10; include /etc/passwd;'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_interface_injection(self):
        self.transparent(); self.s['return_interface'] = ['br-lan"; flush ruleset;']
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_section_injection(self):
        self.s['.name'] = '../../secret'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_invalid_port(self):
        for port in ('0', '65536', '-1', '53;foo'):
            self.s['listen_port'] = port
            with self.assertRaises(nsm.Invalid): self.compile()

    def test_hash_backup_invalid(self):
        self.sections[1]['method'] = 'hash_ip'
        self.sections[3]['backup'] = '1'
        with self.assertRaisesRegex(nsm.Invalid, 'backup'): self.compile()

    def test_missing_pool(self):
        self.s['pool'] = 'missing'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_no_primary(self):
        self.sections[2]['down'] = '1'; self.sections[3]['backup'] = '1'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_proxy_protocol_rejects_udp(self):
        self.s.update(protocol='udp', proxy_protocol='1')
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_loopback_transparency_rejected(self):
        self.transparent(); self.sections[2]['address'] = '127.0.0.1'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_self_loop_rejected(self):
        self.s.update(listen_address='192.0.2.10', listen_port='9000')
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_duplicate_names(self):
        self.sections.append(dict(self.sections[1]))
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_safe_disabled_initial_config(self):
        sections = nsm.parse_uci((ROOT / 'packages/nginx-stream-manager/files/nginx_stream_manager').read_text())
        self.assertFalse(nsm.compile_config(sections)['enabled'])

    def test_global_enable_requires_service(self):
        with self.assertRaises(nsm.Invalid):
            nsm.compile_config([{'.name':'main', '.type':'global', 'enabled':'1'}])

    def test_default_protocol(self):
        del self.s['protocol']
        self.assertEqual(self.compile()['services'][0]['protocol'], 'tcp')

    def test_quotes_in_display_name_do_not_enter_config(self):
        self.s['name'] = '"; include /etc/passwd;'
        self.assertNotIn('/etc/passwd', self.compile()['nginx'])

class LogTests(unittest.TestCase):
    def test_large_log_last_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'log'
            p.write_text('old\n' * 100000 + 'one\ntwo\nthree\n')
            self.assertEqual(nsm.tail_file(p, 2)['text'], 'two\nthree')

    def test_empty_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'log'
            self.assertEqual(nsm.tail_file(p)['text'], '')
            p.touch()
            self.assertEqual(nsm.tail_file(p)['text'], '')

    def test_huge_line_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'log'; p.write_bytes(b'x' * 1000000)
            result = nsm.tail_file(p)
            self.assertLessEqual(len(result['text']), 8000)
            self.assertTrue(result['byte_limited'])

    def test_invalid_utf8_and_no_final_newline(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'log'; p.write_bytes(b'old\n\xffnew')
            self.assertEqual(nsm.tail_file(p, 1)['text'], '\ufffdnew')

    def test_log_path_traversal_rejected(self):
        with patch.object(nsm, 'read_config', return_value=sample()):
            with self.assertRaises(nsm.Invalid): nsm.logs({'service': '../../etc/shadow'})

class ApplyTests(unittest.TestCase):
    def test_failed_preflight_never_mutates_network(self):
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), \
             patch.object(nsm, 'prepare', side_effect=nsm.Invalid('bad syntax')), \
             patch.object(nsm, 'network') as network:
            with self.assertRaises(nsm.Invalid): nsm.apply()
            network.assert_not_called()

    def test_failed_network_restores_previous_config(self):
        old = nsm.compile_config(sample()); old['enabled'] = True
        new = copy.deepcopy(old); new['nginx'] = '# new config'
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), \
             patch.object(nsm, 'prepare', return_value=new), patch.object(nsm, 'pid', return_value=None), \
             patch.object(nsm, 'network', side_effect=[nsm.Invalid('nft failed'), None]) as network:
            nsm.atomic(Path(td) / 'state.json', json.dumps(old))
            nsm.atomic(Path(td) / 'nginx.conf', old['nginx'])
            with self.assertRaisesRegex(nsm.Invalid, 'nft failed'): nsm.apply()
            self.assertEqual((Path(td) / 'nginx.conf').read_text(), old['nginx'])
            self.assertEqual(network.call_count, 2)
            self.assertEqual(nsm.state(), old)

    def test_first_apply_failure_removes_new_routes(self):
        c = nsm.compile_config(sample()); c.update(enabled=True, families=[4, 6])
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), \
             patch.object(nsm, 'prepare', return_value=c), patch.object(nsm, 'pid', return_value=None), \
             patch.object(nsm, 'network', side_effect=nsm.Invalid('failed')), \
             patch.object(nsm, 'command'), patch.object(nsm, 'delete_nft_table'), patch.object(nsm, 'route_del') as delete:
            with self.assertRaises(nsm.Invalid): nsm.apply()
            self.assertEqual([c.args[0] for c in delete.call_args_list], [4, 6])
            self.assertIsNone(nsm.state())

    def test_control_action_whitelist(self):
        with self.assertRaises(nsm.Invalid): nsm.rpc('control', {'action': 'start; reboot'})


class FirewallTests(unittest.TestCase):
    def test_firewall_permit_is_interface_family_port_and_cidr_scoped(self):
        sections = sample()
        sections[-1].update(open_firewall='1', ingress_interface=['br-lan'], protocol='both')
        fw = nsm.compile_config(sections)['fw4']
        self.assertIn('iifname { "br-lan" } meta nfproto ipv4 tcp dport 19000 ip saddr { 192.0.2.0/24 }', fw)
        self.assertIn('udp dport 19000', fw)
        self.assertNotIn('flush', fw)

    def test_no_implicit_firewall_opening(self):
        self.assertEqual(nsm.compile_config(sample())['fw4'].strip(), '')

    def test_firewall_requires_interface(self):
        sections = sample(); sections[-1]['open_firewall'] = '1'
        with self.assertRaisesRegex(nsm.Invalid, 'explicit client ingress'): nsm.compile_config(sections)

    def test_firewall_failed_check_restores_snippet(self):
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), \
             patch.object(nsm, 'command', side_effect=nsm.Invalid('invalid firewall')):
            p = Path(td) / 'fw4-input.nft'; p.write_text('# old\n')
            with self.assertRaises(nsm.Invalid): nsm.firewall_apply('# new\n')
            self.assertEqual(p.read_text(), '# old\n')

class ReloadTests(unittest.TestCase):
    def test_new_worker_generation_confirms_reload(self):
        with patch.object(nsm, 'pid', return_value=100), patch.object(nsm, 'workers', return_value={101, 102}):
            nsm.confirm_reload(100, {101})

    def test_master_exit_rejects_reload(self):
        with patch.object(nsm, 'pid', return_value=None):
            with self.assertRaisesRegex(nsm.Invalid, 'master exited'): nsm.confirm_reload(100, {101})

    def test_rejected_reload_restores_old_config_and_network(self):
        old = nsm.compile_config(sample()); old['enabled'] = True
        new = copy.deepcopy(old); new['nginx'] = '# candidate'
        with tempfile.TemporaryDirectory() as td, patch.object(nsm, 'RUN', Path(td)), \
             patch.object(nsm, 'prepare', return_value=new), patch.object(nsm, 'pid', return_value=100), \
             patch.object(nsm, 'workers', return_value={101}), patch.object(nsm, 'network') as network, \
             patch.object(nsm.os, 'kill') as kill, patch.object(nsm, 'confirm_reload', side_effect=nsm.Invalid('bind failed')):
            nsm.atomic(Path(td) / 'nginx.conf', old['nginx'])
            nsm.atomic(Path(td) / 'state.json', json.dumps(old))
            with self.assertRaisesRegex(nsm.Invalid, 'bind failed'): nsm.apply(reload=True)
            self.assertEqual(nsm.state(), old)
            self.assertEqual((Path(td) / 'nginx.conf').read_text(), old['nginx'])
            self.assertEqual(network.call_count, 2)
            self.assertEqual(kill.call_count, 2)

if __name__ == '__main__':
    unittest.main()
