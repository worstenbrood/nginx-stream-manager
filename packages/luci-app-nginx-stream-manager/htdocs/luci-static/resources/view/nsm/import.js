'use strict';
'require view';
'require rpc';
'require ui';
'require uci';

var preview = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'import_preview', expect: {} });
var commit = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'import_commit', params: ['token'], expect: {} });
return view.extend({
    handleSave: null, handleSaveApply: null, handleReset: null,
    render: function() {
        var result = E('div'), token = null;
        var importButton = E('button', { 'class': 'cbi-button cbi-button-positive', disabled: true }, _('Import disabled services'));
        function error(r) {
            token = null; importButton.disabled = true;
            result.replaceChildren(E('p', { 'class': 'alert-message warning' }, r.error || _('Import failed')));
        }
        var previewButton = E('button', { 'class': 'cbi-button cbi-button-action', click: ui.createHandlerFn(this, function() {
            token = null; importButton.disabled = true;
            return preview().then(function(r) {
                if (!r.ok) return error(r);
                token = r.can_import ? r.token : null;
                importButton.disabled = !r.can_import;
                var rows = [E('tr', { 'class': 'tr' }, [_('Listener'), _('Protocol'), _('Backend'), _('Source IP')].map(function(t) { return E('th', { 'class': 'th' }, t); }))];
                (r.services || []).forEach(function(s) {
                    rows.push(E('tr', { 'class': 'tr' }, [s.listen, s.protocol, s.target, s.transparent ? _('Preserved') : _('Router address')].map(function(t) { return E('td', { 'class': 'td' }, t); })));
                });
                result.replaceChildren(E('h3', {}, _('Import preview')), E('table', { 'class': 'table' }, rows));
                (r.blockers || []).forEach(function(t) { result.appendChild(E('p', { 'class': 'alert-message warning' }, t)); });
                (r.warnings || []).forEach(function(t) { result.appendChild(E('p', {}, t)); });
            }).catch(function(e) { error({error:e.message}); });
        }) }, _('Read nginx.conf and preview'));
        importButton.addEventListener('click', function() {
            if (!token) return;
            var selected = token;
            ui.showModal(_('Import into Stream Manager?'), [
                E('p', {}, _('This appends disabled services and backend pools to the app configuration and makes a backup. It does not change or reload the existing Nginx instance. Save or discard other open LuCI edits first.')),
                E('div', { 'class': 'right' }, [
                    E('button', { 'class': 'cbi-button', click: ui.hideModal }, _('Cancel')),
                    E('button', { 'class': 'cbi-button cbi-button-positive', click: ui.createHandlerFn(this, function() {
                        return commit(selected).then(function(r) {
                            ui.hideModal(); token = null; importButton.disabled = true;
                            if (!r.ok) return error(r);
                            uci.unload('nginx_stream_manager');
                            result.replaceChildren(E('p', {}, r.message), E('p', {}, _('Backup: ') + r.backup),
                                E('p', {}, _('Open Configuration to review services. Configure return devices for transparent services, migrate old listeners and rules, then enable the intended services.')));
                        }).catch(function(e) { ui.hideModal(); error({error:e.message}); });
                    }) }, _('Import'))
                ])
            ]);
        });
        return E('div', { 'class': 'cbi-map' }, [E('h2', {}, _('Import existing Nginx streams')),
            E('p', {}, _('Source: /etc/nginx/nginx.conf. The Stream Manager must be stopped and disabled; your existing Nginx may remain running.')),
            E('p', {}, _('This version imports inline stream blocks. Includes, unsupported directives and unsafe conversions block import rather than being silently omitted.')),
            E('div', { style:'display:flex;gap:.6em;margin:1em 0' }, [previewButton, importButton]), result]);
    }
});
