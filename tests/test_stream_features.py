import copy
import unittest
from test_nsm import nsm, sample


class StreamFeatures(unittest.TestCase):
    def setUp(self):
        self.config = sample()
        self.service = self.config[-1]

    def compile(self):
        return nsm.compile_config(self.config)

    def route(self, mode='sni', match='app.example.test'):
        self.service.update(tls_mode='preread', routing=mode)
        self.config += [{'.type': 'pool', '.name': 'alternate'},
                        {'.type': 'backend', '.name': 'alternate_server', 'pool': 'alternate', 'address': '192.0.2.99', 'port': '9443'},
                        {'.type': 'route', '.name': 'route1', 'service': self.service['.name'], 'pool': 'alternate', 'match': match}]

    def test_down_primary_can_use_backup(self):
        self.config[2]['down'] = '1'
        self.config[3]['backup'] = '1'
        self.assertIn(' backup;', self.compile()['nginx'])
        self.config[3]['down'] = '1'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_balancing_methods_and_backup_constraints(self):
        for method in ('random', 'random_two', 'hash', 'hash_consistent'):
            self.config[1]['method'] = method
            self.compile()
            self.config[3]['backup'] = '1'
            with self.assertRaises(nsm.Invalid): self.compile()
            self.config[3]['backup'] = '0'
        self.config[1]['hash_key'] = '$remote_addr; include /etc/passwd'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_dns_and_unix_restricted_for_transparent_and_udp(self):
        for name in ('backend.example.test', 'unix:/var/run/backend.sock'):
            self.config[2]['address'] = name
            self.assertIn(name, self.compile()['nginx'])
            self.service.update(transparent='1', return_route_ack='1', return_interface=['br-lan'])
            with self.assertRaises(nsm.Invalid): self.compile()
            self.service['transparent'] = '0'
        self.service['protocol'] = 'udp'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_dns_path_and_tls_injection(self):
        for name in ('bad;name', 'a\ninclude x', 'unix:/tmp/a;b', 'unix:/tmp/../etc/key', '$host'):
            self.config[2]['address'] = name
            with self.assertRaises(nsm.Invalid): self.compile()
        self.config[2]['address'] = '192.0.2.10'
        self.service.update(tls_mode='terminate', ssl_certificate='/etc/cert.pem', ssl_certificate_key='/etc/key.pem')
        for key, value in (('ssl_certificate', '/etc/a;quit'), ('ssl_ciphers', 'HIGH;exit'), ('ssl_alpn', 'h2\ninclude /tmp/x')):
            config = copy.deepcopy(self.config)
            config[-1][key] = value
            with self.assertRaises(nsm.Invalid): nsm.compile_config(config)

    def test_tls_security_defaults_and_required_files(self):
        self.service.update(upstream_tls='1', proxy_ssl_name='backend.example.test')
        with self.assertRaises(nsm.Invalid): self.compile()
        self.service['proxy_ssl_trusted_certificate'] = '/etc/ssl/ca.pem'
        self.assertIn('proxy_ssl_verify on;', self.compile()['nginx'])
        self.service.update(tls_mode='terminate', ssl_certificate='/etc/cert.pem')
        with self.assertRaises(nsm.Invalid): self.compile()
        self.service['ssl_certificate_key'] = '/etc/key.pem'
        self.assertIn('ssl_session_tickets off;', self.compile()['nginx'])
        self.service['ssl_verify_client'] = 'on'
        with self.assertRaises(nsm.Invalid): self.compile()
        self.service['ssl_client_certificate'] = '/etc/ssl/ca.pem'
        self.compile()

    def test_tcp_only_options_reject_udp(self):
        for options in ({'tls_mode': 'preread'}, {'upstream_tls': '1'}, {'accept_proxy_protocol': '1'}, {'listen_backlog': '64'}, {'listen_deferred': '1'}, {'listen_keepalive': 'on'}):
            config = copy.deepcopy(self.config)
            config[-1].update(options, protocol='udp')
            with self.assertRaises(nsm.Invalid): nsm.compile_config(config)

    def test_source_bind_conflicts_and_family(self):
        self.service['bind_address'] = '::1'
        with self.assertRaises(nsm.Invalid): self.compile()
        self.service['bind_address'] = '192.0.2.1'
        self.assertIn('proxy_bind 192.0.2.1;', self.compile()['nginx'])
        self.service.update(transparent='1', return_route_ack='1', return_interface=['br-lan'])
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_trusted_proxy_firewall_uses_peer_not_claimed_client(self):
        self.service.update(accept_proxy_protocol='1', trusted_proxy=['198.51.100.10/32'], open_firewall='1', ingress_interface=['br-lan'])
        result = self.compile()
        self.assertIn('198.51.100.10/32', result['fw4'])
        self.assertNotIn('192.0.2.0/24', result['fw4'])
        self.assertIn('allow 192.0.2.0/24;', result['nginx'])
        self.service['trusted_proxy'] = ['0.0.0.0/0']
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_route_pools_included_and_validated(self):
        self.route()
        config = self.compile()['nginx']
        self.assertIn('upstream pool_alternate', config)
        self.assertIn('app.example.test pool_alternate;', config)
        self.assertIn('map $ssl_preread_server_name $route_', config)
        self.config[-2]['down'] = '1'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_route_safety_duplicates_and_disabled_routing(self):
        self.route()
        self.config[-1]['match'] = 'evil;name'
        with self.assertRaises(nsm.Invalid): self.compile()
        self.config[-1]['match'] = 'app.example.test'
        self.service['routing'] = 'none'
        with self.assertRaises(nsm.Invalid): self.compile()
        self.service['routing'] = 'sni'
        duplicate = dict(self.config[-1], **{'.name': 'route2'})
        self.config.append(duplicate)
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_route_protocols_and_source(self):
        for mode, match, expected in (('alpn', 'http/1.1', 'http/1[.]1'), ('source', '10.0.0.0/8', 'geo $remote_addr'), ('tls_protocol', 'none', '"" pool_alternate')):
            self.setUp(); self.route(mode, match)
            self.assertIn(expected, self.compile()['nginx'])

    def test_transparent_routes_are_in_return_rules(self):
        self.route('source', '192.0.2.0/24')
        self.service.update(transparent='1', return_route_ack='1', return_interface=['br-lan'])
        self.assertIn('192.0.2.99', self.compile()['nft'])
        self.config[-2]['address'] = '::1'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_limit_zones_are_per_service(self):
        self.service.update(limit_connections='50', limit_per_client='3')
        self.assertIn('limit_conn_zone $binary_remote_addr', self.compile()['nginx'])
        self.service['limit_per_client'] = '65536'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_unix_traffic(self):
        import json
        raw = json.dumps(dict(service='test', protocol='TCP', time='1800000000', upstream='unix:/run/backend.sock', sent='10', received='20'))
        self.assertEqual(nsm.traffic_record(raw, 1800000000)[0][0], 'test|TCP|unix:/run/backend.sock')

    def test_deny_precedes_allow(self):
        self.service['deny_source'] = ['192.0.2.9/32']
        config = self.compile()['nginx']
        self.assertLess(config.index('deny 192.0.2.9/32;'), config.index('allow 192.0.2.0/24;'))
        self.service['deny_source'] = ['::1']
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_reserved_map_keywords_rejected(self):
        self.route()
        for word in ('default', 'include', 'hostnames', 'volatile'):
            self.config[-1]['match'] = word
            with self.assertRaises(nsm.Invalid): self.compile()

    def test_worker_shutdown_deadline(self):
        self.config[0]['shutdown_timeout'] = '0'
        self.assertIn('worker_shutdown_timeout 0s;', self.compile()['nginx'])
        self.config[0]['shutdown_timeout'] = '-1'
        with self.assertRaises(nsm.Invalid): self.compile()

    def test_luci_session_metadata_preserves_strict_argument_validation(self):
        from unittest.mock import patch
        session = 'a' * 32
        with patch.object(nsm, 'status', return_value={'ok': True}):
            args = {'ubus_rpc_session': session}
            self.assertTrue(nsm.rpc('status', args)['ok'])
            self.assertEqual(args, {'ubus_rpc_session': session})
            for invalid in ({'ubus_rpc_session': 1}, {'ubus_rpc_session': 'bad'},
                            {'ubus_rpc_session': session, 'command': 'reboot'}):
                with self.assertRaises(nsm.Invalid): nsm.rpc('status', invalid)
