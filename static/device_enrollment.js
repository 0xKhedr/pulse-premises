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
        let status = null;
        let deadline = 0;
        let busy = false;
        let requestError = '';

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
                message.textContent = 'Checking provisioning availability…';
            } else if (!status.enabled) {
                message.textContent = 'Automatic provisioning is disabled in deployment settings.';
            } else if (isOpen && !status.listener_running) {
                message.dataset.tone = 'error';
                message.textContent = 'The window is open, but the gateway listener is not running. Check the gateway connection and traffic access.';
            } else if (isOpen && status.quota_used >= status.quota) {
                message.textContent = 'This window has reached its registration limit. Close it and open a new window to add more nodes.';
            } else if (isOpen) {
                const rejected = status.rejected?.length || 0;
                message.textContent = 'Listening for new nodes. Power on a node and save its nickname.'
                    + (rejected ? ` ${rejected} node(s) presented credentials that did not match.` : '');
            } else {
                message.textContent = 'Provisioning is closed. Existing devices continue sending readings.';
            }
        }

        async function request(action = 'status') {
            if (busy) return;
            busy = true;
            render();
            try {
                const options = { credentials: 'same-origin', cache: 'no-store' };
                if (action !== 'status') options.method = 'POST';
                if (action === 'open') {
                    options.headers = { 'Content-Type': 'application/json' };
                    options.body = JSON.stringify({ seconds: Number(duration.value) });
                }
                const response = await fetch(`/admin/enrollment/${action}`, options);
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
