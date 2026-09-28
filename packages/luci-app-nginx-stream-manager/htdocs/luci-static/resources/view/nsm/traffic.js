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
    function metricValue(p) { return direction === 5 ? (p[6] ? p[5]/p[6] : null) : direction === 7 ? (p[8] ? p[7]/p[8] : null) : (p[direction] || 0); }
    function format(v) { return direction < 2 ? bytes(v) : v.toFixed(direction === 5 || direction === 7 ? 1 : 0) + (direction === 5 || direction === 7 ? ' ms' : ''); }
    data.series.forEach(function(s) { s.points.forEach(function(p) { maximum = Math.max(maximum, metricValue(p) || 0); }); });
    var chart = svgElement('svg', { viewBox: '0 0 ' + width + ' ' + height, role: 'img', 'aria-label': _('Upstream measurements from completed sessions'), style: 'width:100%;min-width:480px;display:block' });
    chart.appendChild(svgElement('title', {}, _('Upstream measurements from completed sessions')));
    function x(i) { return left + i / Math.max(1, count - 1) * (width - left - right); }
    function y(value) { return height - bottom - value / maximum * (height - top - bottom); }
    for (var row = 0; row <= 4; row++) {
        var value = maximum * row / 4;
        chart.appendChild(svgElement('line', { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: 'currentColor', opacity: '.12' }));
        chart.appendChild(svgElement('text', { x: left - 10, y: y(value) + 4, 'text-anchor': 'end', fill: 'currentColor', 'font-size': '12' }, format(value)));
    }
    for (var tick = 0; tick <= 4; tick++) {
        var index = Math.round((count - 1) * tick / 4);
        var time = new Date((data.start + index * data.step) * 1000);
        chart.appendChild(svgElement('text', { x: x(index), y: height - 14, 'text-anchor': tick === 4 ? 'end' : 'middle', fill: 'currentColor', 'font-size': '12' }, time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
    }
    data.series.forEach(function(s, index) {
        var line = svgElement('path', { d: s.points.map(function(p, i) { var v = metricValue(p); return v === null ? '' : (i && metricValue(s.points[i-1]) !== null ? 'L' : 'M') + x(i) + ',' + y(v); }).join(' '), fill: 'none', stroke: colors[index], 'stroke-width': '2', 'vector-effect': 'non-scaling-stroke' });
        line.appendChild(svgElement('title', {}, s.upstream));
        chart.appendChild(line);
        s.points.forEach(function(p, i) {
            if (!metricValue(p)) return;
            var dot = svgElement('circle', { cx: x(i), cy: y(metricValue(p)), r: '3', fill: colors[index] });
            dot.appendChild(svgElement('title', {}, s.upstream + ' · ' + new Date((data.start + i * data.step) * 1000).toLocaleString() + ' · ' + format(metricValue(p))));
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
        var hours = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Period') }, [1, 6, 24, 168, 720].map(function(h) { return E('option', { value: h }, h === 1 ? _('1 hour') : h === 24 ? _('1 day') : h >= 24 ? h/24 + ' ' + _('days') : h + ' ' + _('hours')); }));
        var protocol = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Protocol') }, ['all', 'TCP', 'UDP'].map(function(p) { return E('option', { value: p }, p === 'all' ? _('TCP + UDP') : p); }));
        var upstream = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Upstream server') }, E('option', { value: '' }, _('Top 8 by sent bytes')));
        var direction = E('select', { 'class': 'cbi-input-select', 'aria-label': _('Metric') }, [[0, 'To upstream'], [1, 'From upstream'], [2, 'Upstream attempts'], [3, 'Failed attempts / final errors'], [4, 'Retries from this upstream'], [5, 'Mean connection time'], [7, 'Mean completed session duration']].map(function(v) { return E('option', { value: v[0] }, _(v[1])); }));
        var status = E('p', { role: 'status' }), content = E('div'), note = E('p');
        var busy = false, generation = 0, last = null;
        function display(data) {
            data.hours = Number(hours.value);
            var total = data.series.reduce(function(n, s) { return n + s.sent; }, 0);
            var table = E('table', { 'class': 'table' }, E('tr', { 'class': 'tr table-titles' }, [_('Upstream server'), _('Sent'), _('Received'), _('Upstream attempts'), _('Share of shown sent bytes'), _('Failures'), _('Retries'), _('Connect ms'), _('Session ms')].map(function(t) { return E('th', { 'class': 'th' }, t); })));
            data.series.forEach(function(s, i) {
                table.appendChild(E('tr', { 'class': 'tr' }, [E('td', { 'class': 'td' }, [E('span', { style: 'color:' + colors[i] }, '● '), s.upstream]), E('td', { 'class': 'td' }, bytes(s.sent)), E('td', { 'class': 'td' }, bytes(s.received)), E('td', { 'class': 'td' }, String(s.attempts)), E('td', { 'class': 'td' }, total ? (100 * s.sent / total).toFixed(1) + '%' : '—')].concat([s.failures || 0, s.retries || 0, s.connect_samples ? (s.connect_ms/s.connect_samples).toFixed(1) : '—', s.sessions ? (s.session_ms/s.sessions).toFixed(1) : '—'].map(function(v) { return E('td', { 'class': 'td' }, String(v)); }))));
            });
            content.replaceChildren();
            if (!data.series.length) content.appendChild(E('p', {}, _('No completed-session traffic recorded for this selection.')));
            if (data.series.length) content.appendChild(graph(data, Number(direction.value)));
            content.appendChild(table);
            note.textContent = _('Completed-session measurements; not live bandwidth. Session duration belongs to the final selected upstream. Failed attempts use missing connection timings or final 500/502/503 status. Older logs have no timing/error measurements.') + ' ' + _('Bucket duration:') + ' ' + data.step / 60 + ' min. ' + _('The current bucket is incomplete. Empty buckets do not prove there was no live traffic. Retries count as separate upstream attempts.');
            var messages = [];
            if (!data.enabled) messages.push(_('Collection is inactive. Enable Traffic history for this load balancer in Configuration, then Save & Apply and start/reload the manager.'));
            if (data.limited) messages.push(_('History is incomplete: a capacity limit, missing log data or an invalid record was encountered within the last 24 hours.'));
            if (data.pending) messages.push(_('Additional log records are waiting to be processed.'));
            if (!upstream.value && data.omitted) messages.push(_('Some upstreams are not shown. Select an individual upstream from the list.'));
            if (data.endpoint_omitted) messages.push(_('The endpoint selector is limited to the 64 largest series. Additional endpoints can be queried individually through the traffic RPC.'));
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
        var csv = E('button', { 'class': 'cbi-button', click: function() {
            if (!last) return;
            var rows = ['timestamp,upstream,sent,received,attempts,failures,retries,connect_ms,connect_samples,session_ms,sessions'];
            last.series.forEach(function(s) { s.points.forEach(function(p, i) { rows.push([new Date((last.start+i*last.step)*1000).toISOString(), s.upstream].concat(p).map(function(v) { return '"' + String(v).replace(/"/g, '""') + '"'; }).join(',')); }); });
            var url = URL.createObjectURL(new Blob([rows.join('\n')+'\n'], { type: 'text/csv' }));
            var link = E('a', { href: url, download: 'nsm-traffic.csv' }); document.body.appendChild(link); link.click(); link.remove(); setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
        } }, _('Export shown series (CSV)'));
        if (!services.value) status.textContent = _('Configure a load balancer first.');
        poll.add(refresh, 10); refresh();
        return E('div', { 'class': 'cbi-map' }, [E('h2', {}, _('Traffic')),
            E('p', {}, _('Per-upstream history for completed TCP/UDP sessions. Long-running sessions appear only after they end. RAM retains up to 24 hours; optional mounted-drive storage retains hourly history for up to 30 days. Longer periods update every five minutes. Client IP addresses are not recorded.')),
            E('div', { style: 'display:flex;gap:.6em;flex-wrap:wrap' }, [services, hours, protocol, upstream, direction, csv]), status, content, note]);
    }
});
