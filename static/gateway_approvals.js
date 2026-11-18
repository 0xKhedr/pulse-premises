(() => {
    'use strict';
    const el = id => document.getElementById(id);
    let inbox = null;
    let selected = null;
    let loading = false;
    let busy = false;
    const labels = {pending: 'Pending approval', registering: 'Registering', registration_failed: 'Registration failed', accepted: 'Accepted', rejected: 'Rejected'};
    const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const when = value => value ? new Date(value).toLocaleString() : '—';

    function message(text, error = false) {
        el('approvalMessage').textContent = text;
        el('approvalMessage').dataset.error = String(error);
    }

    async function api(path, data) {
        const response = await fetch(path, data === undefined ? {cache: 'no-store'} : {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data), cache: 'no-store'
        });
        if (response.redirected) throw new Error('Your session expired. Sign in again.');
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The request could not be completed.');
        return result;
    }

    function render() {
        const filter = el('approvalFilter').value;
        const reviewStates = ['pending', 'registering', 'registration_failed'];
        const pending = inbox.candidates.filter(item => reviewStates.includes(item.status));
        el('approvalCount').textContent = `(${pending.length} to review)`;
        const collector = inbox.collector;
        el('approvalCollector').textContent = collector.active
            ? '● Connection monitoring is active — new attempts appear automatically.'
            : collector.enabled
                ? 'Connection monitoring is unavailable. Check that the updated TTN stack is running and sharing its journal.'
                : 'Connection monitoring is not configured. The bundled Docker deployment enables it; external TTN servers require access to their gateway failure logs.';
        if (collector.error) el('approvalCollector').textContent += ` ${collector.error}`;
        el('approvalSetup').hidden = inbox.management_configured;
        el('approvalSetup').textContent = 'Acceptance needs a TTN gateway management key. Set TTN_GATEWAY_MANAGEMENT_API_KEY for the configured gateway owner and restart SBMS. Rejection is still available.';
        const setup = inbox.ttn_setup || {};
        el('ttnSetupStatus').textContent = !setup.enabled ? 'Automatic TTN application setup is disabled.'
            : setup.error ? `TTN setup: ${setup.error}`
            : setup.application_ready && setup.webhook_ready ? 'TTN application and uplink webhook are ready.'
            : setup.application_ready && !setup.webhook_enabled ? 'TTN application is ready. Configure the SBMS address reachable from TTN to enable automatic webhook setup.'
            : 'Preparing the TTN application and webhook automatically…';
        const items = inbox.candidates.filter(item => filter === 'all' || (filter === 'pending' ? reviewStates.includes(item.status) : item.status === filter));
        if (!items.length) {
            el('approvalRows').innerHTML = `<tr><td colspan="6">${collector.active ? 'No matching gateway connection requests.' : 'No matching requests recorded. Monitoring is currently unavailable.'}</td></tr>`;
            return;
        }
        el('approvalRows').innerHTML = items.map(item => {
            const canAccept = ['pending', 'rejected', 'registration_failed'].includes(item.status)
                || (item.status === 'registering' && Date.now() / 1000 - item.registration_started_at >= 120);
            const accept = canAccept && item.eui ? `<button type="button" data-action="accept" data-id="${item.id}" ${!inbox.management_configured || busy ? 'disabled' : ''}>${item.status === 'registration_failed' || item.status === 'registering' ? 'Retry' : 'Accept'}</button>` : '';
            const reject = item.status === 'pending' ? `<button type="button" data-action="reject" data-id="${item.id}" ${busy ? 'disabled' : ''}>Reject</button>` : '';
            const key = item.status === 'accepted' && item.require_authenticated_connection ? `<button type="button" data-action="key" data-id="${item.id}" ${!inbox.management_configured || busy ? 'disabled' : ''}>Create connection key</button>` : '';
            const decision = item.decided_by ? `<small>${escape(item.decided_by)}<br>${escape(when(item.decided_at))}</small>` : '';
            return `<tr>
                <td><code>${escape(item.eui || item.observed_gateway_id)}</code><small>${escape(item.gateway_id || item.observed_gateway_id || 'Not registered by SBMS')}</small><small>${escape(item.reason)}</small></td>
                <td>${item.protocol === 'udp' ? 'Semtech UDP' : 'Basics Station'}<small>${escape(item.source_ip || 'Source IP not exposed by TTN')}</small></td>
                <td>${Number(item.seen_count)}<small>First: ${escape(when(item.first_seen_at))}</small></td>
                <td>${escape(when(item.last_seen_at))}</td>
                <td><span class="approval-badge ${escape(item.status)}">${escape(labels[item.status])}</span>${decision}${item.registration_error ? `<small>${escape(item.registration_error)}</small>` : ''}</td>
                <td><div class="approval-actions">${accept}${reject}${key}</div>${!item.eui && canAccept ? '<small>No EUI available. Register manually in TTN.</small>' : ''}${item.status === 'registering' ? '<small>Waiting for TTN confirmation.</small>' : ''}</td>
            </tr>`;
        }).join('');
    }

    async function load() {
        if (loading) return;
        loading = true;
        try {
            inbox = await api('/gateways/approvals');
            render();
        } catch (err) {
            message(err.message, true);
            el('approvalCollector').textContent = 'Connection monitoring status could not be loaded.';
        } finally { loading = false; }
    }

    function review(item) {
        selected = item;
        el('approvalIdentity').textContent = `EUI: ${item.eui} · ${item.protocol === 'udp' ? 'Semtech UDP' : 'Basics Station'}`;
        el('approvalGatewayId').value = item.gateway_id || `gw-${item.eui.toLowerCase()}`;
        el('approvalName').value = item.name || item.gateway_id || `Gateway ${item.eui.slice(-6)}`;
        el('approvalPlan').value = item.frequency_plan_id || inbox.default_frequency_plan_id;
        el('approvalSite').innerHTML = '<option value="">Unassigned</option>' + inbox.sites.map(site => `<option value="${site.id}">${escape(site.name)}</option>`).join('');
        el('approvalSite').value = item.site_id || '';
        el('approvalAuth').checked = item.require_authenticated_connection === null ? item.protocol !== 'udp' : Boolean(item.require_authenticated_connection);
        const retry = Boolean(item.gateway_id);
        el('approvalGatewayId').readOnly = retry;
        el('approvalPlan').readOnly = retry;
        el('approvalAuth').disabled = retry || item.protocol !== 'udp';
        el('approvalProtocolNote').textContent = item.protocol === 'udp'
            ? 'UDP has no authentication. Requiring authentication means you must switch the gateway to Basics Station and configure a connection key before it can connect.'
            : 'After acceptance, create a connection key here and configure it on your gateway. TTN will continue to refuse connections without valid credentials.';
        el('approvalFormError').textContent = '';
        el('gatewayApprovalDialog').showModal();
    }

    el('gatewayApprovalForm').addEventListener('submit', async event => {
        event.preventDefault();
        if (!selected || busy) return;
        busy = true;
        el('submitGatewayApproval').disabled = true;
        el('cancelGatewayApproval').disabled = true;
        el('submitGatewayApproval').textContent = 'Registering…';
        try {
            await api(`/gateways/approvals/${selected.id}/accept`, {
                gateway_id: el('approvalGatewayId').value.trim(), name: el('approvalName').value.trim(),
                frequency_plan_id: el('approvalPlan').value.trim(),
                require_authenticated_connection: el('approvalAuth').checked,
                site_id: el('approvalSite').value ? Number(el('approvalSite').value) : null
            });
            el('gatewayApprovalDialog').close();
            message('Gateway accepted and registered. Configure its connection settings, then check its health below.');
            el('approvalFilter').value = 'accepted';
            if (typeof loadGateways === 'function') loadGateways();
        } catch (err) {
            el('approvalFormError').textContent = err.message;
        } finally {
            busy = false;
            el('submitGatewayApproval').disabled = false;
            el('cancelGatewayApproval').disabled = false;
            el('submitGatewayApproval').textContent = 'Accept and register';
            await load();
            // A failed request may have created the TTN identity. Lock retries
            // to the now-persisted registration settings without losing the error.
            const latest = inbox?.candidates.find(item => item.id === selected.id);
            if (latest?.gateway_id && el('gatewayApprovalDialog').open) {
                selected = latest;
                el('approvalGatewayId').readOnly = true;
                el('approvalPlan').readOnly = true;
                el('approvalAuth').disabled = true;
            }
        }
    });

    el('approvalRows').addEventListener('click', async event => {
        const button = event.target.closest('button[data-action]');
        if (!button || busy) return;
        const item = inbox.candidates.find(candidate => candidate.id === Number(button.dataset.id));
        if (!item) return;
        if (button.dataset.action === 'accept') { review(item); return; }
        busy = true;
        button.disabled = true;
        try {
            if (button.dataset.action === 'reject') {
                await api(`/gateways/approvals/${item.id}/reject`, {});
                message('Gateway rejected. Future attempts will update its record under Rejected.');
            } else if (button.dataset.action === 'key') {
                const result = await api(`/gateways/approvals/${item.id}/connection-key`, {});
                if (!result.key) throw new Error('TTN returned no connection key. Check its API keys in the TTN Console.');
                el('gatewayConnectionKey').value = result.key;
                el('gatewayKeyIdentity').textContent = result.gateway_id;
                el('gatewayKeyMessage').textContent = '';
                el('gatewayKeyDialog').showModal();
            }
        } catch (err) { message(err.message, true); }
        finally { busy = false; await load(); }
    });
    el('gatewayApprovalDialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    el('cancelGatewayApproval').addEventListener('click', () => el('gatewayApprovalDialog').close());
    el('closeGatewayKey').addEventListener('click', () => el('gatewayKeyDialog').close());
    el('gatewayKeyDialog').addEventListener('close', () => { el('gatewayConnectionKey').value = ''; });
    el('copyGatewayKey').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(el('gatewayConnectionKey').value);
            el('gatewayKeyMessage').textContent = 'Key copied.';
        } catch {
            el('gatewayConnectionKey').select();
            el('gatewayKeyMessage').textContent = 'Press Ctrl+C or use your browser’s Copy command.';
        }
    });
    el('approvalFilter').addEventListener('change', () => { if (inbox) render(); });
    el('refreshApprovals').addEventListener('click', load);
    load();
    setInterval(() => { if (!document.hidden) load(); }, 5000);
})();
