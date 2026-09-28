// Regression: modal GridSection clones must populate their own pool choices.
const fs = require('fs');
const assert = require('assert');
const path = require('path');
let pools = [{ '.name': 'old', name: 'Old pool' }];
class Section {
    constructor(kind) { this.kind = kind; this.options = []; }
    tab() {}
    taboption(tab, ...args) { return this.option(...args); }
    option(type, key) {
        const option = { key, keylist: [], vallist: [],
            value(k, v) { this.keylist.push(k); this.vallist.push(v); },
            depends() {}, super() { return Promise.resolve(); } };
        this.options.push(option); return option;
    }
}
class Map {
    constructor() { this.sections = []; }
    section(type, name) { const s = new Section(name); this.sections.push(s); return s; }
    render() { return this; }
}
const uci = { sections(config, type, fn) { (type === 'pool' ? pools : []).forEach(fn); } };
const form = { Map, GridSection: Section, NamedSection: Section };
const source = fs.readFileSync(path.join(__dirname, '../packages/luci-app-nginx-stream-manager/htdocs/luci-static/resources/view/nsm/config.js'), 'utf8');
const view = new Function('view', 'form', 'uci', '_', source)({ extend(v) { return v; } }, form, uci, s => s);
const rendered = view.render();
for (const section of rendered.sections) {
    const original = section.options.find(o => o.key === 'pool');
    if (!original) continue;
    original.load('original');
    const clone = Object.assign({}, original);
    pools = [{ '.name': 'new_pool', name: 'New pool' }];
    clone.load('modal');
    assert.deepStrictEqual(clone.keylist, ['new_pool']);
    assert.deepStrictEqual(clone.vallist, ['New pool']);
    assert.notStrictEqual(clone.keylist, original.keylist);
}
console.log('LuCI modal pool choices: OK');
