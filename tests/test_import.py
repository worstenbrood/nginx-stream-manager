import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from test_nsm import nsm, ROOT
sys.path.insert(0,str(ROOT/'packages/nginx-stream-manager/files'))
import nsm_importer as imp

CURRENT=[{'.type':'global','.name':'main','enabled':'0'}]
SIMPLE='stream { server { listen 9000; proxy_pass 192.0.2.1:9001; } }'

class ParserTests(unittest.TestCase):
    def test_quotes_comments_and_braces_inside_log_format(self):
        nodes=imp.parse('stream { log_format x "a { # } ; b"; #comment\n server {listen 90;} }')
        self.assertEqual(nodes[0]['children'][0]['args'], ['x','a { # } ; b'])

    def test_unclosed_syntax(self):
        for text in ('stream {', 'stream { server { listen 80 } }', 'stream { "x', '}'):
            with self.assertRaises(imp.ImportError): imp.parse(text)

    def test_user_configuration_five_services_and_eight_backends(self):
        text=(ROOT/'tests/fixtures/existing-nginx.conf').read_text()
        p=imp.plan(text,CURRENT)
        self.assertEqual(p['blockers'],[])
        self.assertEqual(len(p['services']),5)
        self.assertEqual(sum(s['.type']=='pool' for s in p['sections']),5)
        self.assertEqual(sum(s['.type']=='backend' for s in p['sections']),8)
        self.assertEqual(sum(s['transparent'] for s in p['services']),3)
        services=[s for s in p['sections'] if s['.type']=='service']
        self.assertTrue(all(s['enabled']=='0' for s in services))
        self.assertEqual(sum(s['protocol']=='both' for s in services),4)
        self.assertTrue(all(s['timeout']=='60' for s in services if s.get('transparent')=='1'))
        self.assertEqual(nsm.parse_uci(imp.uci_text(CURRENT+p['sections'])),CURRENT+p['sections'])
        live=copy.deepcopy(CURRENT+p['sections'])
        for s in live:
            if s['.type']=='service':
                s.update(enabled='1',return_interface=['br-lan'],return_route_ack='1')
        generated=nsm.compile_config(live)
        self.assertEqual(generated['families'],[4,6])
        self.assertIn('listen 127.0.0.1:53 reuseport bind;',generated['nginx'])
        self.assertIn('listen [::1]:53 udp reuseport bind ipv6only=on;',generated['nginx'])

    def test_include_blocks_import(self):
        p=imp.plan('include /etc/nginx/extra.conf; '+SIMPLE,CURRENT)
        self.assertTrue(p['blockers'])
        p=imp.plan('stream { include /etc/nginx/streams/*.conf; }',CURRENT)
        self.assertTrue(p['blockers'])

    def test_unknown_security_directive_blocks_import(self):
        p=imp.plan(SIMPLE.replace('listen 9000;', 'listen 9000; limit_conn foo 10;'),CURRENT)
        self.assertTrue(p['blockers'])

    def test_tls_listener_not_silently_downgraded(self):
        self.assertTrue(imp.plan(SIMPLE.replace('listen 9000;', 'listen 9000 ssl;'),CURRENT)['blockers'])

    def test_hostname_backend_blocked(self):
        self.assertTrue(imp.plan(SIMPLE.replace('192.0.2.1','backend.example'),CURRENT)['blockers'])

    def test_defaults_preserve_nginx_connect_timeout(self):
        p=imp.plan(SIMPLE,CURRENT)
        s=next(s for s in p['sections'] if s['.type']=='service')
        self.assertEqual(s['connect_timeout'],'60')
        self.assertEqual(s['timeout'],'600')

    def test_inherited_settings_and_server_override(self):
        p=imp.plan(SIMPLE.replace('stream {','stream { proxy_timeout 3m; proxy_connect_timeout 7s;').replace('listen 9000;', 'listen 9000; proxy_timeout 20s;'),CURRENT)
        s=next(s for s in p['sections'] if s['.type']=='service')
        self.assertEqual(s['timeout'],'20');self.assertEqual(s['connect_timeout'],'7')

    def test_acl_order_rejection(self):
        self.assertTrue(imp.plan(SIMPLE.replace('listen 9000;', 'listen 9000; deny 192.0.2.0/24; allow all;'),CURRENT)['blockers'])

    def test_allowlist_import(self):
        p=imp.plan(SIMPLE.replace('listen 9000;', 'listen 9000; allow 192.0.2.0/24; deny all;'),CURRENT)
        s=next(s for s in p['sections'] if s['.type']=='service')
        self.assertEqual(s['allow_source'],['192.0.2.0/24'])

    def test_duplicate_import_detected(self):
        p=imp.plan(SIMPLE,CURRENT)
        self.assertTrue(imp.plan(SIMPLE,CURRENT+p['sections'])['blockers'])

    def test_uci_quote_roundtrip(self):
        sections=[{'.name':'safe','.type':'pool','name':"John's pool"}]
        self.assertEqual(nsm.parse_uci(imp.uci_text(sections)),sections)

    def test_multiline_value_rejected(self):
        with self.assertRaises(imp.ImportError): imp.uci_text([{'.name':'x','.type':'pool','name':'x\ny'}])

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'nginx.conf';self.source.write_text(SIMPLE)
        self.app=self.root/'uci';self.app.write_text(imp.uci_text(CURRENT))
        self.patches=[patch.object(nsm,'SOURCE_CONFIG',self.source),patch.object(nsm,'APP_CONFIG',self.app),
            patch.object(nsm,'RUN',self.root/'run'),patch.object(nsm,'IMPORT_BACKUPS',self.root/'backups'),
            patch.object(nsm,'pid',return_value=None),patch.object(nsm,'command',return_value=SimpleNamespace(stdout='')),
            patch.object(nsm,'read_config',side_effect=lambda raw=None:nsm.parse_uci(raw.decode() if raw is not None else self.app.read_text()))]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_preview_no_mutation_and_commit_backup(self):
        before=self.app.read_bytes(); source=self.source.read_bytes()
        p=nsm.import_preview();self.assertTrue(p['can_import'])
        self.assertEqual(self.app.read_bytes(),before)
        r=nsm.import_commit(p['token'])
        self.assertTrue(r['ok']);self.assertEqual(Path(r['backup']).read_bytes(),before)
        self.assertEqual(self.source.read_bytes(),source)
        services=[s for s in nsm.read_config() if s['.type']=='service']
        self.assertTrue(all(s['enabled']=='0' for s in services))
        self.assertFalse((self.root/'run/nginx.conf').exists())

    def test_stale_source_token_rejected(self):
        p=nsm.import_preview();before=self.app.read_bytes()
        self.source.write_text(SIMPLE+' # changed')
        with self.assertRaisesRegex(nsm.Invalid,'changed'):nsm.import_commit(p['token'])
        self.assertEqual(self.app.read_bytes(),before)

    def test_stale_app_token_rejected(self):
        p=nsm.import_preview();self.app.write_text(imp.uci_text(CURRENT)+'# changed\n')
        with self.assertRaisesRegex(nsm.Invalid,'changed'):nsm.import_commit(p['token'])

    def test_running_manager_blocks_import(self):
        with patch.object(nsm,'pid',return_value=123):
            self.assertFalse(nsm.import_preview()['can_import'])

    def test_enabled_manager_blocks_import(self):
        self.app.write_text(imp.uci_text([dict(CURRENT[0],enabled='1')]))
        self.assertFalse(nsm.import_preview()['can_import'])

    def test_invalid_token_rejected(self):
        with self.assertRaises(nsm.Invalid): nsm.import_commit('abc')

    def test_unsupported_directive_cannot_commit(self):
        self.source.write_text(SIMPLE.replace('listen 9000;','listen 9000 ssl;'))
        p=nsm.import_preview();self.assertFalse(p['can_import'])
        with self.assertRaises(nsm.Invalid):nsm.import_commit(p['token'])

    def test_invalid_numeric_setting_blocks_before_write(self):
        self.source.write_text(SIMPLE.replace('listen 9000;', 'listen 9000; proxy_timeout -2;'))
        self.assertFalse(nsm.import_preview()['can_import'])

    def test_duplicate_exact_listener_rejected(self):
        self.source.write_text('stream {server {listen 9000; proxy_pass 192.0.2.1:9001;} server {listen 9000; proxy_pass 192.0.2.2:9001;}}')
        self.assertFalse(nsm.import_preview()['can_import'])

if __name__=='__main__':unittest.main()
