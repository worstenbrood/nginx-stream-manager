'use strict';
'require view';
'require form';
'require uci';

return view.extend({
    load: function() { return uci.load('nginx_stream_manager'); },
    render: function() {
        var m = new form.Map('nginx_stream_manager', _('Nginx Stream Manager'),
            _('Generic TCP/UDP forwarding. Save & Apply commits configuration and triggers a validated reload. Check Overview for runtime errors. Optionally manage firewall input permits per service and client interface.'));
        var s, o;
        function option(section, type, key, title, desc) {
            return section.nsmTab ? section.taboption(section.nsmTab, type, key, title, desc) : section.option(type, key, title, desc);
        }
        function value(section, key, title, def, datatype, desc) {
            var opt = option(section, form.Value, key, title, desc);
            if (def !== null) opt.default = String(def);
            if (datatype) opt.datatype = datatype;
            return opt;
        }
        function flag(section, key, title, def, desc) {
            var opt = option(section, form.Flag, key, title, desc);
            opt.default = def || '0'; opt.rmempty = false;
            return opt;
        }
        function poolOption(section) {
            var opt = option(section, form.ListValue, 'pool', _('Backend pool'));
            opt.load = function(section_id) {
                var current = this; // GridSection clones options for its edit dialog.
                this.keylist = []; this.vallist = [];
                uci.sections('nginx_stream_manager', 'pool', function(p) { current.value(p['.name'], p.name || p['.name']); });
                return this.super('load', [section_id]);
            };
            opt.rmempty = false;
            opt.description = _('Save newly created pools before selecting them here.');
            return opt;
        }
        s = m.section(form.NamedSection, 'main', 'global', _('Global settings'));
        s.addremove = false;
        flag(s, 'enabled', _('Enable manager'), '0');
        value(s, 'workers', _('Workers'), 'auto', null, _('auto or 1–64'));
        value(s, 'connections', _('Connections per worker'), 1024, 'range(64,65535)');
        value(s, 'nofile', _('File descriptor limit per worker'), 4096, 'range(128,1048576)');
        value(s, 'log_mb', _('Rotate each log above (MiB)'), 4, 'range(1,128)', _('Checked every 30 seconds; one previous file retained. This is not a hard size cap.'));
        value(s, 'shutdown_timeout', _('Graceful worker shutdown timeout (seconds)'), 15, 'range(0,604800)', _('After reload, old sessions are closed at this deadline. 0 disables the deadline.'));
        flag(s, 'multi_accept', _('Accept all pending connections per event'), '0');
        flag(s, 'accept_mutex', _('Serialize accepting connections'), '0', _('Normally unnecessary with reuseport listeners.'));
        value(s, 'health_interval', _('Health check interval (seconds)'), 60, 'range(30,3600)', _('Checks run while the manager is running, at most 16 backends per maintenance round. Monitoring never changes pool membership.'));
        value(s, 'health_fall', _('Failed probes before down'), 3, 'range(1,10)');
        value(s, 'health_rise', _('Successful probes before up'), 2, 'range(1,10)');
        value(s, 'certificate_warn_days', _('Certificate expiry warning (days)'), 30, 'range(1,90)');
        flag(s, 'alert_syslog', _('Send health and certificate alerts to syslog'), '0', _('Only state changes are logged. Alerts also appear in Monitoring.'));
        value(s, 'history_path', _('Persistent history directory'), null, null, _('Optional: /mnt/usb/nsm on an already mounted drive. Missing mounts never fall back to router flash. Checkpoint every 5 minutes; at most 8 MiB.'));
        value(s, 'history_days', _('Persistent history retention (days)'), 7, 'range(1,30)', _('Hourly aggregates; RAM keeps at most 24 hours. Capacity limits may shorten history.'));

        s = m.section(form.GridSection, 'pool', _('Backend pools'));
        s.anonymous = false; s.addremove = true; s.nodescriptions = true;
        value(s, 'name', _('Display name'), null);
        o = option(s, form.ListValue, 'method', _('Balancing method'));
        o.value('round_robin', _('Weighted round-robin'));
        o.value('least_conn', _('Least connections'));
        o.value('hash_ip', _('Consistent client-IP hash'));
        o.value('hash', _('Hash (selectable key)'));
        o.value('hash_consistent', _('Consistent hash (selectable key)'));
        o.value('random', _('Weighted random'));
        o.value('random_two', _('Random two, least connections'));
        o.default = 'round_robin';
        o = option(s, form.ListValue, 'hash_key', _('Hash key'));
        ['$remote_addr', '$remote_addr:$remote_port', '$server_addr:$server_port', '$ssl_preread_server_name', '$ssl_server_name', '$proxy_protocol_addr'].forEach(function(k) { o.value(k); });
        o.depends('method', 'hash'); o.depends('method', 'hash_consistent'); o.default = '$remote_addr'; o.modalonly = true;
        o = value(s, 'zone_kb', _('Shared pool memory (KiB)'), 64, 'range(32,16384)'); o.modalonly = true;

        s = m.section(form.GridSection, 'backend', _('Backend servers'));
        s.anonymous = true; s.addremove = true; s.nodescriptions = true;
        flag(s, 'enabled', _('Enabled'), '1');
        poolOption(s);
        o = value(s, 'address', _('IP, DNS name or unix:/path'), null, null, _('DNS names resolve at start/reload. Transparent mode requires literal IPs. UNIX upstreams require TCP.')); o.rmempty = false;
        o = value(s, 'port', _('Port (except UNIX sockets)'), null, 'port');
        o = value(s, 'weight', _('Weight'), 1, 'range(1,1000)'); o.modalonly = true;
        o = value(s, 'max_fails', _('Maximum failures'), 1, 'range(0,1000)', _('Passive failure detection; 0 disables failure counting.')); o.modalonly = true;
        o = value(s, 'fail_timeout', _('Failure timeout (seconds)'), 10, 'range(1,86400)'); o.modalonly = true;
        o = value(s, 'max_conns', _('Maximum connections'), 0, 'range(0,1000000)', _('0 = unlimited. Shared across workers through an upstream zone.')); o.modalonly = true;
        flag(s, 'backup', _('Backup server'), '0', _('Used when primary servers are unavailable. Requires round-robin or least connections.'));
        flag(s, 'down', _('Administratively down'), '0', _('Keeps the server in the pool but prevents new connections. Existing sessions are not migrated.'));
        o = option(s, form.ListValue, 'health_check', _('Active health check'));
        [['off', 'Disabled'], ['tcp', 'TCP connect'], ['tls', 'Verified TLS handshake'], ['dns', 'DNS query over UDP']].forEach(function(v) { o.value(v[0], _(v[1])); });
        o.default = 'off'; o.modalonly = true;
        o = value(s, 'health_tls_name', _('Health TLS server name'), null, 'hostname'); o.depends('health_check', 'tls'); o.modalonly = true;
        o = value(s, 'health_ca', _('Health TLS CA file'), null, null, _('Empty uses system trust. Certificate verification is always enabled.')); o.depends('health_check', 'tls'); o.modalonly = true;
        o = value(s, 'health_dns_name', _('Health DNS query name'), 'example.com', 'hostname', _('Checks a matching NOERROR A-query response; it does not test other UDP protocols.')); o.depends('health_check', 'dns'); o.modalonly = true;

        s = m.section(form.GridSection, 'service', _('Services'));
        [['general', 'General'], ['network', 'Network and access'], ['proxy', 'Proxy and failover'], ['logging', 'Logging'], ['socket', 'Listener and limits'], ['tls', 'TLS and routing']].forEach(function(t) { s.tab(t[0], _(t[1])); });
        s.nsmTab = 'general';
        s.anonymous = false; s.addremove = true; s.nodescriptions = true;
        flag(s, 'enabled', _('Enabled'), '1');
        value(s, 'name', _('Display name'), null);
        o = option(s, form.ListValue, 'protocol', _('Protocol'));
        o.value('tcp', 'TCP'); o.value('udp', 'UDP'); o.value('both', 'TCP + UDP'); o.default = 'tcp';
        o = value(s, 'listen_address', _('Listen IP'), '0.0.0.0', 'ipaddr', _('Use a separate service for IPv6. IPv6 listeners are IPv6-only.')); o.rmempty = false;
        o = value(s, 'listen_port', _('Listen port'), null, 'port'); o.rmempty = false;
        poolOption(s);
        s.nsmTab = 'network';
        o = flag(s, 'open_firewall', _('Manage firewall input permit'), '0', _('Adds a permit for this listener on explicit client interfaces. Other existing firewall rules remain effective.')); o.modalonly = true;
        o = option(s, form.DynamicList, 'ingress_interface', _('Client ingress devices'), _('Linux device names, e.g. br-lan. Include a WAN device only when public access is intended.'));
        o.depends('open_firewall', '1'); o.modalonly = true;
        o = flag(s, 'transparent', _('Preserve client source IP'), '0', _('Backends must route replies through this router. Does not preserve the source port.')); o.modalonly = true;
        o = option(s, form.DynamicList, 'return_interface', _('Backend return devices'), _('Linux device names, e.g. br-lan or eth1. Not UCI network names.'));
        o.depends('transparent', '1'); o.modalonly = true;
        o = flag(s, 'return_route_ack', _('Backend return route verified'), '0', _('I verified backend replies pass through this router, including clients in the same subnet.'));
        o.depends('transparent', '1'); o.modalonly = true;
        o = option(s, form.DynamicList, 'allow_source', _('Allowed client CIDRs'), _('Empty = any client allowed by the firewall. Otherwise Nginx denies all other client addresses.'));
        o.datatype = 'ipaddr'; o.modalonly = true;
        o = option(s, form.DynamicList, 'deny_source', _('Denied client CIDRs'), _('Evaluated before the allow list. With no allow list, other client addresses remain allowed.'));
        o.datatype = 'ipaddr'; o.modalonly = true;
        s.nsmTab = 'proxy';
        [['connect_timeout', _('Connect timeout (seconds)'), 5, 'range(1,300)'],
         ['timeout', _('Idle timeout (seconds)'), 600, 'range(1,604800)'],
         ['next_upstream_tries', _('Maximum upstream attempts (0 = unlimited)'), 0, 'range(0,100)'],
         ['upload_rate', _('Upload limit per connection (bytes/s, 0 = unlimited)'), 0, 'uinteger'],
         ['download_rate', _('Download limit per connection (bytes/s, 0 = unlimited)'), 0, 'uinteger']].forEach(function(f) {
            var opt = value(s, f[0], f[1], f[2], f[3]); opt.modalonly = true;
        });
        o = value(s, 'udp_requests', _('UDP datagrams before starting a new session'), 0, 'range(0,1000000)', _('0 keeps the session until idle timeout or response completion.'));
        o.depends('protocol', 'udp'); o.depends('protocol', 'both'); o.modalonly = true;
        o = value(s, 'udp_responses', _('Expected UDP responses per datagram'), null, 'range(0,1000000)', _('Leave empty for unlimited. 0 means no response expected; 1 suits request/reply protocols, not every UDP service.'));
        o.depends('protocol', 'udp'); o.depends('protocol', 'both'); o.modalonly = true;
        o = flag(s, 'keepalive', _('TCP upstream keepalive'), '0'); o.modalonly = true;
        o.depends('protocol', 'tcp'); o.depends('protocol', 'both');
        o = flag(s, 'half_close', _('TCP half-close'), '0'); o.modalonly = true;
        o.depends('protocol', 'tcp'); o.depends('protocol', 'both');
        o = flag(s, 'proxy_protocol', _('Send PROXY protocol v1 to backend'), '0', _('TCP only. Enable only if the backend explicitly supports PROXY protocol.'));
        o.depends('protocol', 'tcp'); o.modalonly = true;
        s.nsmTab = 'logging';
        o = flag(s, 'access_log', _('Access log'), '0', _('One record per completed session; client addresses are recorded.')); o.modalonly = true;
        o = flag(s, 'traffic_history', _('Traffic history'), '0', _('Per-upstream bytes, errors and timings for completed sessions. Up to 24 hours in RAM, optionally longer on a mounted drive; no client IPs. Apply and reload to activate.')); o.modalonly = true;
        o = option(s, form.ListValue, 'error_level', _('Error log level'));
        ['debug', 'info', 'notice', 'warn', 'error', 'crit', 'alert', 'emerg'].forEach(function(level) { o.value(level); });
        o.default = 'warn'; o.modalonly = true;

        function advanced(key, title, def, datatype, desc) {
            var opt = value(s, key, title, def, datatype, desc); opt.modalonly = true; return opt;
        }
        function advancedFlag(key, title, def, desc) {
            var opt = flag(s, key, title, def, desc); opt.modalonly = true; return opt;
        }
        function select(key, title, items, def, desc) {
            var opt = option(s, form.ListValue, key, title, desc);
            items.forEach(function(item) { opt.value(item[0], _(item[1])); });
            opt.default = def; opt.modalonly = true; return opt;
        }
        s.nsmTab = 'proxy';
        advancedFlag('next_upstream', _('Retry another upstream on connection failure'), '1');
        advanced('next_upstream_timeout', _('Total upstream retry budget (seconds)'), 0, 'range(0,604800)', _('0 = unlimited. Applies to establishing connections; does not replay established sessions.'));
        advanced('proxy_buffer_size', _('Proxy buffer (bytes)'), 16384, 'range(1024,16777216)');
        advanced('bind_address', _('Local source IP for upstream connections'), null, 'ipaddr', _('Must exist on the router. Cannot be combined with source-IP preservation.'));
        advancedFlag('tcp_nodelay', _('TCP_NODELAY'), '1');
        advanced('preread_buffer_size', _('Preread buffer (bytes)'), 16384, 'range(1024,16777216)');
        advanced('preread_timeout', _('Preread timeout (seconds)'), 30, 'range(1,300)');
        s.nsmTab = 'socket';
        [['rcvbuf', _('Listener receive buffer (bytes)')], ['sndbuf', _('Listener send buffer (bytes)')], ['backlog', _('TCP listen backlog')], ['fastopen', _('TCP Fast Open queue')]].forEach(function(f) {
            var opt = advanced('listen_' + f[0], f[1], null, 'range(1,' + (f[0].endsWith('buf') ? '16777216' : '65535') + ')', f[0] === 'fastopen' ? _('Only enable when the application safely handles repeated SYN data.') : _('Empty uses the operating system/Nginx default.'));
            if (f[0] === 'backlog' || f[0] === 'fastopen') opt.depends('protocol', 'tcp');
        });
        o = advancedFlag('listen_deferred', _('TCP deferred accept'), '0'); o.depends('protocol', 'tcp');
        o = select('listen_keepalive', _('Listener TCP keepalive'), [['default', 'System default'], ['on', 'On'], ['off', 'Off']], 'default'); o.depends('protocol', 'tcp');
        o = advancedFlag('accept_proxy_protocol', _('Require incoming PROXY protocol v1/v2'), '0', _('Every TCP client must send a PROXY header. Restrict this listener to your proxy infrastructure.')); o.depends('protocol', 'tcp');
        o = option(s, form.DynamicList, 'trusted_proxy', _('Trusted proxy IPs/CIDRs'), _('Only these peers may replace the client address. Never trust public client networks.'));
        o.datatype = 'ipaddr'; o.modalonly = true; o.depends('accept_proxy_protocol', '1');
        o = advanced('proxy_protocol_timeout', _('PROXY header timeout (seconds)'), 30, 'range(1,300)'); o.depends('accept_proxy_protocol', '1');
        advanced('limit_connections', _('Maximum connections for this service'), 0, 'range(0,65535)', _('0 = unlimited. UDP counts active sessions.'));
        advanced('limit_per_client', _('Maximum connections per client IP'), 0, 'range(0,65535)');
        advanced('limit_zone_kb', _('Connection limit memory per zone (KiB)'), 64, 'range(32,16384)');
        advancedFlag('limit_dry_run', _('Only log connection-limit violations'), '0');
        s.nsmTab = 'tls';
        o = select('tls_mode', _('Client-side TLS'), [['off', 'Pass through without inspection'], ['preread', 'Inspect ClientHello (no decryption)'], ['terminate', 'Terminate TLS on this router']], 'off'); o.depends('protocol', 'tcp');
        select('routing', _('Pool routing'), [['none', 'Default pool only'], ['sni', 'TLS server name (SNI)'], ['alpn', 'TLS application protocol (ALPN)'], ['tls_protocol', 'TLS version / non-TLS'], ['source', 'Client IP / CIDR']], 'none', _('Configure matches in Pool routes below. The service pool remains the fallback. TLS routes require TCP and TLS inspection or termination.'));
        ['ssl_certificate', 'ssl_certificate_key', 'ssl_client_certificate', 'ssl_dhparam', 'ssl_crl', 'ssl_trusted_certificate', 'ssl_password_file', 'ssl_session_ticket_key'].forEach(function(k) {
            var titles = { ssl_certificate: 'TLS certificate path', ssl_certificate_key: 'TLS private key path', ssl_client_certificate: 'Client certificate CA path', ssl_dhparam: 'DH parameters path', ssl_crl: 'Client certificate revocation list path', ssl_trusted_certificate: 'Trusted TLS CA path', ssl_password_file: 'Private key password file path', ssl_session_ticket_key: 'TLS session ticket key path' };
            var opt = advanced(k, _(titles[k]), null, null, _('Absolute path on the router. File contents are not uploaded or displayed here.'));
            opt.depends('tls_mode', 'terminate');
        });
        o = select('ssl_verify_client', _('Verify client certificates'), [['off', 'Off'], ['on', 'Required'], ['optional', 'Optional, verify if supplied'], ['optional_no_ca', 'Optional without CA verification']], 'off'); o.depends('tls_mode', 'terminate');
        [['ssl_handshake_timeout', 'TLS handshake timeout (seconds)', 60, 300], ['ssl_session_timeout', 'TLS session lifetime (seconds)', 300, 86400], ['ssl_verify_depth', 'Client certificate chain depth', 2, 20]].forEach(function(f) { var opt = advanced(f[0], _(f[1]), f[2], 'range(1,' + f[3] + ')'); opt.depends('tls_mode', 'terminate'); });
        o = advanced('ssl_session_cache_kb', _('TLS session cache (KiB)'), 1024, 'range(64,16384)'); o.depends('tls_mode', 'terminate');
        o = advanced('ssl_alpn', _('Offered ALPN protocols'), null, null, _('Space-separated, e.g. h2 http/1.1')); o.depends('tls_mode', 'terminate');
        o = advanced('ssl_ecdh_curve', _('TLS ECDH curves'), null); o.depends('tls_mode', 'terminate');
        o = advancedFlag('ssl_session_tickets', _('TLS session tickets'), '0'); o.depends('tls_mode', 'terminate');
        o = advancedFlag('ssl_prefer_server_ciphers', _('Prefer server TLS ciphers'), '0'); o.depends('tls_mode', 'terminate');
        o = advancedFlag('upstream_tls', _('Encrypt upstream connections with TLS'), '0'); o.depends('protocol', 'tcp');
        [['proxy_ssl_name', 'Upstream TLS certificate/SNI name'], ['proxy_ssl_trusted_certificate', 'Upstream trusted CA file'], ['proxy_ssl_certificate', 'Upstream client certificate file'], ['proxy_ssl_certificate_key', 'Upstream client private key file'], ['proxy_ssl_crl', 'Upstream revocation list file'], ['proxy_ssl_password_file', 'Upstream private key password file']].forEach(function(f) { var opt = advanced(f[0], _(f[1]), null); opt.depends('upstream_tls', '1'); });
        o = advancedFlag('proxy_ssl_verify', _('Verify upstream certificate'), '1', _('Requires a CA file and a matching certificate/SNI name.')); o.depends('upstream_tls', '1');
        o = advancedFlag('proxy_ssl_session_reuse', _('Reuse upstream TLS sessions'), '1'); o.depends('upstream_tls', '1');
        o = advanced('proxy_ssl_verify_depth', _('Upstream certificate chain depth'), 2, 'range(1,20)'); o.depends('upstream_tls', '1');
        ['ssl_', 'proxy_ssl_'].forEach(function(prefix) {
            var opt = select(prefix + 'protocols', prefix === 'ssl_' ? _('Client TLS versions') : _('Upstream TLS versions'), [['TLSv1.2 TLSv1.3', 'TLS 1.2 and 1.3'], ['TLSv1.3', 'TLS 1.3'], ['TLSv1.2', 'TLS 1.2']], 'TLSv1.2 TLSv1.3');
            opt.depends(prefix === 'ssl_' ? 'tls_mode' : 'upstream_tls', prefix === 'ssl_' ? 'terminate' : '1');
            opt = advanced(prefix + 'ciphers', prefix === 'ssl_' ? _('Client TLS cipher list') : _('Upstream TLS cipher list'), 'HIGH:!aNULL:!MD5', null, _('OpenSSL cipher string for TLS 1.2. TLS 1.3 uses the OpenSSL default suites.'));
            opt.depends(prefix === 'ssl_' ? 'tls_mode' : 'upstream_tls', prefix === 'ssl_' ? 'terminate' : '1');
        });

        s = m.section(form.GridSection, 'route', _('Pool routes'), _('Matches are per service; unmatched traffic uses its default pool. SNI supports exact DNS names and *.example.com: exact names win, then the longest wildcard suffix. ALPN uses protocol tokens, source routes use IP/CIDR, TLS version uses TLSv1.2 / TLSv1.3 / none. For overlapping ALPN offers, the first matching route wins.'));
        s.anonymous = true; s.addremove = true;
        flag(s, 'enabled', _('Enabled'), '1');
        o = option(s, form.ListValue, 'service', _('Service'));
        o.load = function(section_id) { this.keylist = []; this.vallist = []; var opt = this; uci.sections('nginx_stream_manager', 'service', function(item) { opt.value(item['.name'], item.name || item['.name']); }); return this.super('load', [section_id]); }; o.rmempty = false;
        o = value(s, 'match', _('Match'), null); o.rmempty = false;
        poolOption(s);
        return m.render();
    }
});
