"""Exercise the shipped form controller with DOM surfaces and TTN responses stubbed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_downlink_form_validates_bytes_and_waits_for_class_a_delivery(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the portal controller test")
    controller = Path(__file__).resolve().parents[1] / "static/device_downlinks.js"
    harness = tmp_path / "downlink-form.cjs"
    harness.write_text(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor() { this.value = ''; this.checked = false; this.dataset = {}; this.events = {}; this.children = []; }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute() {}
    replaceChildren(...children) { this.children = children; this.value = ''; }
    add(child) { this.children.push(child); }
    scrollIntoView() { this.scrolled = true; }
    set innerHTML(value) { throw new Error('Untrusted device/payload data must not be interpreted as HTML'); }
}
const fields = Object.fromEntries(['form', 'device', 'port', 'encoding', 'payload', 'confirmed',
    'send', 'refresh', 'message', 'size', 'preview'].map(name => [name, new Element()]));
fields.port.value = '1'; fields.encoding.value = 'text';
const panel = new Element();
panel.querySelector = selector => fields[selector.match(/data-downlink-(.+)\]/)[1]];
let ready;
const document = {querySelectorAll: () => [panel], addEventListener: (name, callback) => { ready = callback; }};
let reply, transportError;
const posts = [];
const response = data => ({ok: true, status: 200, redirected: false, json: async () => data});
const fetch = async (url, options) => {
    if (url === '/devices/downlink-targets') return response({devices: [{device_id: 'node-1', label: '<untrusted label>'}]});
    posts.push({url, options, data: JSON.parse(options.body)});
    if (transportError) throw transportError;
    return new Promise(resolve => { reply = resolve; });
};
const window = {location: {search: '?device_id=node-1'}};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    document, window, fetch, TextEncoder, URLSearchParams, TypeError, SyntaxError,
    Option: function(text, value) { this.text = text; this.value = value; }, atob,
});

(async () => {
    ready();
    await new Promise(setImmediate);
    assert.equal(fields.device.value, 'node-1');
    assert.equal(fields.device.children[1].text, '<untrusted label> (node-1)');
    fields.payload.value = 'Hello'; fields.payload.events.input();
    assert.equal(fields.send.disabled, false);
    assert.equal(fields.preview.textContent, '48 65 6C 6C 6F');
    const submitting = fields.form.events.submit({preventDefault() {}});
    assert.equal(fields.send.disabled, true);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, '/devices/node-1/downlinks');
    assert.deepEqual(posts[0].data, {f_port: 1, payload: 'Hello', encoding: 'text', confirmed: false});
    // A second click while the request is pending cannot append another message.
    await fields.form.events.submit({preventDefault() {}});
    assert.equal(posts.length, 1);
    reply(response({byte_count: 5, device_id: 'node-1', f_port: 1, status: 'queued'}));
    await submitting;
    assert.match(fields.message.textContent, /Waiting for a Class A receive window/);
    assert.match(fields.message.textContent, /Receipt is not yet confirmed/);

    fields.payload.value = 'é'.repeat(26); fields.payload.events.input();
    assert.equal(fields.send.disabled, true); // Byte limit, not character count.
    fields.payload.value = 'é'.repeat(25); fields.payload.events.input();
    assert.equal(fields.send.disabled, false);
    fields.encoding.value = 'hex'; fields.payload.value = '0x12'; fields.encoding.events.change();
    assert.equal(fields.send.disabled, true);
    fields.payload.value = '00 FF'; fields.payload.events.input();
    assert.equal(fields.send.disabled, false);
    assert.equal(fields.preview.textContent, '00 FF');
    fields.port.value = '224'; fields.port.events.input(); assert.equal(fields.send.disabled, true);
    fields.port.value = '15'; fields.port.events.input(); assert.equal(fields.send.disabled, false);
    fields.encoding.value = 'base64'; fields.payload.value = 'AP8='; fields.encoding.events.change();
    assert.equal(fields.preview.textContent, '00 FF');

    transportError = new TypeError('offline');
    await fields.form.events.submit({preventDefault() {}});
    assert.match(fields.message.textContent, /result is unknown/);
    assert.match(fields.message.textContent, /before sending again/);
    assert.equal(posts.length, 2); // No automatic retry on an uncertain queue push.
    window.selectDeviceDownlink('node-1'); assert.equal(panel.scrolled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
''', encoding="utf-8")
    result = subprocess.run([node, str(harness), str(controller)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
