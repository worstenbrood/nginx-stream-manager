'use strict';
'require view';
'require rpc';
'require uci';
'require poll';

var readTraffic = rpc.declare({ object: 'luci.nginx-stream-manager', method: 'traffic', params: ['service', 'hours', 'protocol', 'upstream'], expect: {} });
var colors = ['#2563eb', '#d97706', '#059669', '#dc2626', '#7c3aed', '#0891b2', '#be185d', '#64748b'];
function bytes(n) {
    var units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0) + ' ' + units[i];
}
function svgElement(tag, attrs, text) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs).forEach(function(k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
}
function graph(data, direction) {
    var width = 900, height = 280, left = 82, top = 18, bottom = 40, right = 18;
    var count = Math.round(Number(data.hours || 1) * 3600 / data.step);
    if (data.series.length) count = data.series[0].points.length;
    var maximum = 1;
    data.series.forEach(function(s) { s.points.forEach(function(p) { maximum = Math.max(maximum, p[direction]); }); });
    var chart = svgElement('svg', { viewBox: '0 0 ' + width + ' ' + height, role: 'img', 'aria-label': _('Traffic volume from completed sessions'), style: 'width:100%;min-width:480px;display:block' });
    chart.appendChild(svgElement('title', {}, _('Traffic volume from completed sessions')));
    function x(i) { return left + i / Math.max(1, count - 1) * (width - left - right); }
    function y(value) { return height - bottom - value / maximum * (height - top - bottom); }
    for (var row = 0; row <= 4; row++) {
        var value = maximum * row / 4;
        chart.appendChild(svgElement('line', { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: 'currentColor', opacity: '.12' }));
        chart.appendChild(svgElement('text', { x: left - 10, y: y(value) + 4, 'text-anchor': 'end', fill: 'currentColor', 'font-size': '12' }, bytes(value)));
    }
    for (var tick = 0; tick <= 4; tick++) {
        var index = Math.round((count - 1) * tick / 4);
        var time = new Date((data.start + index * data.step) * 1000);
        chart.appendChild(svgElement('text', { x: x(index), y: height - 14, 'text-anchor': tick === 4 ? 'end' : 'middle', fill: 'currentColor', 'font-size': '12' }, time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
    }
    data.series.forEach(function(s, index) {
        var line = svgElement('polyline', { points: s.points.map(function(p, i) { return x(i) + ',' + y(p[direction]); }).join(' '), fill: 'none', stroke: colors[index], 'stroke-width': '2', 'vector-effect': 'non-scaling-stroke' });
        line.appendChild(svgElement('title', {}, s.upstream));
        chart.appendChild(line);
        s.points.forEach(function(p, i) {
            if (!p[direction]) return;
            var dot = svgElement('circle', { cx: x(i), cy: y(p[direction]), r: '3', fill: colors[index] });
            dot.appendChild(svgElement('title', {}, s.upstream + ' · ' + new Date((data.start + i * data.step) * 1000).toLocaleString() + ' · ' + bytes(p[direction])));
            chart.appendChild(dot);
        });
    });
    return E('div', { style: 'overflow-x:auto' }, chart);
}
return view.extend({
    handleSave: null, handleSaveApply: null, handleReset: null,
    load: function() { return uci.load('nginx_stream_manager'); },
    render: function() {
        var services = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Load balancer') });
        uci.sections('nginx_stream_manager', 'service', function(s) {
            services.appendChild(E('option', { value: s['.name'] }, s.name || s['.name']));
        });
        var hours = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Period') }, [1, 6, 24].map(function(h) { return E('option', { value: h }, h === 1 ? _('1 hour') : h + ' ' + _('hours')); }));
        var protocol = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Protocol') }, ['all', 'TCP', 'UDP'].map(function(p) { return E('option', { value: p }, p === 'all' ? _('TCP + UDP') : p); }));
        var upstream = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Upstream server') }, E('option', { value: '' }, _('Top 8 by sent bytes')));
        var direction = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Direction') }, [E('option', { value: '0' }, _('To upstream')), E('option', { value: '1' }, _('From upstream'))]);
        var status = E('p', { role: 'status' }), content = E('div'), note = E('p');
        var busy = false, generation = 0, last = null;
        function display(data) {
            data.hours = Number(hours.value);
            var total = data.series.reduce(function(n, s) { return n + s.sent; }, 0);
            var table = E('table', { 'class': 'table' }, E('tr', { 'class': 'tr table-titles' }, [_('Upstream server'), _('Sent'), _('Received'), _('Upstream attempts'), _('Share of shown sent bytes')].map(function(t) { return E('th', { 'class': 'th' }, t); })));
            data.series.forEach(function(s, i) {
                table.appendChild(E('tr', { 'class': 'tr' }, [E('td', { 'class': 'td' }, [E('span', { style: 'color:' + colors[i] }, '● '), s.upstream]), E('td', { 'class': 'td' }, bytes(s.sent)), E('td', { 'class': 'td' }, bytes(s.received)), E('td', { 'class': 'td' }, String(s.attempts)), E('td', { 'class': 'td' }, total ? (100 * s.sent / total).toFixed(1) + '%' : '—')]));
            });
            content.replaceChildren();
            if (!data.series.length) content.appendChild(E('p', {}, _('No completed-session traffic recorded for this selection.')));
            if (data.series.length) content.appendChild(graph(data, Number(direction.value)));
            content.appendChild(table);
            note.textContent = _('Volume per time bucket; not live bandwidth.') + ' ' + _('Bucket duration:') + ' ' + data.step / 60 + ' min. ' + _('The current bucket is incomplete. Empty buckets do not prove there was no live traffic. Retries count as separate upstream attempts.');
            var messages = [];
            if (!data.enabled) messages.push(_('Collection is inactive. Enable Traffic history for this load balancer in Configuration, then Save & Apply and start/reload the manager.'));
            if (data.limited) messages.push(_('History is incomplete: a capacity limit, missing log data or an invalid record was encountered within the last 24 hours.'));
            if (data.pending) messages.push(_('Additional log records are waiting to be processed.'));
            if (!upstream.value && data.omitted) messages.push(_('Some upstreams are not shown. Select an individual upstream from the list.'));
            messages.push(_('Collector started:') + ' ' + new Date(data.started * 1000).toLocaleString());
            messages.push(_('Updated:') + ' ' + new Date(data.now * 1000).toLocaleTimeString());
            status.textContent = messages.join(' ');
        }
        function refresh() {
            if (busy || !services.value) return Promise.resolve();
            busy = true;
            var requestGeneration = generation;
            return readTraffic(services.value, Number(hours.value), protocol.value, upstream.value).then(function(data) {
                if (requestGeneration !== generation) return;
                if (!data.ok) throw new Error(data.error || _('Unable to read traffic history'));
                var selected = upstream.value;
                upstream.replaceChildren(E('option', { value: '' }, _('Top 8 by sent bytes')));
                data.endpoints.forEach(function(peer) { upstream.appendChild(E('option', { value: peer }, peer)); });
                if (selected && data.endpoints.indexOf(selected) < 0) upstream.appendChild(E('option', { value: selected }, selected));
                upstream.value = selected;
                last = data; display(data);
            }).catch(function(error) { if (requestGeneration === generation) status.textContent = error.message; }).finally(function() {
                busy = false;
                if (requestGeneration !== generation) refresh();
            });
        }
        [services, hours, protocol, upstream].forEach(function(el) { el.addEventListener('change', function() {
            generation++; last = null; content.replaceChildren();
            if (el === services || el === protocol) upstream.value = '';
            refresh();
        }); });
        direction.addEventListener('change', function() { if (last) display(last); });
        if (!services.value) status.textContent = _('Configure a load balancer first.');
        poll.add(refresh, 10); refresh();
        return E('div', { 'class': 'cbi-map' }, [E('h2', {}, _('Traffic')),
            E('p', {}, _('Per-upstream history for completed TCP/UDP sessions. Long-running sessions appear only after they end. History is kept in RAM for up to 24 hours and is lost on reboot. Client IP addresses are not recorded by this feature.')),
            E('div', { style: 'display:flex;gap:.6em;flex-wrap:wrap' }, [services, hours, protocol, upstream, direction]), status, content, note]);
    }
});
