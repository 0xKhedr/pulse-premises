const html = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function requestJSON(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
    return data;
}
let profileRecords = [], sensorRecords = [], editorRecord;
function renderProfiles() {
    const search = document.getElementById('profileSearch').value.toLowerCase();
    const records = profileRecords.filter(p => `${p.profile_name} ${p.profile_code} ${p.node_type}`.toLowerCase().includes(search));
    document.getElementById('profileList').innerHTML = records.map(p => `<article class="card"><span class="pill">${html(p.status)} · v${html(p.profile_version)}</span><h2>${html(p.profile_name)}</h2><p>${html(p.description || p.node_type)}</p><p>${html(p.field_count)} fields · ${html(p.rule_count)} rules · ${html(p.device_count)} devices</p><p class="muted">${html(p.profile_code)}</p><a class="btn" href="/sensor-profile-editor?profile_id=${Number(p.id)}">Edit profile</a> <a class="btn secondary" href="/sensor-profile-editor/advanced?profile_id=${Number(p.id)}">Advanced</a></article>`).join('') || '<div class="empty-state">No matching profiles.</div>';
}
async function loadProfiles() {
    try { profileRecords = (await requestJSON('/sensor-profiles')).profiles; renderProfiles(); document.getElementById('cloneSource').innerHTML = profileRecords.map(p => `<option value="${Number(p.id)}">${html(p.profile_name)}</option>`).join(''); document.getElementById('profileMessage').textContent = `${profileRecords.length} profiles available`; }
    catch (error) { document.getElementById('profileMessage').textContent = error.message; }
}
function initProfiles() {
    document.getElementById('profileSearch').addEventListener('input', renderProfiles);
    document.getElementById('cloneProfileForm').addEventListener('submit', async event => {
        event.preventDefault();
        try {
            const result = await requestJSON(`/sensor-profiles/${Number(document.getElementById('cloneSource').value)}/clone`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({new_profile_name:document.getElementById('cloneName').value, new_profile_code:document.getElementById('cloneCode').value, activate:document.getElementById('cloneActive').checked})});
            location.href = `/sensor-profile-editor?profile_id=${Number(result.profile.id)}`;
        } catch (error) { document.getElementById('profileMessage').textContent = error.message; }
    });
    loadProfiles();
}
function catalogTab(edit) {
    document.getElementById('catalogPanel').hidden = edit; document.getElementById('catalogFormPanel').hidden = !edit;
    document.getElementById('catalogTab').setAttribute('aria-selected', String(!edit)); document.getElementById('catalogFormTab').setAttribute('aria-selected', String(edit));
}
function renderCatalog() {
    const search = document.getElementById('sensorSearch').value.toLowerCase();
    document.getElementById('sensorList').innerHTML = sensorRecords.filter(s => `${s.model} ${s.sensor_code} ${s.manufacturer}`.toLowerCase().includes(search)).map(s => `<article class="card"><span class="pill">${html(s.protocol)} · ${s.enabled ? 'Enabled' : 'Disabled'}</span><h2>${html(s.model)}</h2><p>${html(s.manufacturer || 'Generic')} · ${html(s.use_case)}</p><p>${html(s.description || s.sensor_code)}</p><p class="muted">Used by ${Number(s.profile_count)} profiles</p><button data-sensor-edit="${Number(s.id)}">Edit sensor</button></article>`).join('') || '<div class="empty-state">No matching sensors.</div>';
}
async function loadCatalog() {
    try { sensorRecords = (await requestJSON('/sensor-catalog')).sensors; renderCatalog(); }
    catch (error) { document.getElementById('catalogMessage').textContent = error.message; }
}
function newSensor() { document.getElementById('sensorForm').reset(); document.querySelector('#sensorForm [name=id]').value = ''; document.getElementById('sensorFormTitle').textContent = 'Add sensor'; }
function initCatalog() {
    document.getElementById('sensorSearch').addEventListener('input', renderCatalog);
    document.getElementById('sensorList').addEventListener('click', event => {
        const button = event.target.closest('[data-sensor-edit]'); if (!button) return;
        const sensor = sensorRecords.find(s => s.id === Number(button.dataset.sensorEdit));
        const form = document.getElementById('sensorForm');
        [...form.elements].filter(el => el.name).forEach(el => { if (el.type === 'checkbox') el.checked = sensor[el.name]; else el.value = sensor[el.name] ?? ''; });
        document.getElementById('sensorFormTitle').textContent = 'Edit sensor'; catalogTab(true);
    });
    document.getElementById('sensorForm').addEventListener('submit', async event => {
        event.preventDefault(); const form = event.target; const data = Object.fromEntries(new FormData(form));
        data.enabled = form.elements.enabled.checked; const id = data.id; delete data.id;
        try { await requestJSON(`/sensor-catalog${id ? '/' + Number(id) : ''}`, {method: id ? 'PUT' : 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)}); document.getElementById('catalogMessage').textContent = 'Sensor saved.'; await loadCatalog(); catalogTab(false); newSensor(); }
        catch (error) { document.getElementById('catalogMessage').textContent = error.message; }
    }); loadCatalog();
}
async function loadAudit() {
    const params = new URLSearchParams(new FormData(document.getElementById('auditFilters')));
    [...params].forEach(([key,value]) => { if (!value) params.delete(key); });
    document.getElementById('auditExport').href = `/audit-log/export.csv?${params}`;
    try {
        const records = await requestJSON(`/audit-log?${params}`);
        document.getElementById('auditMessage').textContent = `${records.length} events`;
        document.getElementById('auditRows').innerHTML = records.map(r => `<tr><td>${html(r.timestamp)}</td><td>${html(r.actor)}</td><td>${html(r.action)}</td><td>${html(r.target_type)} ${html(r.target_id)}</td><td>${html(r.message)}</td></tr>`).join('') || '<tr><td colspan="5">No activity matches these filters.</td></tr>';
    } catch (error) { document.getElementById('auditMessage').textContent = error.message; }
}
function resetAudit() { document.getElementById('auditFilters').reset(); loadAudit(); }
function initAudit() { document.getElementById('auditFilters').addEventListener('submit', event => { event.preventDefault(); loadAudit(); }); loadAudit(); }
function editorTab(id, button) {
    if (editorRecord) {
        try {
            if (!document.getElementById('profileAdvanced').hidden) { editorRecord = JSON.parse(document.getElementById('profileDefinition').value); fillEditor(); }
            else syncEditor();
        } catch (error) { document.getElementById('editorMessage').textContent = `Invalid JSON: ${error.message}`; return; }
    }
    document.querySelectorAll('.editor-section').forEach(panel => { panel.hidden = panel.id !== id; });
    document.querySelectorAll('.section-tabs button').forEach(tab => tab.setAttribute('aria-selected', String(tab === button)));
}
function fillEditor() {
    const mapping = {profileName:'profile_name',profileCode:'profile_code',profileNodeType:'node_type',profileInterval:'uplink_interval_seconds',profilePort:'f_port',profileStatus:'status',profileDescription:'description'};
    Object.entries(mapping).forEach(([id,key]) => { document.getElementById(id).value = editorRecord[key] ?? ''; });
    document.getElementById('profileDefinition').value = JSON.stringify(editorRecord, null, 2);
    document.getElementById('fieldRows').innerHTML = (editorRecord.fields || []).map(f => `<tr><td>${html(f.field_key)}</td><td>${html(f.label)}</td><td>${html(f.data_type)}</td><td>${html(f.unit || '—')}</td></tr>`).join('');
    document.getElementById('ruleList').innerHTML = (editorRecord.rules || []).map(r => `<p><span class="pill">${html(r.severity)}</span> ${html(r.rule_name || r.rule_code)} · ${html(r.field_key)} ${html(r.operator)} ${html(r.threshold_value ?? r.expected_boolean ?? '')}</p>`).join('') || '<p>No alarm rules configured.</p>';
}
function syncEditor() {
    if (!document.getElementById('profileAdvanced').hidden) return JSON.parse(document.getElementById('profileDefinition').value);
    Object.assign(editorRecord, {profile_name:document.getElementById('profileName').value, node_type:document.getElementById('profileNodeType').value, uplink_interval_seconds:Number(document.getElementById('profileInterval').value), f_port:Number(document.getElementById('profilePort').value), status:document.getElementById('profileStatus').value, description:document.getElementById('profileDescription').value});
    document.getElementById('profileDefinition').value = JSON.stringify(editorRecord, null, 2); return editorRecord;
}
async function loadEditor() {
    const id = document.getElementById('editorProfile').value; if (!id) return;
    try { editorRecord = (await requestJSON(`/sensor-profiles/${Number(id)}?include_formatter=true`)).profile; fillEditor(); document.querySelector('[onclick="saveEditor()"]').disabled = editorRecord.is_system; document.getElementById('editorMessage').textContent = editorRecord.is_system ? 'Built-in profile. Create a copy from the profile list to edit.' : ''; }
    catch (error) { document.getElementById('editorMessage').textContent = error.message; }
}
async function initEditor(advanced = false) {
    try {
        const profiles = (await requestJSON('/sensor-profiles')).profiles;
        const select = document.getElementById('editorProfile'); select.innerHTML = profiles.map(p => `<option value="${Number(p.id)}">${html(p.profile_name)}</option>`).join('');
        const requested = new URLSearchParams(location.search).get('profile_id'); if (profiles.some(p => String(p.id) === requested)) select.value = requested;
        select.addEventListener('change', loadEditor); await loadEditor();
        if (advanced) editorTab('profileAdvanced', document.getElementById('advancedTab'));
    } catch (error) { document.getElementById('editorMessage').textContent = error.message; }
}
async function validateEditor() {
    try { const data = await requestJSON('/sensor-profiles/validate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(syncEditor())}); document.getElementById('editorMessage').textContent = data.valid ? 'Definition is valid.' : JSON.stringify(data.errors || data); }
    catch (error) { document.getElementById('editorMessage').textContent = error.message; }
}
async function saveEditor() {
    try { await requestJSON(`/sensor-profiles/${Number(document.getElementById('editorProfile').value)}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(syncEditor())}); await loadEditor(); document.getElementById('editorMessage').textContent = 'Profile saved.'; }
    catch (error) { document.getElementById('editorMessage').textContent = error.message; }
}
