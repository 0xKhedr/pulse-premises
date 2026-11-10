document.addEventListener('DOMContentLoaded', () => {
    const panel = document.querySelector('[data-device-configuration]');
    if (!panel) return;
    const find = selector => panel.querySelector(selector);
    const form = find('[data-configuration-form]');
    const interval = find('[data-send-interval]');
    const send = find('[data-configuration-send]');
    const refresh = find('[data-configuration-refresh]');
    const message = find('[data-configuration-message]');
    const lastQueued = find('[data-last-queued]');
    const endpoint = `/devices/${encodeURIComponent(panel.dataset.deviceId)}/configuration`;
    let busy = false;
    let canQueue = false;

    function updateControls() {
        const seconds = Number(interval.value);
        interval.disabled = busy || !canQueue;
        send.disabled = busy || !canQueue || !Number.isInteger(seconds) || seconds < 30 || seconds > 86400;
        refresh.disabled = busy;
        panel.setAttribute('aria-busy', String(busy));
    }

    async function readResponse(response) {
        if (response.redirected || response.status === 401) throw new Error('Sign in as an administrator to configure devices.');
        if (response.status === 403) throw new Error('Administrator access is required to configure devices.');
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The configuration request was not accepted.');
        return data;
    }

    function showQueued(seconds, queuedAt) {
        lastQueued.textContent = `Last queued: ${seconds} seconds${queuedAt ? ` (${queuedAt} UTC)` : ''}. Receipt and application are not confirmed.`;
    }

    async function load() {
        if (busy) return;
        busy = true;
        canQueue = false;
        updateControls();
        message.dataset.tone = 'info';
        message.textContent = 'Loading configuration…';
        try {
            const data = await readResponse(await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store'}));
            canQueue = data.can_queue;
            const previous = data.send_rate.last_queued;
            interval.value = previous ? previous.send_interval_seconds : data.send_rate.default_seconds;
            if (previous) showQueued(previous.send_interval_seconds, previous.queued_at);
            else lastQueued.textContent = 'No send-rate change has been queued from this page. The device’s active value is unknown.';
            message.textContent = data.unavailable_reason || 'Ready to queue a send-rate change.';
            if (!canQueue) message.dataset.tone = 'error';
        } catch (error) {
            message.dataset.tone = 'error';
            message.textContent = error.message;
        } finally { busy = false; updateControls(); }
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        updateControls();
        if (send.disabled) return;
        const seconds = Number(interval.value);
        busy = true;
        updateControls();
        message.dataset.tone = 'info';
        message.textContent = 'Queueing send-rate change in TTN…';
        try {
            const data = await readResponse(await fetch(`${endpoint}/send-rate`, {
                method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({send_interval_seconds: seconds})
            }));
            showQueued(data.send_interval_seconds);
            message.textContent = `${data.send_interval_seconds}-second interval queued. Waiting for a receive window after an uplink at the existing rate. Receipt and application are not confirmed.`;
        } catch (error) {
            message.dataset.tone = 'error';
            message.textContent = error instanceof TypeError || error instanceof SyntaxError
                ? 'The request result is unknown. Check the TTN queue before sending again.' : error.message;
        } finally { busy = false; updateControls(); }
    });
    interval.addEventListener('input', updateControls);
    refresh.addEventListener('click', load);
    load();
});
