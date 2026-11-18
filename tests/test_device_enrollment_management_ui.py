"""Run the shipped enrollment controller against a minimal DOM and mocked API."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_queue_filters_bulk_decisions_checks_and_revocation(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the enrollment controller check")
    harness = tmp_path / "enrollment.cjs"
    harness.write_text(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor(tag = 'div') { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.value = ''; this.attrs = {}; this.textContent = ''; }
    append(node) { this.children.push(node); }
    replaceChildren() { this.children = []; }
    addEventListener(event, fn) { this.events[event] = fn; }
    setAttribute(key, value) { this.attrs[key] = value; }
    set innerHTML(value) { throw Error('Device strings must not be interpreted as HTML'); }
}
const names = ['search','sort','select-all','selected','bulk-accept','bulk-reject','empty','queue','candidates',
               'state','remaining','count','duration','open','close','refresh','message','action-message'];
const fields = Object.fromEntries(names.map(name => [name, new Element()]));
fields.sort.value = 'newest'; fields.duration.value = '600';
const views = ['review','waiting','attention','active','rejected','all'];
const filters = views.map(name => { const node = new Element('button'); node.dataset.provisioningFilter = name; return node; });
const totals = Object.fromEntries(views.map(name => [name, new Element()]));
const panel = new Element();
panel.querySelector = selector => selector.includes('total=') ? totals[selector.match(/="(.*?)"/)[1]] : fields[selector.slice(19, -1)];
panel.querySelectorAll = () => filters;
let ready;
const rows = ['1','2','3','4'].map((n, i) => ({dev_eui: n.repeat(16), device_id: `node-<${n}>`, join_eui: '0000000000000000',
    status: i === 2 ? 'rejected' : i === 3 ? 'awaiting_join' : 'pending', first_seen_at:'2026-11-17 10:00:00',
    last_seen_at:'2026-11-17T10:01:00Z', seen_count:3, waiting_seconds:1800, attention_required:i === 3,
    connection_state:'never_connected', gateway_id:'gateway-lobby', ttn_check:null}));
const status = () => ({enabled:true,open:false,quota:10,quota_used:4,candidates:structuredClone(rows)});
const posts = [];
const fetch = async (url, options = {}) => {
    if (options.method === 'POST') {
        posts.push({url,body:JSON.parse(options.body)});
        if (url.endsWith('/bulk')) {
            const body = JSON.parse(options.body);
            rows.filter(row => body.dev_euis.includes(row.dev_eui)).forEach(row => { row.status = body.action === 'accept' ? 'awaiting_join' : 'rejected'; });
        } else {
            const [,eui,action] = url.match(/candidates\/([^/]+)\/(.+)$/);
            const row = rows.find(item => item.dev_eui === eui);
            if (action === 'check') row.ttn_check = {components:{is:'registered',ns:'registered',as:'missing',js:'registered'},session_started_at:null};
            if (action === 'revoke') row.status = 'rejected';
            if (action === 'reconsider') row.status = 'pending';
        }
    }
    return {ok:true,status:200,redirected:false,json:async () => status()};
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    document:{hidden:false,querySelectorAll:() => [panel],createElement:tag => new Element(tag),addEventListener:(event,fn) => { if(event === 'DOMContentLoaded') ready = fn; }},
    fetch,confirm:() => true,setInterval:() => 1,console,TypeError,SyntaxError
});
const flush = () => new Promise(setImmediate);
const text = node => [node.textContent,...node.children.map(text)].join(' ');
const buttons = node => [node,...node.children.flatMap(buttons)].filter(item => item.tag === 'button');
const click = async label => { const button = buttons(fields.candidates).find(node => node.textContent === label); assert.ok(button, label); button.events.click(); await flush(); };
(async () => {
    ready(); await flush();
    assert.equal(fields.candidates.children.length,2);
    assert.equal(totals.waiting.textContent,1);
    assert.match(text(fields.candidates),/node-<1>/);
    fields.search.value = '1111'; fields.search.events.input();
    assert.equal(fields.candidates.children.length,1);
    fields['select-all'].checked = true; fields['select-all'].events.change({target:fields['select-all']});
    assert.equal(fields['bulk-accept'].disabled,false);
    await fields['bulk-accept'].events.click(); await flush();
    assert.equal(posts.length,1);
    assert.deepEqual(posts[0].body.dev_euis,['1111111111111111']);
    assert.equal(rows[0].status,'awaiting_join');
    fields.search.value = ''; fields.search.events.input(); filters.find(node => node.dataset.provisioningFilter === 'waiting').events.click();
    assert.equal(fields.candidates.children.length,2);
    assert.match(text(fields.candidates),/Waiting 30m/);
    await click('Check TTN'); assert.match(text(fields.candidates),/AS: missing/);
    await click('Revoke approval'); assert.equal(rows[0].status,'rejected');
    filters.find(node => node.dataset.provisioningFilter === 'rejected').events.click();
    await click('Return to review'); assert.equal(rows[0].status,'pending');
    assert.ok(!posts.some(post => post.url.endsWith('/accept')), 'Reconsider must not grant admission');
    assert.match(text(fields['action-message']),/updated/);
    console.log('Enrollment UI controller passed');
})().catch(error => {console.error(error);process.exitCode=1;});
''', encoding="utf-8")
    controller = Path(__file__).resolve().parents[1] / "static/device_enrollment.js"
    result = subprocess.run([node, str(harness), str(controller)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
