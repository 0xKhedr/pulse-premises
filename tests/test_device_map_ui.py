import shutil
import subprocess
from pathlib import Path

import pytest


def test_map_status_hover_accessibility_and_saved_presentation(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the map controller test")
    controller = Path(__file__).resolve().parents[1] / "static/device_map.js"
    harness = tmp_path / "map.cjs"
    harness.write_text(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.events = {}; this.attributes = {};
        this.dataset = {}; this.style = {}; this.value = ''; this._text = '';
        this.classList = {add: (...names) => { this.className += ' ' + names.join(' '); }};
    }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren(...children) { this.children = children; this._text = ''; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, fn) { this.events[name] = fn; }
    click() { this.events.click?.({}); this.onclick?.({}); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
    set innerHTML(value) { throw new Error('Device data must never become HTML'); }
    getBoundingClientRect() { return {left: 100, top: 300, bottom: 330, width: 280, height: 230}; }
}
const body = new Element('body'), posts = [], saved = [];
let resolveSave;
const window = {innerWidth: 1200, innerHeight: 900, addEventListener() {}, setInterval() {}};
const document = {body, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    window, document, Date, fetch: (url, options) => {
        posts.push({url, data: JSON.parse(options.body)});
        return new Promise(resolve => { resolveSave = resolve; });
    }
});
const map = window.DeviceMap;
const response = data => ({ok: true, json: async () => data});
const event = {preventDefault() {}};
(async () => {
    const device = {device_id: 'node ?<unsafe>', label: 'A long device name with <markup>',
        node_type: 'environment', connection_state: 'online',
        telemetry: {temperature: 0, humidity: 51.2, battery_percent: 0, rssi: -72}};
    assert.equal(map.state(device), 'online');
    assert.equal(map.state({...device, telemetry: {alarm_active: 'true'}}), 'alert');
    assert.equal(map.state({...device, telemetry: {battery_status: 'critical'}}), 'alert');
    assert.equal(map.state({...device, connection_state: 'offline', telemetry: {alarm_active: true}}), 'offline');
    assert.equal(map.state({...device, connection_state: 'stale'}), 'stale');
    assert.equal(map.state({}), 'never_connected');
    assert.equal(map.state({...device, telemetry: {alarm_active: true}}, {showAlerts: false}), 'online');
    assert.equal(map.state({...device, last_seen_at: new Date(Date.now() - 360000).toISOString(), offline_after_seconds: 300}), 'offline');
    assert.equal(map.iconKey(device), 'thermometer');
    assert.equal(map.iconKey({...device, map_icon: 'door'}), 'door');
    assert.equal(map.iconKey({...device, map_icon: '<svg>'}), 'thermometer');
    assert.ok(map.readings(device).some(([key, value]) => key === 'Temperature' && value === '0 °C'));
    assert.ok(map.readings(device).some(([key, value]) => key === 'Battery' && value === '0 %'));
    assert.equal(map.readings({telemetry: {cam_reading: '004827'}})[0][1], '004827');
    assert.equal(map.readings({telemetry: {custom_sensor: 123}})[0][1], '123');
    assert.equal(map.readings({display_telemetry: [{field_key: 'contact', label: 'Door open', value: false}]})[0][1], 'No');
    const marker = new Element('div');
    map.renderMarker(marker, device, {room_name: 'Lobby'});
    assert.equal(marker.dataset.state, 'online');
    assert.equal(marker.children[1].textContent, device.label);
    assert.equal(marker.tabIndex, 0);
    marker.events.mouseenter();
    const tooltip = body.children[0];
    assert.equal(tooltip.hidden, false);
    assert.ok(tooltip.textContent.includes('0 °C'));
    assert.ok(tooltip.textContent.includes(device.label));
    assert.ok(tooltip.textContent.includes('Lobby'));
    assert.equal(marker.attributes['aria-describedby'], 'device-map-tooltip');
    marker.events.mouseleave(); assert.equal(tooltip.hidden, true);
    map.renderMarker(marker, {...device, alarm_message: 'Restricted alarm'}, null, {showReadings: false, showAlerts: false});
    marker.events.focus();
    assert.ok(!tooltip.textContent.includes('51.2'));
    assert.ok(!tooltip.textContent.includes('Restricted alarm'));
    let clicked = 0; marker.onclick = () => { clicked++; };
    marker.events.keydown({key: 'Enter', preventDefault() {}}); assert.equal(clicked, 1);
    const container = new Element('div');
    device.label = 'Lobby sensor';
    map.mountEditor(container, device, data => saved.push(data));
    const details = container.children[0], form = details.children[1];
    const input = form.children.find(element => element.tag === 'input');
    const palette = form.children.find(element => element.className === 'device-map-icon-picker');
    const message = form.children.find(element => element.className === 'device-map-save-message');
    assert.equal(palette.children.length, 12);
    palette.children.find(element => element.dataset.icon === 'humidity').click();
    let pending = form.events.submit(event);
    assert.equal(input.disabled, true);
    await form.events.submit(event); assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].data, {map_icon: 'humidity'});
    assert.equal(posts[0].url, '/devices/node%20%3F%3Cunsafe%3E/presentation');
    resolveSave(response({...device, map_icon: 'humidity'})); await pending;
    assert.equal(input.disabled, false); assert.equal(saved.length, 1);
    input.value = 'Lobby West';
    map.mountEditor(container, {...device}, data => saved.push(data));
    assert.equal(container.children[0], details); assert.equal(input.value, 'Lobby West');
    pending = form.events.submit(event);
    assert.deepEqual(posts[1].data, {label: 'Lobby West'});
    resolveSave({ok: false, json: async () => ({detail: 'Administrator access required'})}); await pending;
    assert.equal(message.textContent, 'Administrator access required');
    assert.equal(saved.length, 1);
    pending = form.events.submit(event);
    resolveSave(response({...device, label: 'Lobby West', map_icon: 'humidity'})); await pending;
    assert.equal(saved.length, 2); assert.equal(input.value, 'Lobby West');
    await form.events.submit(event); assert.equal(posts.length, 3);
    input.value = '<script>'; await form.events.submit(event); assert.equal(posts.length, 3);
})().catch(error => { console.error(error); process.exitCode = 1; });
''', encoding="utf-8")
    result = subprocess.run([node, str(harness), str(controller)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
