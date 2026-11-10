"""Exercise the shipped configuration controller with a simulated browser DOM."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_configuration_form_validates_disables_duplicates_and_preserves_uncertain_state(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the configuration controller test")
    controller = Path(__file__).resolve().parents[1] / "static/device_configuration.js"
    harness = tmp_path / "configuration.cjs"
    harness.write_text(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor() { this.value = ''; this.dataset = {}; this.events = {}; }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute() {}
    set innerHTML(value) { throw new Error('Device data must not be interpreted as HTML'); }
}
const names = ['configuration-form', 'send-interval', 'configuration-send', 'configuration-refresh',
               'configuration-message', 'last-queued'];
const fields = Object.fromEntries(names.map(name => [name, new Element()]));
const panel = new Element(); panel.dataset.deviceId = 'node ?<unsafe>';
panel.querySelector = selector => fields[selector.slice(6, -1)];
let ready, resolvePost, transportError, unavailable = false, loadError = false;
const posts = [];
const response = data => ({ok: true, status: 200, json: async () => data});
const config = () => ({can_queue: !unavailable, unavailable_reason: unavailable ? 'Preview mode' : null,
    send_rate: {default_seconds: 30, last_queued: {send_interval_seconds: 300, queued_at: '2026-11-09 12:00:00'}}});
const fetch = async (url, options) => {
    assert.ok(url.startsWith('/devices/node%20%3F%3Cunsafe%3E/configuration'));
    if (!options.method) {
        if (loadError) return {ok: false, status: 403};
        return response(config());
    }
    posts.push({url, data: JSON.parse(options.body)});
    if (transportError) throw transportError;
    return new Promise(resolve => { resolvePost = resolve; });
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    document: {querySelector: () => panel, addEventListener: (name, fn) => { ready = fn; }},
    fetch, TypeError, SyntaxError
});
(async () => {
    ready(); await new Promise(setImmediate);
    const input = fields['send-interval'], send = fields['configuration-send'];
    const form = fields['configuration-form'], refresh = fields['configuration-refresh'];
    const message = fields['configuration-message'], last = fields['last-queued'];
    assert.equal(input.value, 300);
    assert.match(last.textContent, /Last queued: 300 seconds/);
    assert.match(last.textContent, /not confirmed/);
    for (const invalid of ['', '29', '86401', '30.5', 'NaN']) {
        input.value = invalid; input.events.input(); assert.equal(send.disabled, true);
    }
    input.value = '86400'; input.events.input(); assert.equal(send.disabled, false);
    let pending = form.events.submit({preventDefault() {}});
    assert.equal(send.disabled, true); assert.equal(input.disabled, true); assert.equal(refresh.disabled, true);
    await form.events.submit({preventDefault() {}}); assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].data, {send_interval_seconds: 86400});
    assert.match(posts[0].url, /configuration\/send-rate$/);
    resolvePost(response({status: 'queued', send_interval_seconds: 86400})); await pending;
    assert.match(last.textContent, /86400 seconds/);
    assert.match(message.textContent, /existing rate/);
    assert.match(message.textContent, /not confirmed/);

    const previousText = last.textContent;
    transportError = new TypeError('offline'); input.value = '600'; input.events.input();
    await form.events.submit({preventDefault() {}});
    assert.match(message.textContent, /result is unknown/);
    assert.equal(posts.length, 2); assert.equal(last.textContent, previousText);
    transportError = null;
    input.value = '60'; input.events.input();
    pending = form.events.submit({preventDefault() {}});
    resolvePost({ok: false, status: 409, json: async () => ({detail: 'Approval required'})}); await pending;
    assert.equal(message.textContent, 'Approval required'); assert.equal(last.textContent, previousText);

    unavailable = true; await refresh.events.click();
    assert.equal(input.disabled, true); assert.equal(send.disabled, true);
    assert.equal(message.textContent, 'Preview mode');
    unavailable = false; await refresh.events.click(); assert.equal(send.disabled, false);
    loadError = true; await refresh.events.click();
    assert.equal(send.disabled, true); assert.match(message.textContent, /Administrator access/);
})().catch(error => {console.error(error); process.exitCode = 1;});
''', encoding="utf-8")
    result = subprocess.run([node, str(harness), str(controller)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
