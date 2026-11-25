(function () {
    'use strict';

    const icons = {
        sensor: ['Sensor', 'M12 8v8m-4-4h8M5 5h14v14H5zM9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3'],
        thermometer: ['Temperature', 'M9 14.8V5a3 3 0 0 1 6 0v9.8a5 5 0 1 1-6 0ZM12 8v10m0 0h.01'],
        humidity: ['Humidity', 'M12 3C9 7 5 10 5 14a7 7 0 0 0 14 0c0-4-4-7-7-11ZM9 17l6-6m-6 0h.01M15 17h.01'],
        energy: ['Energy', 'm13 2-9 12h7l-1 8 10-13h-7l1-7Z'],
        shield: ['Safety', 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6'],
        occupancy: ['Occupancy', 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m18 0v-2a4 4 0 0 0-3-3.9M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm8-8a4 4 0 0 1 0 8'],
        water: ['Water', 'M12 3C9 7 5 10 5 14a7 7 0 0 0 14 0c0-4-4-7-7-11ZM8 15c0 2 2 3 4 3'],
        meter: ['Meter', 'M4 18a9 9 0 1 1 16 0ZM12 14l4-6M7 10l-1-1m6-3V5m6 5 1-1'],
        light: ['Lighting', 'M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0l-1 2H9l-1-2Z'],
        door: ['Door', 'M4 21h16M6 21V3h12v18M14 12h.01'],
        solar: ['Solar', 'M3 13h18l-2 8H5l-2-8Zm5 0 1 8m7-8-1 8M4 17h16M12 2v2M5 5l2 2m12-2-2 2M9 10a3 3 0 0 1 6 0'],
    };
    const statusLabels = {online: 'Online', offline: 'Offline', alert: 'Alert', stale: 'Stale readings', never_connected: 'Never connected'};
    let tooltip, activeMarker;
    let nextId = 0;

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function truth(value) { return [true, 1, '1', 'true', 'True'].includes(value); }

    function timestamp(value) {
        if (!value) return null;
        let text = String(value).replace(' ', 'T');
        if (!/(Z|[+-]\d\d:\d\d)$/i.test(text)) text += 'Z';
        const parsed = Date.parse(text);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function state(device, options = {}) {
        const telemetry = device.telemetry || {};
        let connection = device.connection_state || device.status || 'never_connected';
        const seen = timestamp(device.last_seen_at || device.last_seen);
        if (seen !== null && device.offline_after_seconds) {
            const age = Math.max(0, (Date.now() - seen) / 1000);
            connection = age >= device.offline_after_seconds ? 'offline'
                : age >= device.stale_after_seconds ? 'stale' : 'online';
        }
        if (connection === 'offline') return 'offline';
        if (options.showAlerts !== false && (truth(device.alarm_active) || truth(telemetry.alarm_active)
            || Number(telemetry.profile_alarm_active_count) > 0 || connection === 'alarm'
            || ['low', 'critical'].includes(telemetry.battery_status))) return 'alert';
        return ['online', 'stale'].includes(connection) ? connection : 'never_connected';
    }

    function iconKey(device) {
        if (icons[device.map_icon]) return device.map_icon;
        const aliases = {environment: 'thermometer', temperature: 'thermometer', safety: 'shield',
            electricity: 'energy', motion: 'occupancy', default: 'sensor', multi: 'sensor'};
        const type = device.icon_type || device.node_type;
        return icons[type] ? type : aliases[type] || aliases[device.node_type] || 'sensor';
    }

    function icon(key) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        for (const [name, value] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
            'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true'})) svg.setAttribute(name, value);
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', (icons[key] || icons.sensor)[1]);
        svg.appendChild(path);
        return svg;
    }

    function valueText(value) {
        if (value === null || value === undefined || value === '') return '—';
        if (typeof value === 'boolean') return value ? 'Yes' : 'No';
        if (typeof value === 'number') return Number.isFinite(value) ? String(Math.round(value * 100) / 100) : '—';
        return typeof value === 'object' ? '—' : String(value);
    }

    function readings(device) {
        const telemetry = device.telemetry || {};
        const fields = device.display_telemetry?.length ? device.display_telemetry : device.telemetry_fields || [];
        const rows = [], keys = new Set();
        function add(key, label, value, unit) {
            if (!key || keys.has(key) || value === undefined || value === null || value === '') return;
            keys.add(key); rows.push([label || key, valueText(value) + (unit ? ' ' + unit : '')]);
        }
        fields.forEach(field => {
            const key = field.field_key || field.key || field.telemetry_key;
            add(key, field.label || field.field_label, field.value ?? telemetry[key], field.unit);
        });
        if (!rows.length) {
            const candidates = [
                [/^temperature(?:_\d+)?$/, 'Temperature', '°C'], [/^humidity(?:_\d+)?$/, 'Humidity', '%'],
                [/^co2(?:_\d+)?$/, 'CO₂', 'ppm'], [/^pressure(?:_\d+)?$/, 'Pressure', 'hPa'],
                [/^occupancy(?:_\d+)?$/, 'Occupancy', ''], [/^power(?:_\d+)?$/, 'Power', 'W'],
                [/^voltage(?:_\d+)?$/, 'Voltage', 'V'], [/^distance(?:_\d+)?$/, 'Distance', ''],
                [/^cam_reading$/, 'Meter reading', ''],
            ];
            for (const [pattern, label, unit] of candidates) {
                for (const [key, value] of Object.entries(telemetry)) if (pattern.test(key)) add(key, label, value, unit);
            }
            if (!rows.length) {
                for (const [key, value] of Object.entries(telemetry)) {
                    if (/^(alarm_|profile_|device_|last_|local_|telemetry_|configuration_|firmware_|payload_|gateway_|uplink_|received_|battery_|rssi$|snr$|timestamp$|power_source$)/.test(key)) continue;
                    if (typeof value === 'object') continue;
                    add(key, key.replace(/_/g, ' ').replace(/^./, first => first.toUpperCase()), value, '');
                }
            }
        }
        add('battery_percent', 'Battery', telemetry.battery_percent, '%');
        if (telemetry.battery_percent == null) add('battery_voltage', 'Battery', telemetry.battery_voltage, 'V');
        add('rssi', 'Signal', telemetry.rssi ?? telemetry.last_rssi, 'dBm');
        return rows.slice(0, 8);
    }

    function hideTooltip() {
        if (tooltip) tooltip.hidden = true;
        if (activeMarker) activeMarker.removeAttribute('aria-describedby');
        activeMarker = null;
    }

    function showTooltip(marker) {
        const {device, room, options} = marker._deviceMap;
        if (!tooltip) {
            tooltip = node('div', 'device-map-tooltip');
            tooltip.id = 'device-map-tooltip'; tooltip.setAttribute('role', 'tooltip');
            document.body.appendChild(tooltip);
        }
        activeMarker = marker; tooltip.replaceChildren();
        tooltip.appendChild(node('strong', 'device-map-tooltip-title', device.label || device.device_id));
        const status = state(device, options);
        tooltip.appendChild(node('span', 'device-map-tooltip-status', statusLabels[status]));
        tooltip.appendChild(node('div', 'device-map-tooltip-location', room?.room_name || device.room || 'Floor device'));
        if (options.showReadings !== false) {
            const grid = node('dl', 'device-map-readings');
            readings(device).forEach(([label, value]) => {
                grid.appendChild(node('dt', '', label)); grid.appendChild(node('dd', '', value));
            });
            tooltip.appendChild(grid);
            if (!grid.children.length) tooltip.appendChild(node('p', '', 'No readings received yet.'));
        }
        if (options.showAlerts !== false && (device.alarm_message || device.telemetry?.alarm_message)) {
            tooltip.appendChild(node('p', 'device-map-alert-message', device.alarm_message || device.telemetry.alarm_message));
        }
        const seen = timestamp(device.last_seen_at || device.last_seen);
        tooltip.appendChild(node('div', 'device-map-tooltip-meta', 'Last seen: ' + (seen === null ? 'Never' : new Date(seen).toLocaleString())));
        tooltip.appendChild(node('div', 'device-map-tooltip-meta', 'Device ID: ' + device.device_id));
        const profile = device.profile_name || device.profile?.profile_name || device.profile_code;
        if (profile) tooltip.appendChild(node('div', 'device-map-tooltip-meta', 'Profile: ' + profile));
        tooltip.hidden = false;
        const rect = marker.getBoundingClientRect(), bounds = tooltip.getBoundingClientRect();
        tooltip.style.left = Math.max(8, Math.min(rect.left + rect.width / 2 - bounds.width / 2, window.innerWidth - bounds.width - 8)) + 'px';
        const above = rect.top - bounds.height - 10;
        tooltip.style.top = Math.max(8, Math.min(above >= 8 ? above : rect.bottom + 10, window.innerHeight - bounds.height - 8)) + 'px';
        marker.setAttribute('aria-describedby', tooltip.id);
    }

    function renderMarker(marker, device, room, options = {}) {
        marker._deviceMap = {device, room, options};
        marker.classList.add('device-map-marker');
        marker.dataset.state = state(device, options);
        marker.dataset.deviceId = device.device_id;
        marker.setAttribute('role', 'button'); marker.tabIndex = 0;
        marker.setAttribute('aria-label', (device.label || device.device_id) + ': ' + statusLabels[marker.dataset.state]);
        marker.removeAttribute('title');
        marker.replaceChildren(icon(iconKey(device)), node('span', 'device-map-name', device.label || device.device_id));
        if (!marker._deviceMapBound) {
            marker._deviceMapBound = true;
            marker.addEventListener('mouseenter', () => showTooltip(marker));
            marker.addEventListener('mouseleave', hideTooltip);
            marker.addEventListener('focus', () => showTooltip(marker));
            marker.addEventListener('blur', hideTooltip);
            marker.addEventListener('mousedown', hideTooltip);
            marker.addEventListener('keydown', event => {
                if (event.key === 'Escape') hideTooltip();
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); hideTooltip(); marker.click(); }
            });
        }
        if (activeMarker === marker) showTooltip(marker);
    }

    function mountEditor(container, device, onSaved) {
        if (container._deviceEditor?.device.device_id === device.device_id) {
            Object.assign(container._deviceEditor, {device, onSaved}); return;
        }
        const editor = {device, onSaved, selected: device.map_icon || 'auto', busy: false};
        container._deviceEditor = editor; container.replaceChildren();
        const details = node('details', 'device-map-editor'), summary = node('summary', '', 'Name & map icon');
        details.appendChild(summary);
        const form = node('form'), id = 'device-map-name-' + (++nextId);
        const label = node('label', '', 'Device name'); label.htmlFor = id;
        const input = node('input'); input.id = id; input.value = device.label || device.device_id;
        input.maxLength = 48; input.required = true; input.autocomplete = 'off';
        form.appendChild(label); form.appendChild(input);
        form.appendChild(node('p', 'device-map-help', 'Up to 48 characters: letters, numbers, spaces, _ . - / #. Website names are kept when firmware announces its nickname.'));
        const palette = node('div', 'device-map-icon-picker'); palette.setAttribute('role', 'group'); palette.setAttribute('aria-label', 'Map icon');
        const choices = {auto: ['Automatic'], ...icons};
        Object.entries(choices).forEach(([key, [name]]) => {
            const choice = node('button', 'device-map-icon-choice'); choice.type = 'button';
            choice.setAttribute('aria-label', name); choice.title = name; choice.dataset.icon = key;
            choice.setAttribute('aria-pressed', String(editor.selected === key));
            choice.appendChild(icon(key === 'auto' ? iconKey({...device, map_icon: null}) : key));
            choice.appendChild(node('span', '', name));
            choice.addEventListener('click', () => {
                editor.selected = key;
                for (const item of palette.children) item.setAttribute('aria-pressed', String(item.dataset.icon === key));
            });
            palette.appendChild(choice);
        });
        form.appendChild(node('label', '', 'Map icon')); form.appendChild(palette);
        const save = node('button', 'device-map-save', 'Save name & icon'); save.type = 'submit'; form.appendChild(save);
        const message = node('p', 'device-map-save-message'); message.setAttribute('role', 'status'); form.appendChild(message);
        form.addEventListener('submit', async event => {
            event.preventDefault(); if (editor.busy) return;
            const name = input.value.trim().replace(/\s+/g, ' '), changes = {};
            if (name !== (editor.device.label || editor.device.device_id)) {
                if (!/^[A-Za-z0-9 _.\-/#]{1,48}$/.test(name)) {
                    message.textContent = 'Use 1–48 characters: letters, numbers, spaces, _ . - / #.'; return;
                }
                changes.label = name;
            }
            if (editor.selected !== (editor.device.map_icon || 'auto')) changes.map_icon = editor.selected;
            if (!Object.keys(changes).length) { message.textContent = 'No changes to save.'; return; }
            editor.busy = true;
            const controls = [input, save, ...palette.children]; controls.forEach(control => { control.disabled = true; });
            message.textContent = 'Saving…';
            try {
                const response = await fetch('/devices/' + encodeURIComponent(editor.device.device_id) + '/presentation', {
                    method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(changes),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save device settings.');
                Object.assign(editor.device, data); input.value = data.label || data.device_id;
                message.textContent = 'Saved. The name and icon apply to the website.';
                editor.onSaved?.(data);
            } catch (error) { message.textContent = error.message || 'Could not save device settings.'; }
            finally { editor.busy = false; controls.forEach(control => { control.disabled = false; }); }
        });
        details.appendChild(form); container.appendChild(details);
    }

    function legend(container) {
        const legend = node('div', 'device-map-legend'); legend.setAttribute('aria-label', 'Device map status legend');
        for (const status of ['online', 'offline', 'alert', 'stale']) {
            const item = node('span', '', statusLabels[status]); item.dataset.state = status; legend.appendChild(item);
        }
        container.appendChild(legend);
    }

    window.addEventListener('scroll', hideTooltip, true);
    window.addEventListener('resize', hideTooltip);
    // Connection age keeps advancing even if a client temporarily loses its
    // refresh request. This uses the server's configured offline threshold.
    window.setInterval(() => {
        document.querySelectorAll('.device-map-marker').forEach(marker => {
            const data = marker._deviceMap;
            if (!data) return;
            const status = state(data.device, data.options);
            marker.dataset.state = status;
            marker.setAttribute('aria-label', (data.device.label || data.device.device_id) + ': ' + statusLabels[status]);
        });
        if (activeMarker && !activeMarker.isConnected) hideTooltip();
    }, 1000);
    window.DeviceMap = {state, iconKey, readings, renderMarker, mountEditor, hideTooltip, legend};
}());
