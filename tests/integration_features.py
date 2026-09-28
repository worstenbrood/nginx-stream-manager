"""Actual OSS stream features; disposable Docker only. No external endpoints."""
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import unittest
from integration_nginx import RealRuntime, nsm


class FeatureRuntime(RealRuntime):
    def tcp_service(self, backend=None):
        self.service('feature', '127.0.0.1', 19100, backend or self.backend(4, b'primary'))
        self.sections[-1]['protocol'] = 'tcp'
        return self.sections[-1]

    def add_pool(self, name, backend):
        self.sections.extend([{'.type': 'pool', '.name': name},
                              {'.type': 'backend', '.name': name + '_backend', 'pool': name,
                               'address': backend[0], 'port': str(backend[1])}])

    def certificates(self):
        cert, key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=tls.example.test', '-addext', 'subjectAltName=DNS:tls.example.test',
                        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        return cert, key

    def tls_backend(self, cert, key, tag=b'tls'):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        context.set_alpn_protocols(['h2', 'http/1.1'])
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen()
        self.sockets.append(sock)
        def serve():
            while True:
                try:
                    client, _ = sock.accept()
                    try:
                        with context.wrap_socket(client, server_side=True) as wrapped:
                            wrapped.recv(1024); wrapped.sendall(tag)
                    except (OSError, ssl.SSLError):
                        client.close()
                except OSError:
                    return
        threading.Thread(target=serve, daemon=True).start()
        return '127.0.0.1', sock.getsockname()[1]

    def tls_request(self, cert, name='tls.example.test', alpn=None, client_key=None):
        context = ssl.create_default_context(cafile=str(cert))
        context.check_hostname = name == 'tls.example.test'
        if alpn: context.set_alpn_protocols([alpn])
        if client_key: context.load_cert_chain(cert, client_key)
        with socket.create_connection(('127.0.0.1', 19100), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=name) as client:
                client.sendall(b'probe')
                return client.recv(1024)

    def test_backup_on_connection_failure(self):
        reserved = socket.socket(); reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]  # Bound, not listening: deterministic refusal.
        self.sockets.append(reserved)
        service = self.tcp_service(('127.0.0.1', port))
        backup = self.backend(4, b'backup')
        self.sections.append({'.type': 'backend', '.name': 'backup', 'pool': 'feature_pool',
                              'address': backup[0], 'port': str(backup[1]), 'backup': '1'})
        self.start()
        self.assertEqual(self.request('127.0.0.1', 19100, socket.SOCK_STREAM), b'backup')

    def test_administratively_down_primary_uses_backup(self):
        self.tcp_service()
        self.sections[-2]['down'] = '1'
        backup = self.backend(4, b'backup')
        self.sections.append({'.type': 'backend', '.name': 'backup', 'pool': 'feature_pool',
                              'address': backup[0], 'port': str(backup[1]), 'backup': '1'})
        self.start()
        self.assertEqual(self.request('127.0.0.1', 19100, socket.SOCK_STREAM), b'backup')

    def test_tls_termination_and_client_certificate(self):
        cert, key = self.certificates()
        service = self.tcp_service()
        service.update(tls_mode='terminate', ssl_certificate=str(cert), ssl_certificate_key=str(key),
                       ssl_verify_client='on', ssl_client_certificate=str(cert), ssl_alpn='h2 http/1.1')
        self.start()
        self.assertEqual(self.tls_request(cert, client_key=key), b'primary')
        try:
            response = self.tls_request(cert)
        except (ssl.SSLError, OSError):
            response = b''
        self.assertEqual(response, b'')

    def test_verified_upstream_tls(self):
        cert, key = self.certificates()
        service = self.tcp_service(self.tls_backend(cert, key))
        service.update(upstream_tls='1', proxy_ssl_name='tls.example.test', proxy_ssl_trusted_certificate=str(cert))
        self.start()
        self.assertEqual(self.request('127.0.0.1', 19100, socket.SOCK_STREAM), b'tls')

    def test_upstream_certificate_name_mismatch_rejected(self):
        cert, key = self.certificates()
        service = self.tcp_service(self.tls_backend(cert, key))
        service.update(upstream_tls='1', proxy_ssl_name='wrong.example.test', proxy_ssl_trusted_certificate=str(cert))
        self.start()
        try:
            response = self.request('127.0.0.1', 19100, socket.SOCK_STREAM)
        except ConnectionResetError:
            response = b''
        self.assertEqual(response, b'')
        self.assertIn('certificate does not match', (nsm.LOG / 'feature.error.log').read_text())

    def test_sni_passthrough_routes_and_fallback(self):
        cert, key = self.certificates()
        service = self.tcp_service(self.tls_backend(cert, key, b'default'))
        service.update(tls_mode='preread', routing='sni')
        self.add_pool('special', self.tls_backend(cert, key, b'special'))
        self.sections.append({'.type': 'route', '.name': 'route1', 'service': 'feature', 'match': 'tls.example.test', 'pool': 'special'})
        self.start()
        self.assertEqual(self.tls_request(cert), b'special')
        self.assertEqual(self.tls_request(cert, name='other.example.test'), b'default')

    def test_alpn_passthrough(self):
        cert, key = self.certificates()
        service = self.tcp_service(self.tls_backend(cert, key, b'default'))
        service.update(tls_mode='preread', routing='alpn')
        self.add_pool('special', self.tls_backend(cert, key, b'h2'))
        self.sections.append({'.type': 'route', '.name': 'route1', 'service': 'feature', 'match': 'h2', 'pool': 'special'})
        self.start()
        self.assertEqual(self.tls_request(cert, alpn='h2'), b'h2')
        self.assertEqual(self.tls_request(cert, alpn='http/1.1'), b'default')

    def test_wildcard_sni_priority_matches_actual_nginx(self):
        cert, key = self.certificates()
        service = self.tcp_service(self.tls_backend(cert, key, b'default'))
        service.update(tls_mode='preread', routing='sni')
        for name in ('wide', 'narrow', 'exact'):
            self.add_pool(name, self.tls_backend(cert, key, name.encode()))
        for i, (match, pool) in enumerate((('*.example.test', 'wide'), ('*.sub.example.test', 'narrow'), ('app.sub.example.test', 'exact'))):
            self.sections.append({'.type': 'route', '.name': 'wild'+str(i), 'service': 'feature', 'match': match, 'pool': pool})
        self.start()
        for name, expected in (('example.test', b'default'), ('x.example.test', b'wide'), ('x.sub.example.test', b'narrow'), ('app.sub.example.test', b'exact')):
            self.assertEqual(self.tls_request(cert, name=name), expected)

    def test_source_route_and_proxy_real_ip(self):
        service = self.tcp_service()
        service.update(routing='source', accept_proxy_protocol='1', trusted_proxy=['127.0.0.1/32'], allow_source=['192.0.2.0/24'])
        self.add_pool('special', self.backend(4, b'trusted-client'))
        self.sections.append({'.type': 'route', '.name': 'route1', 'service': 'feature', 'match': '192.0.2.0/24', 'pool': 'special'})
        if os.environ.get('NSM_EXPECT_NO_STREAM_REALIP') == '1':
            with self.assertRaisesRegex(nsm.Invalid, 'stream real-IP'):
                self.start()
            return
        self.start()
        with socket.create_connection(('127.0.0.1', 19100), timeout=3) as client:
            client.sendall(b'PROXY TCP4 192.0.2.5 127.0.0.1 1234 19100\r\nprobe')
            self.assertEqual(client.recv(1024), b'trusted-client')

    def test_connection_limit_enforced(self):
        service = self.tcp_service()
        service.update(limit_per_client='1', timeout='30')
        self.start()
        with socket.create_connection(('127.0.0.1', 19100), timeout=3) as first:
            # Wait until Nginx has opened this connection to the backend.
            import time
            for _ in range(100):
                port = ':%04X' % int(self.sections[-2]['port'])
                if any(port in row and row.split()[3] == '01' for row in Path('/proc/net/tcp').read_text().splitlines()[1:]): break
                time.sleep(.02)
            try:
                response = self.request('127.0.0.1', 19100, socket.SOCK_STREAM)
            except ConnectionResetError:
                response = b''
            self.assertEqual(response, b'')
            first.sendall(b'probe')
            self.assertEqual(first.recv(1024), b'primary')

    def test_balancers_and_listener_options_nginx_validation(self):
        service = self.tcp_service()
        service.update(listen_backlog='128', listen_keepalive='on', listen_rcvbuf='32768', listen_sndbuf='32768',
                       next_upstream='0', next_upstream_timeout='3', bind_address='127.0.0.1')
        for method in ('random', 'random_two', 'hash', 'hash_consistent'):
            self.sections[1]['method'] = method
            compiled = nsm.compile_config(self.sections)
            nsm.atomic(self.root / 'candidate.conf', compiled['nginx'])
            nsm.command([nsm.NGINX, '-t', '-p', str(self.root) + '/', '-c', str(self.root / 'candidate.conf')])


if __name__ == '__main__':
    if not Path('/.dockerenv').exists():
        raise SystemExit('Run only inside the disposable Docker test container.')
    suite = unittest.TestSuite(FeatureRuntime(name) for name in FeatureRuntime.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
