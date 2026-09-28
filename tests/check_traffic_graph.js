// Exercise the actual graph renderer for timing averages and missing measurements.
const fs = require('fs');
const assert = require('assert');
const path = require('path');
function element(tag, attrs = {}, children = []) {
    return { tag, attrs, children: Array.isArray(children) ? children : [children],
        setAttribute(k, v) { this.attrs[k] = String(v); }, appendChild(c) { this.children.push(c); } };
}
const document = { createElementNS(ns, tag) { return element(tag); } };
const source = fs.readFileSync(path.join(__dirname, '../packages/luci-app-nginx-stream-manager/htdocs/luci-static/resources/view/nsm/traffic.js'), 'utf8');
const graph = new Function('document', 'E', '_', 'rpc', source.split('return view.extend')[0] + '\nreturn graph;')(document, element, x => x, { declare() {} });
const data = { start: 1800000000, step: 60, series: [{ upstream: '192.0.2.1:9', points: [[10, 20, 2, 1, 1, 50, 2, 200, 1], [0, 0, 0, 0, 0, 0, 0, 0, 0], [2, 3, 1, 0, 0, 40, 1, 300, 1]] }] };
for (const metric of [0, 1, 2, 3, 4, 5, 7]) {
    const rendered = graph(data, metric);
    assert(!JSON.stringify(rendered).includes('NaN'));
    const svg = rendered.children[0];
    const line = svg.children.find(c => c.tag === 'path');
    assert(line);
    if (metric === 5 || metric === 7) {
        assert.equal((line.attrs.d.match(/M/g) || []).length, 2, 'Missing timing sample must break the line');
        const dots = svg.children.filter(c => c.tag === 'circle');
        assert(dots[0].children[0].textContent.includes(metric === 5 ? '25.0 ms' : '200.0 ms'));
    }
}
console.log('Traffic graph averages and missing measurements: OK');
