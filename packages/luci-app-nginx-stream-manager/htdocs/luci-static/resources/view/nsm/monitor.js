'use strict';
'require view';
'require rpc';
'require ui';
'require poll';

var read = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'monitor', params: ['page'], expect: {} });
var refresh = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'monitor_refresh', expect: {} });
var diagnostics = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'diagnostics', expect: {} });
var metrics = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'metrics', expect: {} });
function table(headings, rows) {
    return E('table', { 'class': 'table' }, [E('tr', { 'class': 'tr' }, headings.map(function(h) { return E('th', { 'class': 'th' }, _(h)); }))].concat(rows.map(function(row) {
        return E('tr', { 'class': 'tr' }, row.map(function(cell) { return E('td', { 'class': 'td' }, cell == null ? '—' : String(cell)); }));
    })));
}
function date(t) { return t ? new Date(t * 1000).toLocaleString() : '—'; }
return view.extend({
    handleSave: null, handleSaveApply: null, handleReset: null,
    load: function() { return read(0); },
    render: function(initial) {
        var content = E('div'), message = E('p', { role: 'status' });
        var page = 0, pages = 1, pageLabel = E('span');
        function draw(data) {
            if (!data.ok) throw new Error(data.error || _('Unable to read monitoring'));
            pages = data.pages || 1; pageLabel.textContent = (page+1) + ' / ' + pages;
            var certificates = (data.certificates || []).map(function(c) {
                return E('details', {}, [E('summary', {}, c.path + ' · ' + (c.ok ? c.days_left + ' ' + _('days remaining') : _('Error'))),
                    E('p', {}, _('Services: ') + c.services.join(', ')), E('pre', { style: 'white-space:pre-wrap' }, c.ok ? c.details : c.error)]);
            });
            content.replaceChildren(E('h3', {}, _('Upstream health')),
                table(['Backend', 'Pool', 'Address', 'Probe', 'State', 'Latency (ms)', 'Last probe', 'Detail'], (data.backends || []).map(function(b) {
                    return [b.id, b.pool, b.address, b.mode, b.status || 'unknown', b.latency_ms, date(b.checked), b.error || ''];
                })), E('h3', {}, _('Active alerts') + ' (' + (data.active_alerts || 0) + ')'),
                E('div', {}, Object.keys(data.conditions || {}).map(function(key) { return E('p', { 'class': 'alert-message warning' }, data.conditions[key]); })),
                E('p', {}, data.last_error || ''), E('p', {}, data.snapshot_error || ''), E('p', {}, data.archive_error || ''), E('h3', {}, _('Certificates')),
                E('p', {}, _('Leaf certificates are inspected hourly. Private keys are never returned. CA-chain and hostname validation occur during verified TLS probes or Nginx validation.')),
                E('div', {}, certificates.length ? certificates : [_('No certificate results yet. Run due checks to inspect configured certificate files.')]),
                E('h3', {}, _('Recent events (latest 20)')),
                table(['Time', 'Level', 'Event'], (data.events || []).slice().reverse().map(function(e) { return [date(e.time), e.level, e.message]; })));
        }
        function action(label, run) {
            return E('button', { 'class': 'cbi-button', click: ui.createHandlerFn(this, function() {
                message.textContent = _('Working…');
                return run().catch(function(e) { message.textContent = e.message; });
            }) }, _(label));
        }
        var buttons = E('div', { style: 'display:flex;gap:.5em;flex-wrap:wrap' }, [
            action('Run due checks', function() { return refresh().then(function(r) { if (!r.ok) throw new Error(r.error); message.textContent = r.message || _('Monitoring updated.'); return read(page).then(draw); }); }),
            action('Previous page', function() { page = Math.max(0, page-1); return read(page).then(draw); }), pageLabel,
            action('Next page', function() { page = Math.min(pages-1, page+1); return read(page).then(draw); }),
            action('Diagnostics', function() { return diagnostics().then(function(r) {
                if (!r.ok) throw new Error(r.error);
                message.textContent = '';
                ui.showModal(_('Diagnostics'), [E('p', {}, r.note), E('div', {}, r.checks.map(function(c) {
                    return E('details', {}, [E('summary', {}, (c.ok ? '✓ ' : '⚠ ') + c.title), E('pre', { style: 'white-space:pre-wrap' }, c.text)]);
                })), E('button', { 'class': 'cbi-button', click: ui.hideModal }, _('Close'))]);
            }); }),
            action('Export Prometheus metrics', function() { return metrics().then(function(r) {
                if (!r.ok) throw new Error(r.error);
                var url = URL.createObjectURL(new Blob([r.text], { type: 'text/plain' }));
                var link = E('a', { href: url, download: 'nsm-metrics.prom' }); document.body.appendChild(link); link.click(); link.remove(); setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
                message.textContent = _('Metrics exported. Automated collectors can use the authenticated metrics RPC.');
            }); })]);
        draw(initial);
        poll.add(function() { return read(page).then(draw).catch(function(e) { message.textContent = e.message; }); }, 15);
        return E('div', {}, [E('h2', {}, _('Monitoring')), E('p', {}, _('Checks are opt-in per backend. They test from the router and never change forwarding. Down/up transitions require consecutive failed/successful probes. Stale results are not current health.')), buttons, message, content]);
    }
});
