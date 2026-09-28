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
        function value(section, key, title, def, datatype, desc) {
            var opt = section.option(form.Value, key, title, desc);
            if (def !== null) opt.default = String(def);
            if (datatype) opt.datatype = datatype;
            return opt;
        }
        function flag(section, key, title, def, desc) {
            var opt = section.option(form.Flag, key, title, desc);
            opt.default = def || '0'; opt.rmempty = false;
            return opt;
        }
        function poolOption(section) {
            var opt = section.option(form.ListValue, 'pool', _('Backend pool'));
            opt.load = function(section_id) {
                this.keylist = []; this.vallist = [];
                uci.sections('nginx_stream_manager', 'pool', function(p) { opt.value(p['.name'], p.name || p['.name']); });
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

        s = m.section(form.GridSection, 'pool', _('Backend pools'));
        s.anonymous = false; s.addremove = true; s.nodescriptions = true;
        value(s, 'name', _('Display name'), null);
        o = s.option(form.ListValue, 'method', _('Balancing method'));
        o.value('round_robin', _('Weighted round-robin'));
        o.value('least_conn', _('Least connections'));
        o.value('hash_ip', _('Consistent client-IP hash'));
        o.default = 'round_robin';

        s = m.section(form.GridSection, 'backend', _('Backend servers'));
        s.anonymous = true; s.addremove = true; s.nodescriptions = true;
        flag(s, 'enabled', _('Enabled'), '1');
        poolOption(s);
        o = value(s, 'address', _('IP address'), null, 'ipaddr'); o.rmempty = false;
        o = value(s, 'port', _('Port'), null, 'port'); o.rmempty = false;
        o = value(s, 'weight', _('Weight'), 1, 'range(1,1000)'); o.modalonly = true;
        o = value(s, 'max_fails', _('Maximum failures'), 1, 'range(0,1000)', _('Passive failure detection; 0 disables failure counting.')); o.modalonly = true;
        o = value(s, 'fail_timeout', _('Failure timeout (seconds)'), 10, 'range(1,86400)'); o.modalonly = true;
        o = value(s, 'max_conns', _('Maximum connections'), 0, 'range(0,1000000)', _('0 = unlimited. Shared across workers through an upstream zone.')); o.modalonly = true;
        o = flag(s, 'backup', _('Backup server'), '0', _('Not supported with client-IP hashing.')); o.modalonly = true;
        o = flag(s, 'down', _('Administratively down'), '0'); o.modalonly = true;

        s = m.section(form.GridSection, 'service', _('Services'));
        s.anonymous = false; s.addremove = true; s.nodescriptions = true;
        flag(s, 'enabled', _('Enabled'), '1');
        value(s, 'name', _('Display name'), null);
        o = s.option(form.ListValue, 'protocol', _('Protocol'));
        o.value('tcp', 'TCP'); o.value('udp', 'UDP'); o.value('both', 'TCP + UDP'); o.default = 'tcp';
        o = value(s, 'listen_address', _('Listen IP'), '0.0.0.0', 'ipaddr', _('Use a separate service for IPv6. IPv6 listeners are IPv6-only.')); o.rmempty = false;
        o = value(s, 'listen_port', _('Listen port'), null, 'port'); o.rmempty = false;
        poolOption(s);
        o = flag(s, 'open_firewall', _('Manage firewall input permit'), '0', _('Adds a permit for this listener on explicit client interfaces. Other existing firewall rules remain effective.')); o.modalonly = true;
        o = s.option(form.DynamicList, 'ingress_interface', _('Client ingress devices'), _('Linux device names, e.g. br-lan. Include a WAN device only when public access is intended.'));
        o.depends('open_firewall', '1'); o.modalonly = true;
        o = flag(s, 'transparent', _('Preserve client source IP'), '0', _('Backends must route replies through this router. Does not preserve the source port.')); o.modalonly = true;
        o = s.option(form.DynamicList, 'return_interface', _('Backend return devices'), _('Linux device names, e.g. br-lan or eth1. Not UCI network names.'));
        o.depends('transparent', '1'); o.modalonly = true;
        o = flag(s, 'return_route_ack', _('Backend return route verified'), '0', _('I verified backend replies pass through this router, including clients in the same subnet.'));
        o.depends('transparent', '1'); o.modalonly = true;
        o = s.option(form.DynamicList, 'allow_source', _('Allowed client CIDRs'), _('Empty = any client allowed by the firewall. Otherwise Nginx denies all other client addresses.'));
        o.datatype = 'ipaddr'; o.modalonly = true;
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
        o = flag(s, 'access_log', _('Access log'), '0', _('One record per completed session; client addresses are recorded.')); o.modalonly = true;
        o = flag(s, 'traffic_history', _('Traffic history'), '0', _('Per-upstream byte totals for completed sessions. Up to 24 hours in RAM; no client IPs. Apply and reload to activate.')); o.modalonly = true;
        return m.render();
    }
});
