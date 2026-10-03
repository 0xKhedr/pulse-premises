// Shared live-floor, device, gateway, activity, and export behaviour for the client portal pages.

const params = new URLSearchParams(window.location.search);
const userId = params.get("user_id") || "1";

let structureData = null;
let selectedFloorId = null;
let currentLiveData = null;

function escapeHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showError(message) {
    document.getElementById("viewerArea").innerHTML = `
        <div class="empty">${escapeHtml(message)}</div>
    `;
}

async function loadStructure() {
    try {
        const res = await fetch(`/client-portal/${userId}/allowed-structure`);

        if (!res.ok) {
            showError("Could not load client access. Check user_id or permissions.");
            return;
        }

        structureData = await res.json();

        document.getElementById("clientSubtitle").innerText =
            `${structureData.user.name} - ${structureData.user.email}`;

        drawTree(structureData.structure);

    } catch (error) {
        showError("Could not load client portal data.");
    }
}

function drawTree(structure) {
    const tree = document.getElementById("tree");
    tree.innerHTML = "";

    if (!structure || structure.length === 0) {
        tree.innerHTML = `<div class="empty">No allowed locations for this user.</div>`;
        return;
    }

    structure.forEach(client => {
        const clientDiv = document.createElement("div");
        clientDiv.className = "tree-client";

        clientDiv.innerHTML = `<div class="tree-title">Client: ${escapeHtml(client.name)}</div>`;

        client.sites.forEach(site => {
            const siteDiv = document.createElement("div");
            siteDiv.className = "tree-site";
            siteDiv.innerHTML = `Site: ${escapeHtml(site.name)}`;

            site.buildings.forEach(building => {
                const buildingDiv = document.createElement("div");
                buildingDiv.className = "tree-building";
                buildingDiv.innerHTML = `Building: ${escapeHtml(building.name)}`;

                building.floors.forEach(floor => {
                    const btn = document.createElement("button");
                    btn.className = "floor-btn";
                    btn.id = "floorBtn_" + floor.id;

                    let floorLabel = floor.name || ("Floor " + floor.floor_number);

                    if (
                        floor.floor_number &&
                        floor.name &&
                        floor.name.toLowerCase() === ("floor " + floor.floor_number).toLowerCase()
                    ) {
                        floorLabel = floor.name;
                    }

                    btn.innerText = floorLabel;
                    btn.onclick = () => loadFloor(floor.id);
                    buildingDiv.appendChild(btn);
                });

                siteDiv.appendChild(buildingDiv);
            });

            clientDiv.appendChild(siteDiv);
        });

        tree.appendChild(clientDiv);
    });
}

function drawPermissions(permissions) {
    const box = document.getElementById("permissions");

    box.innerHTML = `
        <span class="perm on">Level: ${escapeHtml(permissions.access_level)}</span>
        <span class="perm ${permissions.can_view_devices ? "on" : "off"}">Devices</span>
        <span class="perm ${permissions.can_view_gateways ? "on" : "off"}">Gateways</span>
        <span class="perm ${permissions.can_view_alarms ? "on" : "off"}">Alarms</span>
        <span class="perm ${permissions.can_view_telemetry ? "on" : "off"}">Telemetry</span>
        <span class="perm ${permissions.can_manage_email_settings ? "on" : "off"}">Email Settings</span>
    `;
}

async function loadFloor(floorId) {
    selectedFloorId = floorId;

    document.querySelectorAll(".floor-btn").forEach(btn => {
        btn.classList.remove("active");
    });

    const activeBtn = document.getElementById("floorBtn_" + floorId);

    if (activeBtn) {
        activeBtn.classList.add("active");
    }

    const res = await fetch(`/client-portal/${userId}/floors/${floorId}/live`);

    if (!res.ok) {
        showError("You do not have permission to open this floor.");
        return;
    }

    currentLiveData = await res.json();

    document.getElementById("floorTitle").innerText =
        currentLiveData.floor.name || ("Floor " + floorId);

    drawPermissions(currentLiveData.permissions);
    drawFloor();
}

function drawFloor() {
    const floor = currentLiveData.floor;

    if (!floor.image_path) {
        showError("This floor does not have an uploaded image.");
        return;
    }

    document.getElementById("viewerArea").innerHTML = `
        <div class="viewer" id="viewer">
            <img id="floorImage" src="${escapeHtml(floor.image_path)}">
        </div>
    `;

    const img = document.getElementById("floorImage");

    img.onload = function() {
        drawDevices();
        drawGateways();
    };

    img.onerror = function() {
        showError("Could not load floor image: " + floor.image_path);
    };
}

function drawDevices() {
    if (!currentLiveData.permissions.can_view_devices) {
        return;
    }

    const viewer = document.getElementById("viewer");
    const img = document.getElementById("floorImage");
    const floor = currentLiveData.floor;

    const deviceEntries = (currentLiveData.floor_devices || [])
        .map(device => ({device, room: null}));

    currentLiveData.rooms.forEach(room => {
        if (!room.devices) {
            return;
        }

        room.devices.forEach(device => deviceEntries.push({device, room}));
    });

    deviceEntries.forEach(({device, room}) => {
            if (device.x === null || device.y === null) {
                return;
            }

            const marker = document.createElement("div");
            marker.className = "node-marker";
            marker.innerHTML = "●";
            marker.title = device.label || device.device_id;

            marker.style.left = (device.x * img.clientWidth / floor.image_width) + "px";
            marker.style.top = (device.y * img.clientHeight / floor.image_height) + "px";

            marker.onclick = async function(event) {
                event.stopPropagation();

                showDetails(`
                    <h3>Device Details</h3>
                    <p><strong>Label:</strong> ${escapeHtml(device.label || "--")}</p>
                    <p><strong>Device ID:</strong> ${escapeHtml(device.device_id)}</p>
                    <p><strong>Type:</strong> ${escapeHtml(device.node_type)}</p>
                    <p><strong>Room:</strong> ${escapeHtml(device.room || room?.room_name || "Floor only")}</p>
                    <p><strong>Position:</strong> X ${escapeHtml(device.x)} / Y ${escapeHtml(device.y)}</p>
                    <hr>
                    <p class="muted">Loading telemetry...</p>
                `);

                await loadDeviceTelemetry(device, room);
            };

            viewer.appendChild(marker);
    });
}

async function loadDeviceTelemetry(device, room) {
    const box = document.getElementById("detailsBox");

    let baseHtml = `
        <h3>Device Details</h3>
        <p><strong>Label:</strong> ${escapeHtml(device.label || "--")}</p>
        <p><strong>Device ID:</strong> ${escapeHtml(device.device_id)}</p>
        <p><strong>Type:</strong> ${escapeHtml(device.node_type)}</p>
        <p><strong>Room:</strong> ${escapeHtml(device.room || room?.room_name || "Floor only")}</p>
        <p><strong>Position:</strong> X ${escapeHtml(device.x)} / Y ${escapeHtml(device.y)}</p>
    `;

    if (!currentLiveData.permissions.can_view_telemetry) {
        box.innerHTML = baseHtml + `
            <hr>
            <h3>Live Telemetry</h3>
            <p class="muted">Telemetry permission is disabled for this user.</p>
        `;
        return;
    }

    try {
        const res = await fetch(`/client-portal/${userId}/devices/${device.device_id}/latest-telemetry`);

        if (!res.ok) {
            box.innerHTML = baseHtml + `
                <hr>
                <h3>Live Telemetry</h3>
                <p class="muted">Could not load telemetry.</p>
            `;
            return;
        }

        const data = await res.json();
        const telemetry = data.telemetry || {};

        let telemetryHtml = `
            <hr>
            <h3>Live Telemetry</h3>
        `;

        if (Object.keys(telemetry).length === 0) {
            telemetryHtml += `<p class="muted">No telemetry received yet.</p>`;
        } else {
            Object.keys(telemetry).forEach(key => {
                const value = telemetry[key];

                if (
                    key === "alarm_active" ||
                    key === "alarm_message" ||
                    key === "received_by_gateways" ||
                    key === "gateway_id" ||
                    key === "rssi" ||
                    key === "snr" ||
                    key === "spreading_factor" ||
                    key === "bandwidth" ||
                    key === "frequency"
                ) {
                    return;
                }

                if (key === "temperature") {
                    telemetryHtml += `<p><strong>Temperature:</strong> ${escapeHtml(value)} °C</p>`;
                }

                else if (key === "humidity") {
                    telemetryHtml += `<p><strong>Humidity:</strong> ${escapeHtml(value)} %</p>`;
                }

                else if (key === "battery_percent") {
                    telemetryHtml += `<p><strong>Battery:</strong> 🔋 ${escapeHtml(value)}%</p>`;
                }

                else if (key === "battery_voltage") {
                    telemetryHtml += `<p><strong>Battery Voltage:</strong> ${escapeHtml(value)} V</p>`;
                }

                else if (key === "battery_status") {
                    let color = "#22c55e";

                    if (value === "low") color = "#f97316";
                    if (value === "critical") color = "#ef4444";

                    telemetryHtml += `
                        <p>
                            <strong>Battery Status:</strong>
                            <span style="color:${color};font-weight:bold;">
                                ${escapeHtml(String(value).toUpperCase())}
                            </span>
                        </p>
                    `;
                }

                else if (key === "signal_status") {
                    let color = "#22c55e";

                    if (value === "weak") color = "#f97316";
                    if (value === "poor") color = "#ef4444";

                    telemetryHtml += `
                        <p>
                            <strong>Connection Quality:</strong>
                            <span style="color:${color};font-weight:bold;">
                                ${escapeHtml(String(value).toUpperCase())}
                            </span>
                        </p>
                    `;
                }

                else if (key === "local_updated_at") {
                    return;
                }

                else {
                    telemetryHtml += `<p><strong>${escapeHtml(key)}:</strong> ${escapeHtml(value)}</p>`;
                }
            });
        }

        const alarmClass = data.alarm_active ? "color:#ef4444;font-weight:bold;" : "color:#22c55e;font-weight:bold;";

        telemetryHtml += `
            <p><strong>Alarm:</strong> <span style="${alarmClass}">${escapeHtml(data.alarm_message || "OK")}</span></p>
            <p><strong>Last Update:</strong> ${escapeHtml(data.updated_at || telemetry.local_updated_at || "--")}</p>
        `;

        box.innerHTML = baseHtml + telemetryHtml;

    } catch (e) {
        box.innerHTML = baseHtml + `
            <hr>
            <h3>Live Telemetry</h3>
            <p class="muted">Telemetry loading error.</p>
        `;
    }
}

function drawGateways() {
    if (!currentLiveData.permissions.can_view_gateways) {
        return;
    }

    const viewer = document.getElementById("viewer");
    const img = document.getElementById("floorImage");
    const floor = currentLiveData.floor;

    currentLiveData.gateways.forEach(gw => {
        if (gw.x === null || gw.y === null) {
            return;
        }

        const marker = document.createElement("div");
        const status = gw.status || "unknown";

        marker.className = "gateway-marker " + status;
        marker.innerHTML = "📡";
        marker.title = gw.label || gw.name || gw.gateway_id;

        marker.style.left = (gw.x * img.clientWidth / floor.image_width) + "px";
        marker.style.top = (gw.y * img.clientHeight / floor.image_height) + "px";

        marker.onclick = function(event) {
            event.stopPropagation();

            showDetails(`
                <h3>Gateway Details</h3>
                <p><strong>Name:</strong> ${escapeHtml(gw.name || "--")}</p>
                <p><strong>Label:</strong> ${escapeHtml(gw.label || "--")}</p>
                <p><strong>Gateway ID:</strong> ${escapeHtml(gw.gateway_id)}</p>
                <p><strong>Status:</strong> ${escapeHtml(status.toUpperCase())}</p>
                <p><strong>Last Seen:</strong> ${escapeHtml(gw.last_seen || "--")}</p>
                <p><strong>Position:</strong> X ${escapeHtml(gw.x || "--")} / Y ${escapeHtml(gw.y || "--")}</p>
                <p><strong>Note:</strong> ${escapeHtml(gw.location_note || "--")}</p>
            `);
        };

        viewer.appendChild(marker);
    });
}

function showDetails(html) {
    const box = document.getElementById("detailsBox");
    box.style.display = "block";
    box.innerHTML = html;
}

async function loadClientActivityLog() {
    const box = document.getElementById("clientActivityLog");

    if (!box) {
        console.log("clientActivityLog element not found");
        return;
    }

    box.innerHTML = "Loading activity...";

    try {
        const response = await fetch(`/client-portal/${userId}/activity-log?limit=50`);

        if (!response.ok) {
            throw new Error("Failed to load activity log");
        }

        const logs = await response.json();

        if (!logs || logs.length === 0) {
            box.innerHTML = `
                <div class="activity-empty">
                    No recent activity found.
                </div>
            `;
            return;
        }

        box.innerHTML = logs.map(function(log) {
            return `
                <div class="activity-item">
                    <div class="activity-message">
                        ${escapeHtml(log.message || "")}
                    </div>

                    <div class="activity-meta">
                        ${escapeHtml(log.timestamp || log.created_at || "")}
                        · <span class="activity-badge">${escapeHtml(log.action || "")}</span>
                    </div>
                </div>
            `;
        }).join("");

    } catch (error) {
        console.error("Activity log error:", error);

        box.innerHTML = `
            <div class="activity-error">
                Error loading activity log: ${escapeHtml(error.message)}
            </div>
        `;
    }
}

function exportClientAlarmHistoryCsv() {
    const query = new URLSearchParams();

    query.set("limit", "5000");

    if (selectedFloorId) {
        query.set("floor_id", selectedFloorId);
    }

    window.location.href = `/client-portal/${userId}/alarms/export.csv?` + query.toString();
}

function exportClientFullStructureCsv() {
    window.location.href = `/client-portal/${userId}/export/full-structure.csv`;
}

loadStructure();
loadClientActivityLog();

