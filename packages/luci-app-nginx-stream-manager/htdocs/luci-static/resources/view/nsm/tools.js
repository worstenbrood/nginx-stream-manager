'use strict';
'require view';
'require rpc';
'require ui';
'require uci';

var tools = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'config_tools', params: ['action', 'source', 'name', 'content', 'token'], expect: {} });
var testRoute = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'route_test', params: ['service', 'name'], expect: {} });
function request(action, source, name, content, token) {
    return tools(action, source || '', name || '', content || '', token || '').then(function(r) { if (!r.ok) throw new Error(r.error); return r; });
}
return view.extend({
    handleSave: null, handleSaveApply: null, handleReset: null,
    load: function() { return Promise.all([request('list'), uci.load('nginx_stream_manager')]); },
    render: function(initial) {
        var status = E('p', { role: 'status' }), preview = E('div'), snapshots = E('select', { 'aria-label': _('Snapshot') }), source = E('select', { 'aria-label': _('Source service or pool') });
        var name = E('input', { type: 'text', placeholder: _('New unique section name'), 'aria-label': _('New section name') });
        var upload = E('input', { type: 'file', accept: '.json,application/json', 'aria-label': _('Configuration backup file') });
        var text = E('textarea', { rows: 6, style: 'width:100%', 'aria-label': _('Configuration backup JSON'), placeholder: _('Paste an exported configuration backup, or select its JSON file.') });
        var token = '', commit;
        function invalidate() { token = ''; if (commit) commit.disabled = true; preview.replaceChildren(); }
        function populate(data) {
            snapshots.replaceChildren(); source.replaceChildren();
            data.items.forEach(function(s) { snapshots.appendChild(E('option', { value: s.id }, new Date(s.time*1000).toLocaleString() + ' · ' + s.label + ' · ' + s.id.slice(0,8))); });
            data.sections.forEach(function(s) { source.appendChild(E('option', { value: s.id }, s.type + ' · ' + s.id)); });
        }
        function show(r) {
            token = r.token; commit.disabled = false;
            preview.replaceChildren(E('p', {}, r.message), E('pre', { style: 'white-space:pre-wrap;max-height:450px;overflow:auto' }, r.diff || _('No differences.')),
                E('p', {}, r.truncated ? _('Diff truncated; review the full exported configurations before committing.') : ''));
            if (r.truncated) { token = ''; commit.disabled = true; }
        }
        function button(label, fn) {
            return E('button', { 'class': 'cbi-button', click: ui.createHandlerFn(this, function() {
                status.textContent = '';
                return Promise.resolve().then(fn).catch(function(e) { status.textContent = e.message; });
            }) }, _(label));
        }
        commit = button('Commit previewed configuration', function() {
            return request('commit', '', '', '', token).then(function(r) { invalidate(); status.textContent = r.message; return request('list').then(populate); });
        }); commit.disabled = true;
        upload.addEventListener('change', function() { invalidate(); var file = upload.files[0]; if (!file) return;
            if (file.size > 65536) { status.textContent = _('Backup exceeds 64 KiB.'); return; }
            file.text().then(function(value) { text.value = value; }).catch(function(e) { status.textContent = e.message; });
        });
        [snapshots, source, name, text].forEach(function(el) { el.addEventListener('input', invalidate); el.addEventListener('change', invalidate); });
        var services = E('select', { 'aria-label': _('SNI service') }), hostname = E('input', { type: 'text', 'aria-label': _('Test SNI hostname'), placeholder: 'app.example.com' });
        uci.sections('nginx_stream_manager', 'service', function(s) { if (s.routing === 'sni') services.appendChild(E('option', { value: s['.name'] }, s.name || s['.name'])); });
        var result = E('p', { role: 'status' });
        populate(initial[0]);
        return E('div', {}, [E('h2', {}, _('Configuration tools')), E('p', {}, _('Up to 10 distinct snapshots, including configurations whose Nginx workers started successfully. This does not prove end-to-end forwarding. Restoring, importing and duplicating require a stopped manager and no pending LuCI edits. Backups contain settings and file paths, not certificates or keys.')),
            status, E('h3', {}, _('History and backup')), E('div', { style: 'display:flex;gap:.5em;flex-wrap:wrap' }, [snapshots,
                button('Compare / restore snapshot', function() { invalidate(); return request('restore', snapshots.value).then(show); }),
                button('Save snapshot', function() { return request('snapshot').then(function() { status.textContent = _('Snapshot saved.'); return request('list').then(populate); }); }),
                button('Export configuration', function() { return request('export').then(function(r) {
                    var url = URL.createObjectURL(new Blob([r.content], { type: 'application/json' }));
                    var link = E('a', { href: url, download: 'nginx-stream-manager-config.json' }); document.body.appendChild(link); link.click(); link.remove(); setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
                }); })]),
            E('h3', {}, _('Import configuration backup')), upload, text,
            button('Preview import', function() { invalidate(); return request('import', '', '', text.value).then(show); }),
            E('h3', {}, _('Duplicate service or pool')), E('p', {}, _('Services are copied disabled, including their routes. Pools include copies of their backend entries.')),
            E('div', {}, [source, name, button('Preview duplicate', function() { invalidate(); return request('duplicate', source.value, name.value).then(show); })]),
            E('h3', {}, _('Proposed configuration changes')), preview, commit,
            E('h3', {}, _('Test SNI routing')), E('p', {}, _('Uses committed configuration: exact names win, then the longest *.suffix match, then the default pool. A wildcard does not match its bare domain.')),
            E('div', {}, [services, hostname, button('Test route', function() { return testRoute(services.value, hostname.value).then(function(r) {
                if (!r.ok) throw new Error(r.error); result.textContent = r.name + ' → ' + r.pool + ' (' + r.match + ')';
            }); })]), result]);
    }
});
