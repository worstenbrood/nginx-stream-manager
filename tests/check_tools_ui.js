// Exercise rename preview, invalidation and selector refresh in the actual LuCI view.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const elements = [];
function E(tag, attrs, children) {
    const el = { tag, attrs: attrs || {}, children: [], value: '', disabled: false, handlers: {},
        appendChild(child) { this.children.push(child); },
        replaceChildren(...items) { this.children = items; },
        addEventListener(event, fn) { const previous = this.handlers[event]; this.handlers[event] = () => { if (previous) previous(); fn(); }; }
    };
    if (Array.isArray(children)) el.children = children;
    else if (children !== undefined) el.children = [children];
    elements.push(el); return el;
}
let renamed = false, unloaded = false;
const requests = [];
const data = () => ({ ok: true, items: [], sections: [{ type: 'service', id: renamed ? 'new_service' : 'old_service' },
    { type: 'backend', id: '__proto__', pool: 'pool', address: '192.0.2.10', port: '9000' }] });
const rpc = { declare(spec) { return async (...args) => {
    assert.equal(spec.method, 'config_tools'); requests.push(args);
    if (args[0] === 'list') return data();
    if (args[0] === 'rename') return { ok: true, token: 'rename-token', diff: '-old_service\n+new_service', message: 'Preview only.' };
    if (args[0] === 'commit') { assert.equal(args[4], 'rename-token'); renamed = true; return { ok: true, message: 'Configuration saved.' }; }
    throw new Error('Unexpected action');
}; } };
const uci = {
    load() { return Promise.resolve(); }, unload() { unloaded = true; },
    sections(config, type, fn) { fn({ '.name': renamed ? 'new_service' : 'old_service', routing: 'sni' }); }
};
const ui = { createHandlerFn(context, fn) { return fn; } };
const code = fs.readFileSync(path.join(__dirname, '../packages/luci-app-nginx-stream-manager/htdocs/luci-static/resources/view/nsm/tools.js'), 'utf8');
const view = new Function('view', 'rpc', 'ui', 'uci', 'E', '_', code)({ extend: v => v }, rpc, ui, uci, E, s => s);
const button = text => elements.find(e => e.tag === 'button' && e.children.includes(text));
const field = label => elements.find(e => e.attrs['aria-label'] === label);
(async () => {
    view.render(await view.load());
    const commit = button('Commit previewed configuration');
    assert.equal(commit.disabled, true);
    field('Source service, pool or backend').value = 'old_service';
    field('New section name').value = 'new_service';
    await button('Preview rename').attrs.click();
    assert.deepStrictEqual(requests.at(-1).slice(0, 3), ['rename', 'old_service', 'new_service']);
    assert.equal(commit.disabled, false);
    field('Source service, pool or backend').value = '__proto__';
    field('Source service, pool or backend').handlers.change();
    assert.equal(button('Preview duplicate').disabled, true);
    assert.equal(commit.disabled, true);
    await button('Preview rename').attrs.click();
    assert.deepStrictEqual(requests.at(-1).slice(0, 3), ['rename', '__proto__', 'new_service']);
    field('Source service, pool or backend').value = 'old_service';
    field('Source service, pool or backend').handlers.change();
    assert.equal(button('Preview duplicate').disabled, false);
    field('New section name').handlers.input();
    assert.equal(commit.disabled, true);
    await button('Preview rename').attrs.click();
    await commit.attrs.click();
    assert.equal(commit.disabled, true);
    assert.equal(unloaded, true);
    assert.equal(field('Source service, pool or backend').children[0].attrs.value, 'new_service');
    assert.equal(field('SNI service').children[0].attrs.value, 'new_service');
    console.log('LuCI rename preview, invalidation and selector refresh: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
