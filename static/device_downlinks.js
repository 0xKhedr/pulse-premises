document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-downlink-panel]').forEach(panel => {
        const field = name => panel.querySelector(`[data-downlink-${name}]`);
        const form = field('form');
        const target = field('device');
        const port = field('port');
        const encoding = field('encoding');
        const payload = field('payload');
        const confirmed = field('confirmed');
        const send = field('send');
        const refresh = field('refresh');
        const message = field('message');
        let sending = false;
        let loading = false;
        let preferredTarget = new URLSearchParams(window.location.search).get('device_id') || '';

        function bytes() {
            if (encoding.value === 'text') return new TextEncoder().encode(payload.value);
            const compact = payload.value.replace(/\s/g, '');
            if (encoding.value === 'hex') {
                if (!/^(?:[0-9a-fA-F]{2})+$/.test(compact)) throw new Error('Enter complete hex byte pairs, for example 48 65 6C 6C 6F.');
                return Uint8Array.from(compact.match(/../g), pair => parseInt(pair, 16));
            }
            try { return Uint8Array.from(atob(compact), value => value.charCodeAt(0)); }
            catch { throw new Error('Enter a valid Base64 payload.'); }
        }

        function preview() {
            let valid = false;
            try {
                const raw = bytes();
                field('size').textContent = `${raw.length} / 51 bytes`;
                field('preview').textContent = Array.from(raw, byte => byte.toString(16).padStart(2, '0').toUpperCase()).join(' ');
                valid = raw.length >= 1 && raw.length <= 51;
                if (raw.length > 51) field('size').textContent += ' — shorten the payload';
            } catch (error) {
                field('size').textContent = payload.value ? error.message : '0 / 51 bytes';
                field('preview').textContent = '';
            }
            const fPort = Number(port.value);
            send.disabled = sending || loading || !target.value || !valid || !Number.isInteger(fPort) || fPort < 1 || fPort > 223;
            refresh.disabled = sending || loading;
            target.disabled = sending || loading;
            for (const input of [port, encoding, payload, confirmed]) input.disabled = sending;
            panel.setAttribute('aria-busy', String(sending || loading));
        }

        async function jsonResponse(response) {
            if (response.redirected || response.status === 401) throw new Error('Sign in as an administrator to send downlinks.');
            if (response.status === 403) throw new Error('Administrator access is required to send downlinks.');
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The downlink request was not accepted.');
            return data;
        }

        async function loadTargets(preferred = target.value) {
            preferredTarget = preferred;
            if (sending || loading) return;
            loading = true;
            preview();
            try {
                const data = await jsonResponse(await fetch('/devices/downlink-targets', {credentials: 'same-origin', cache: 'no-store'}));
                target.replaceChildren(new Option(data.devices.length ? 'Choose a device' : 'No joined devices available', ''));
                for (const device of data.devices) {
                    target.add(new Option(`${device.label || device.device_id} (${device.device_id})`, device.device_id));
                }
                if (preferredTarget) target.value = preferredTarget;
            } catch (error) {
                message.dataset.tone = 'error';
                message.textContent = error.message;
            } finally { loading = false; preview(); }
        }

        form.addEventListener('submit', async event => {
            event.preventDefault();
            preview();
            if (send.disabled) return;
            sending = true;
            const selectedDevice = target.value;
            const data = {f_port: Number(port.value), payload: payload.value, encoding: encoding.value, confirmed: confirmed.checked};
            preview();
            message.dataset.tone = 'info';
            message.textContent = 'Queueing downlink in TTN...';
            try {
                const response = await fetch(`/devices/${encodeURIComponent(selectedDevice)}/downlinks`, {
                    method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                const result = await jsonResponse(response);
                message.textContent = `${result.byte_count} byte(s) queued for ${result.device_id} on port ${result.f_port}. Waiting for a Class A receive window after an uplink. Receipt is not yet confirmed.`;
            } catch (error) {
                message.dataset.tone = 'error';
                message.textContent = error instanceof TypeError || error instanceof SyntaxError
                    ? 'The request result is unknown. Check the TTN queue before sending again.' : error.message;
            } finally { sending = false; preview(); }
        });
        for (const input of [target, port, encoding, payload]) input.addEventListener('input', preview);
        encoding.addEventListener('change', preview);
        refresh.addEventListener('click', () => loadTargets());
        window.selectDeviceDownlink = deviceId => {
            if (sending) return;
            target.value = deviceId;
            if (!target.value) loadTargets(deviceId);
            preview();
            panel.scrollIntoView({behavior: 'smooth', block: 'center'});
        };
        loadTargets(preferredTarget);
    });
});
