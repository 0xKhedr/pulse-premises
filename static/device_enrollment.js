document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-provisioning-panel]').forEach(panel => {
        const field = name => panel.querySelector(`[data-provisioning-${name}]`);
        const stateLabel = field('state');
        const remainingLabel = field('remaining');
        const countLabel = field('count');
        const duration = field('duration');
        const openButton = field('open');
        const closeButton = field('close');
        const refreshButton = field('refresh');
        const message = field('message');
        const queue = field('queue');
        const candidates = field('candidates');
        const empty = field('empty');
        let status = null;
        let deadline = 0;
        let busy = false;
        let requestError = '';
        let activeAction = null;
        let queueSignature = '';

        function renderCandidates() {
            const rows = status?.candidates || [];
            const queueBusy = busy && activeAction?.action !== 'status';
            const signature = JSON.stringify([rows, queueBusy, status?.enabled, queueBusy ? activeAction : null]);
            if (signature === queueSignature) return;
            queueSignature = signature;
            empty.hidden = rows.length > 0;
            queue.hidden = rows.length === 0;
            candidates.replaceChildren();
            const names = {
                pending: 'Pending approval', registering: 'Registering',
                registration_failed: 'Registration failed', awaiting_join: 'Awaiting join',
                online: 'Online', rejected: 'Rejected'
            };
            const addText = (parent, tag, text, className = '') => {
                const element = document.createElement(tag);
                element.textContent = text;
                element.className = className;
                parent.append(element);
                return element;
            };
            const formatDate = value => {
                if (!value) return '—';
                const date = new Date(value.replace(' ', 'T') + 'Z');
                return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
            };
            for (const row of rows) {
                const tr = document.createElement('tr');
                const identity = addText(tr, 'td', '');
                addText(identity, 'strong', row.device_id);
                addText(identity, 'small', `DevEUI: ${row.dev_eui}`);
                addText(identity, 'small', `JoinEUI: ${row.join_eui}`);
                const radio = addText(tr, 'td', row.gateway_id || 'Gateway unavailable');
                addText(radio, 'small', [
                    row.rssi == null ? null : `RSSI ${row.rssi} dBm`,
                    row.snr == null ? null : `SNR ${row.snr} dB`
                ].filter(Boolean).join(' / ') || 'Signal unavailable');
                const seen = addText(tr, 'td', formatDate(row.last_seen_at));
                addText(seen, 'small', `${row.seen_count} observation(s)`);
                const state = activeAction?.dev_eui === row.dev_eui && activeAction.action === 'accept'
                    ? 'registering' : row.status;
                const stateCell = addText(tr, 'td', '');
                const badge = addText(stateCell, 'span', names[state] || state, 'pill');
                badge.dataset.enrollmentState = state;
                if (row.registration_error && state !== 'registering') {
                    addText(stateCell, 'small', row.registration_error, 'provisioning-error');
                }
                if (row.decided_by) addText(stateCell, 'small', `By ${row.decided_by}`);
                const actions = addText(tr, 'td', '');
                if (['pending', 'registration_failed'].includes(row.status)) {
                    for (const action of ['accept', 'reject']) {
                        const button = addText(actions, 'button', action === 'reject' ? 'Reject'
                            : row.status === 'registration_failed' ? 'Retry registration' : 'Accept');
                        button.type = 'button';
                        button.className = action === 'reject' ? 'secondary' : '';
                        button.disabled = queueBusy || (action === 'accept' && !status.enabled);
                        button.setAttribute('aria-label', `${button.textContent} ${row.device_id}`);
                        button.addEventListener('click', () => request(action, row.dev_eui));
                    }
                } else {
                    actions.textContent = row.status === 'rejected' ? 'Admission denied'
                        : row.status === 'awaiting_join' ? 'Waiting for device retry'
                        : row.status === 'registering' ? 'Please wait' : 'Approved';
                }
                candidates.append(tr);
            }
        }

        function render() {
            const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
            const isOpen = Boolean(status?.open && remaining > 0);
            const available = Boolean(status?.enabled && !requestError);
            panel.setAttribute('aria-busy', String(busy));
            stateLabel.textContent = requestError ? 'Status unavailable' : !status ? 'Checking status'
                : !status.enabled ? 'Disabled' : isOpen ? 'Open' : 'Closed';
            stateLabel.dataset.state = isOpen ? 'open' : 'closed';
            remainingLabel.textContent = !status ? '—' : isOpen
                ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}` : '—';
            countLabel.textContent = status ? `${status.quota_used} / ${status.quota}` : '—';
            duration.disabled = busy || !available || isOpen;
            openButton.disabled = busy || !available || isOpen;
            closeButton.disabled = busy || !status || !isOpen;
            refreshButton.disabled = busy;
            message.dataset.tone = 'info';
            if (requestError) {
                message.dataset.tone = 'error';
                message.textContent = requestError;
            } else if (!status) {
                message.textContent = 'Checking discovery availability…';
            } else if (!status.enabled) {
                message.textContent = 'Discovery and acceptance are disabled in deployment settings. Saved decisions remain available.';
            } else if (isOpen && !status.listener_running) {
                message.dataset.tone = 'error';
                message.textContent = 'The window is open, but the gateway listener is not running. Check the gateway connection and traffic access.';
            } else if (isOpen && status.quota_used >= status.quota) {
                message.textContent = 'This scan has reached its discovery limit. You can still review pending devices. Start a new scan to discover more.';
            } else if (isOpen) {
                const rejected = status.rejected?.length || 0;
                message.textContent = 'Listening for new nodes. Power on a node and save its nickname, then Accept or Reject it below.'
                    + (rejected ? ` ${rejected} node(s) presented credentials that did not match.` : '');
            } else {
                message.textContent = 'Scanning is stopped. You can still Accept or Reject saved pending devices.';
            }
            renderCandidates();
        }

        async function request(action = 'status', dev_eui = null) {
            if (busy) return;
            busy = true;
            activeAction = { action, dev_eui };
            render();
            try {
                const options = { credentials: 'same-origin', cache: 'no-store' };
                if (action !== 'status') options.method = 'POST';
                if (action === 'open') {
                    options.headers = { 'Content-Type': 'application/json' };
                    options.body = JSON.stringify({ seconds: Number(duration.value) });
                }
                const path = dev_eui ? `candidates/${encodeURIComponent(dev_eui)}/${action}` : action;
                const response = await fetch(`/admin/enrollment/${path}`, options);
                if (response.status === 401 || response.redirected) {
                    status = null;
                    throw new Error('Sign in as an administrator to manage provisioning.');
                }
                if (response.status === 403) {
                    status = null;
                    throw new Error('An administrator account is required to manage provisioning.');
                }
                const data = await response.json();
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail
                    : 'Could not update provisioning. Refresh status and try again.');
                status = data;
                deadline = Date.now() + Number(data.seconds_remaining || 0) * 1000;
                requestError = '';
            } catch (error) {
                requestError = error instanceof TypeError || error instanceof SyntaxError
                    ? 'Could not reach provisioning. Refresh status and try again.' : error.message;
            } finally {
                busy = false;
                activeAction = null;
                render();
            }
        }

        openButton.addEventListener('click', () => request('open'));
        closeButton.addEventListener('click', () => request('close'));
        refreshButton.addEventListener('click', () => request());
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) request();
        });
        setInterval(() => { if (!document.hidden) render(); }, 1000);
        setInterval(() => { if (!document.hidden) request(); }, 5000);
        request();
    });
});
