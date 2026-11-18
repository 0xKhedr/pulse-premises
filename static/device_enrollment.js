document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-provisioning-panel]').forEach(panel => {
        const field = name => panel.querySelector(`[data-provisioning-${name}]`);
        let status = null, deadline = 0, busy = false, requestError = '', signature = '', filter = 'review';
        const selected = new Set();
        const groups = {
            review: row => ['pending', 'registration_failed', 'registering'].includes(row.status),
            waiting: row => ['awaiting_join', 'revoking', 'revocation_failed'].includes(row.status),
            attention: row => row.attention_required, active: row => row.status === 'online',
            rejected: row => row.status === 'rejected', all: () => true
        };
        const names = {pending: 'Pending approval', registering: 'Registering', registration_failed: 'Registration failed',
            awaiting_join: 'Accepted · awaiting first uplink', online: 'First uplink received', rejected: 'Rejected',
            revoking: 'Revoking approval', revocation_failed: 'Revocation needs retry'};
        const add = (parent, tag, text, className = '') => {
            const node = document.createElement(tag);
            node.textContent = text; node.className = className; parent.append(node); return node;
        };
        const date = value => {
            if (!value) return null;
            const normalized = value.replace(' ', 'T');
            return new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(normalized) ? normalized : normalized + 'Z');
        };
        const formatDate = value => { const d = date(value); return d && !Number.isNaN(d.getTime()) ? d.toLocaleString() : '—'; };
        const elapsed = seconds => seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : `${Math.floor(seconds / 60)}m`;

        function visibleRows() {
            const search = field('search').value.trim().toLowerCase();
            const rows = (status?.candidates || []).filter(row => groups[filter](row) && (!search ||
                [row.device_id, row.dev_eui, row.join_eui, row.gateway_id, row.decided_by].some(value => String(value || '').toLowerCase().includes(search))));
            const sort = field('sort').value;
            return rows.sort((a, b) => sort === 'waiting' ? b.waiting_seconds - a.waiting_seconds
                : sort === 'oldest' ? date(a.first_seen_at) - date(b.first_seen_at) : date(b.last_seen_at) - date(a.last_seen_at));
        }

        function renderCandidates() {
            const allRows = status?.candidates || [];
            for (const eui of selected) if (!allRows.some(row => row.dev_eui === eui && row.status === 'pending')) selected.delete(eui);
            for (const [name, matches] of Object.entries(groups)) panel.querySelector(`[data-provisioning-total="${name}"]`).textContent = allRows.filter(matches).length;
            const rows = visibleRows(), pending = rows.filter(row => row.status === 'pending');
            const chosen = pending.filter(row => selected.has(row.dev_eui)).length;
            field('select-all').checked = pending.length > 0 && chosen === pending.length;
            field('select-all').indeterminate = chosen > 0 && chosen < pending.length;
            field('select-all').disabled = busy || !pending.length;
            field('selected').textContent = `${selected.size} selected (maximum 50)`;
            field('bulk-accept').disabled = busy || !status?.enabled || !selected.size || selected.size > 50;
            field('bulk-reject').disabled = busy || !selected.size || selected.size > 50;
            const next = JSON.stringify([rows, busy, [...selected], filter, status?.enabled]);
            if (next === signature) return;
            signature = next;
            field('empty').hidden = rows.length > 0;
            field('empty').textContent = allRows.length ? 'No devices match this view and search.' : 'No verified devices recorded. Open a scan to discover nodes.';
            field('queue').hidden = !rows.length;
            field('candidates').replaceChildren();
            for (const row of rows) {
                const tr = document.createElement('tr'), selectCell = add(tr, 'td', '');
                if (row.status === 'pending') {
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox'; checkbox.checked = selected.has(row.dev_eui); checkbox.disabled = busy;
                    checkbox.setAttribute('aria-label', `Select ${row.device_id}`);
                    checkbox.addEventListener('change', () => {
                        if (checkbox.checked && selected.size < 50) selected.add(row.dev_eui); else selected.delete(row.dev_eui);
                        renderCandidates();
                    });
                    selectCell.append(checkbox);
                }
                const identity = add(tr, 'td', '');
                add(identity, 'strong', row.device_id);
                add(identity, 'small', `DevEUI: ${row.dev_eui}`); add(identity, 'small', `JoinEUI: ${row.join_eui}`);
                if (row.local_device_id) add(identity, 'a', 'Open device').href = `/device-capabilities-manager?device_id=${encodeURIComponent(row.local_device_id)}`;
                const radio = add(tr, 'td', row.gateway_id || 'Gateway unavailable');
                add(radio, 'small', [row.rssi == null ? null : `RSSI ${row.rssi} dBm`, row.snr == null ? null : `SNR ${row.snr} dB`].filter(Boolean).join(' / ') || 'Signal unavailable');
                const seen = add(tr, 'td', formatDate(row.last_seen_at));
                add(seen, 'small', `${row.seen_count} observed join request(s)`); add(seen, 'small', `First seen: ${formatDate(row.first_seen_at)}`);
                const state = add(tr, 'td', '');
                add(state, 'span', names[row.status] || row.status, 'pill').dataset.enrollmentState = row.status;
                if (row.status === 'online') add(state, 'small', `Current connectivity: ${row.connection_state.replaceAll('_', ' ')}`);
                if (row.status === 'awaiting_join') {
                    add(state, 'small', `Waiting ${elapsed(row.waiting_seconds)} since approval`);
                    if (row.attention_required) add(state, 'small', 'No first uplink yet. Check power, antenna, frequency plan and credentials. Retry the join on the node.', 'provisioning-error');
                }
                if (row.registration_error) add(state, 'small', row.registration_error, 'provisioning-error');
                if (row.decided_by) add(state, 'small', `${row.decided_by} · ${formatDate(row.decided_at)}`);
                const check = row.ttn_check;
                if (check) {
                    add(state, 'small', `TTN checked: ${formatDate(row.ttn_checked_at)}`);
                    if (check.error) add(state, 'small', check.error, 'provisioning-error');
                    else {
                        add(state, 'small', Object.entries(check.components).map(([part, value]) => `${part.toUpperCase()}: ${value}`).join(' · '));
                        add(state, 'small', check.session_started_at ? `TTN session created: ${formatDate(check.session_started_at)}. Send an uplink; check webhook delivery if it does not appear.` : 'No active TTN session observed.' + (row.status === 'awaiting_join' ? ' The device must retry its join.' : ''));
                    }
                }
                const actions = add(tr, 'td', '');
                const button = (action, label, disabled = false) => {
                    const node = add(actions, 'button', label);
                    node.type = 'button'; node.disabled = busy || disabled; node.setAttribute('aria-label', `${label} ${row.device_id}`);
                    node.addEventListener('click', () => {
                        if (action === 'revoke' && !confirm(`Revoke approval for ${row.device_id}? This deletes its TTN registration and blocks admission until you explicitly reconsider and accept it again.`)) return;
                        request(action, row.dev_eui);
                    });
                };
                if (['pending', 'registration_failed'].includes(row.status)) {
                    button('accept', row.status === 'pending' ? 'Accept' : 'Retry registration', !status.enabled);
                    button('reject', row.status === 'pending' ? 'Reject' : 'Reject and clean up TTN');
                }
                if (['awaiting_join', 'revocation_failed'].includes(row.status)) button('revoke', row.status === 'revocation_failed' ? 'Retry revocation' : 'Revoke approval');
                if (row.status === 'rejected') button('reconsider', 'Return to review');
                if (!['registering', 'revoking', 'revocation_failed'].includes(row.status)) button('check', 'Check TTN');
                field('candidates').append(tr);
            }
        }

        function render() {
            const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000)), isOpen = Boolean(status?.open && remaining > 0);
            const available = Boolean(status?.enabled && !requestError);
            panel.setAttribute('aria-busy', String(busy));
            field('state').textContent = requestError ? 'Status unavailable' : !status ? 'Checking status' : !status.enabled ? 'Disabled' : isOpen ? 'Open' : 'Closed';
            field('state').dataset.state = isOpen ? 'open' : 'closed';
            field('remaining').textContent = isOpen ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}` : '—';
            field('count').textContent = status ? `${status.quota_used} / ${status.quota}` : '—';
            field('duration').disabled = busy || !available || isOpen; field('open').disabled = busy || !available || isOpen;
            field('close').disabled = busy || !isOpen; field('refresh').disabled = busy;
            field('message').dataset.tone = requestError ? 'error' : 'info';
            field('message').textContent = requestError || (!status ? 'Checking discovery availability…'
                : !status.enabled ? 'Discovery and acceptance are disabled. Saved decisions remain available.'
                    : isOpen && !status.listener_running ? 'The scan is open, but the gateway listener is not running. Check gateway traffic access.'
                        : isOpen ? 'Listening for verified join requests. Review new nodes below.' : 'Scanning is stopped. Saved requests and awaiting-uplink devices can still be managed below.');
            renderCandidates();
        }

        async function readStatus() {
            const response = await fetch('/admin/enrollment/status', {credentials: 'same-origin', cache: 'no-store'});
            if (response.redirected || !response.ok) throw new Error('Sign in as an administrator to manage device enrollment.');
            status = await response.json(); deadline = Date.now() + Number(status.seconds_remaining || 0) * 1000;
        }

        async function request(action = 'status', dev_eui = null) {
            if (busy) return;
            busy = true; render();
            try {
                if (action === 'status') await readStatus();
                else {
                    const options = {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: '{}'};
                    let path = dev_eui ? `candidates/${encodeURIComponent(dev_eui)}/${action}` : action;
                    if (action === 'open') options.body = JSON.stringify({seconds: Number(field('duration').value)});
                    if (action.startsWith('bulk-')) { path = 'bulk'; options.body = JSON.stringify({action: action.slice(5), dev_euis: [...selected]}); }
                    field('action-message').textContent = 'Applying action…';
                    const response = await fetch(`/admin/enrollment/${path}`, options);
                    if (response.redirected) throw new Error('Sign in again to continue.');
                    const data = await response.json();
                    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not update enrollment.');
                    status = data; deadline = Date.now() + Number(data.seconds_remaining || 0) * 1000;
                    const failures = data.results?.filter(item => !item.success) || [];
                    field('action-message').textContent = data.results
                        ? `${data.results.length - failures.length} completed; ${failures.length} unsuccessful. ${failures.map(item => `${item.dev_eui}: ${item.detail}`).join(' ')}` : 'Device enrollment updated.';
                    if (action === 'accept' && status.candidates.some(row => row.dev_eui === dev_eui && row.status === 'awaiting_join')) setFilter('waiting');
                    if (action.startsWith('bulk-')) selected.clear();
                }
                requestError = '';
            } catch (error) {
                const text = error instanceof TypeError || error instanceof SyntaxError ? 'The request result is unknown. Refresh status before retrying.' : error.message;
                field('action-message').textContent = text;
                if (action === 'status') requestError = text;
                else { try { await readStatus(); } catch { requestError = text; } }
            } finally { busy = false; render(); }
        }

        function setFilter(value) {
            filter = value;
            panel.querySelectorAll('[data-provisioning-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.provisioningFilter === filter)));
            renderCandidates();
        }
        panel.querySelectorAll('[data-provisioning-filter]').forEach(button => button.addEventListener('click', () => setFilter(button.dataset.provisioningFilter)));
        field('search').addEventListener('input', renderCandidates); field('sort').addEventListener('change', renderCandidates);
        field('select-all').addEventListener('change', event => {
            visibleRows().filter(row => row.status === 'pending').forEach(row => {
                if (event.target.checked && selected.size < 50) selected.add(row.dev_eui); else if (!event.target.checked) selected.delete(row.dev_eui);
            }); renderCandidates();
        });
        field('bulk-accept').addEventListener('click', () => request('bulk-accept')); field('bulk-reject').addEventListener('click', () => request('bulk-reject'));
        field('open').addEventListener('click', () => request('open')); field('close').addEventListener('click', () => request('close')); field('refresh').addEventListener('click', () => request());
        document.addEventListener('visibilitychange', () => { if (!document.hidden) request(); });
        setInterval(() => { if (!document.hidden) render(); }, 1000); setInterval(() => { if (!document.hidden) request(); }, 5000);
        request();
    });
});
