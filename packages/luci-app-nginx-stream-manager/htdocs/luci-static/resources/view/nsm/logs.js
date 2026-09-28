'use strict';
'require view';
'require rpc';
'require uci';
'require poll';

var readLog = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'logs', params: ['service', 'kind', 'lines'], expect: {} });
return view.extend({
    handleSave: null, handleSaveApply: null, handleReset: null,
    load: function() { return uci.load('nginx_stream_manager'); },
    render: function() {
        var paused = false, busy = false, lastText = null, generation = 0;
        var service = E('select', { 'class': 'cbi-input-select' }, E('option', { value: 'manager' }, _('Manager')));
        uci.sections('nginx_stream_manager', 'service', function(s) {
            service.appendChild(E('option', { value: s['.name'] }, s.name || s['.name']));
        });
        var kind = E('select', { 'class': 'cbi-input-select' }, [E('option', { value: 'error' }, _('Errors')), E('option', { value: 'access' }, _('Access'))]);
        var count = E('select', { 'class': 'cbi-input-select' }, [100, 500, 1000].map(function(n) { return E('option', { value: n, selected: n === 500 ? '' : null }, String(n)); }));
        var status = E('span', { role: 'status' }, _('Live'));
        var area = E('textarea', { readonly: 'readonly', wrap: 'off', style: 'width:100%;height:65vh;font-family:monospace;white-space:pre' });
        var pause = E('button', { 'class': 'cbi-button', click: function() {
            paused = !paused; pause.textContent = paused ? _('Resume') : _('Pause');
            status.textContent = paused ? _('Paused') : _('Live');
            if (!paused) refresh();
        } }, _('Pause'));
        function refresh() {
            if (paused || busy) return Promise.resolve();
            busy = true;
            var requestGeneration = generation;
            return readLog(service.value, service.value === 'manager' ? 'error' : kind.value, Number(count.value)).then(function(r) {
                if (requestGeneration !== generation || paused) return;
                if (!r.ok) { status.textContent = r.error || _('Read failed'); return; }
                var text = r.text || '';
                if (text !== lastText) {
                    var pin = lastText === null || area.scrollHeight - area.scrollTop - area.clientHeight < 40;
                    var top = area.scrollTop, left = area.scrollLeft;
                    area.value = text; lastText = text;
                    area.scrollTop = pin ? area.scrollHeight : top; area.scrollLeft = left;
                }
                status.textContent = _('Live') + (r.byte_limited ? ' · ' + _('8 KB output limit reached') : '');
            }).catch(function(e) { status.textContent = e.message; }).finally(function() { busy = false; });
        }
        [service, kind, count].forEach(function(el) { el.addEventListener('change', function() {
            generation++; lastText = null; kind.disabled = service.value === 'manager'; refresh();
        }); });
        kind.disabled = true;
        poll.add(refresh, 2);
        refresh();
        return E('div', { 'class': 'cbi-map' }, [E('h2', {}, _('Stream logs')),
            E('div', { style: 'display:flex;gap:.6em;align-items:center;flex-wrap:wrap;margin-bottom:1em' }, [service, kind, count, pause, status]), area,
            E('p', {}, _('Refresh every 2 seconds. Only the file tail is read. Access records appear when sessions finish.'))]);
    }
});
