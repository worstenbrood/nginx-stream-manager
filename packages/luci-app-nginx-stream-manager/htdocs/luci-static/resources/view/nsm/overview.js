'use strict';
'require view';
'require rpc';
'require ui';
'require poll';

var statusCall = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'status', expect: {} });
var previewCall = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'preview', expect: {} });
var validateCall = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'validate', expect: {} });
var controlCall = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'control', params: ['action'], expect: {} });

return view.extend({
    handleSave: null, handleSaveApply: null, handleReset: null,
    load: function() { return statusCall(); },
    render: function(initial) {
        var stateBox = E('div'), detailBox = E('div');
        function draw(s) {
            stateBox.replaceChildren(E('p', {}, [
                E('strong', {}, s.running ? _('Running') : _('Stopped')),
                ' · ' + _('Boot start') + ': ' + (s.boot_enabled ? _('Enabled') : _('Disabled')),
                ' · v' + (s.version || '?')
            ]));
            if (!s.ok) stateBox.appendChild(E('p', { 'class': 'alert-message warning' }, s.error || _('Status unavailable')));
            if (s.last_error) stateBox.appendChild(E('p', { 'class': 'alert-message warning' }, _('Last apply error: ') + s.last_error));
            (s.warnings || []).forEach(function(w) { stateBox.appendChild(E('p', { 'class': 'alert-message warning' }, w)); });
            var rows = [E('tr', { 'class': 'tr table-titles' }, [
                E('th', { 'class': 'th' }, _('Service')), E('th', { 'class': 'th' }, _('Listener')),
                E('th', { 'class': 'th' }, _('Pool')), E('th', { 'class': 'th' }, _('Source IP'))
            ])];
            (s.services || []).forEach(function(x) {
                rows.push(E('tr', { 'class': 'tr' }, [E('td', { 'class': 'td' }, x.id),
                    E('td', { 'class': 'td' }, x.listen + ' / ' + x.protocol),
                    E('td', { 'class': 'td' }, x.pool), E('td', { 'class': 'td' }, x.transparent ? _('Preserved') : _('Router address'))]));
            });
            detailBox.replaceChildren(E('table', { 'class': 'table' }, rows));
        }
        function report(title, res) {
            ui.showModal(title, [
                E('p', {}, res.ok ? (res.message || _('Validation completed.')) : (res.error || _('Operation failed.'))),
                E('div', {}, (res.warnings || []).map(function(w) { return E('p', {}, w); })),
                E('div', { 'class': 'right' }, E('button', { 'class': 'cbi-button', click: ui.hideModal }, _('Close')))
            ]);
        }
        var buttons = E('div', { style: 'display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:1em' });
        ['start', 'stop', 'reload', 'enable', 'disable'].forEach(function(action) {
            var labels = { start: _('Start'), stop: _('Stop'), reload: _('Reload committed configuration'),
                enable: _('Enable at boot'), disable: _('Disable at boot') };
            buttons.appendChild(E('button', { 'class': 'cbi-button cbi-button-action', click: ui.createHandlerFn(this, function() {
                return controlCall(action).then(function(r) { report(labels[action], r); return statusCall().then(draw); });
            }) }, labels[action]));
        }, this);
        buttons.appendChild(E('button', { 'class': 'cbi-button', click: ui.createHandlerFn(this, function() {
            return validateCall().then(function(r) { report(_('Validate committed configuration'), r); });
        }) }, _('Validate')));
        buttons.appendChild(E('button', { 'class': 'cbi-button', click: ui.createHandlerFn(this, function() {
            return previewCall().then(function(r) {
                if (!r.ok) return report(_('Preview'), r);
                ui.showModal(_('Generated configuration (committed UCI)'), [
                    E('textarea', { readonly: 'readonly', rows: 24, style: 'width:100%;font-family:monospace', wrap: 'off' },
                        r.nginx + '\n# nftables\n' + r.nft + '\n# Firewall4 input permits\n' + r.fw4),
                    E('p', {}, r.truncated ? _('Preview truncated. Full active files are in /var/run/nginx-stream-manager/.') : ''),
                    E('p', {}, _('Reserved routing table: 20100 · priority: 10100 · mark: 0x40000000/0x40000000')),
                    E('div', { 'class': 'right' }, E('button', { 'class': 'cbi-button', click: ui.hideModal }, _('Close')))
                ]);
            });
        }) }, _('Preview')));
        draw(initial);
        poll.add(function() { return statusCall().then(draw).catch(function(e) {
            stateBox.replaceChildren(E('p', { 'class': 'alert-message warning' }, e.message));
        }); }, 5);
        return E('div', { 'class': 'cbi-map' }, [E('h2', {}, _('Nginx Stream Manager')), stateBox, buttons, detailBox,
            E('p', {}, _('A reload drains old workers for up to 15 seconds. Long-lived TCP/UDP sessions may disconnect. Backend health detection is passive, not an active probe.')),
            E('p', {}, _('Configuration → Enable manager controls operation; Enable at boot separately controls startup after reboot.'))]);
    }
});
