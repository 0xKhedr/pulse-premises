from __future__ import annotations

from app.config import settings
from app.main import create_login_session, create_password_hash


def test_http_discovery_to_operational_floor_workflow(client, db_conn, monkeypatch):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "workflow-secret")
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)

    password = create_password_hash("WorkflowAdminPass123!")
    admin_id = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('workflow_admin@test.local', 'Workflow Admin', 'admin', 1, ?, ?)
        """,
        (password["password_hash"], password["password_salt"]),
    ).lastrowid
    db_conn.commit()
    client.cookies.set("sbms_session", create_login_session(db_conn, admin_id))

    device_id = "browser-workflow-device"
    envelope = {
        "end_device_ids": {
            "device_id": device_id,
            "dev_eui": "70B3D57ED0000200",
            "application_ids": {"application_id": "smart-building-lora-2"},
        },
        "uplink_message": {
            "decoded_payload": {"temperature_1": 55.0},
            "rx_metadata": [
                {
                    "gateway_ids": {"gateway_id": "workflow-gateway"},
                    "rssi": -88,
                    "snr": 7.0,
                }
            ],
        },
    }

    webhook = client.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "workflow-secret"},
        json=envelope,
    )
    assert webhook.status_code == 200, webhook.text

    inbox = client.get("/devices/unplaced")
    assert inbox.status_code == 200, inbox.text
    discovered = next(row for row in inbox.json() if row["device_id"] == device_id)
    assert discovered["monitoring_state"] == "discovery"
    assert discovered["telemetry"]["rssi"] == -88
    assert discovered["telemetry"]["snr"] == 7.0
    assert discovered["telemetry"]["gateway_id"] == "workflow-gateway"

    client_id = client.post("/clients", json={"name": "Workflow Client"}).json()[
        "client_id"
    ]
    site_id = client.post(
        "/sites",
        json={"client_id": client_id, "name": "Workflow Site"},
    ).json()["site_id"]
    building_id = client.post(
        "/buildings",
        json={"site_id": site_id, "name": "Workflow Building"},
    ).json()["building_id"]
    floor_id = client.post(
        "/floors",
        json={"building_id": building_id, "name": "Workflow Floor"},
    ).json()["floor_id"]

    placed = client.put(
        f"/devices/{device_id}/position",
        json={"floor_id": floor_id, "x": 120, "y": 240},
    )
    assert placed.status_code == 200, placed.text
    assert placed.json()["floor_id"] == floor_id

    operational_uplink = client.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "workflow-secret"},
        json=envelope,
    )
    assert operational_uplink.status_code == 200, operational_uplink.text

    assert all(
        row["device_id"] != device_id for row in client.get("/devices/unplaced").json()
    )
    floor = client.get(f"/floors/{floor_id}/live")
    assert floor.status_code == 200, floor.text
    placed_device = next(
        row for row in floor.json()["floor_devices"] if row["device_id"] == device_id
    )
    assert placed_device["monitoring_state"] == "operational"
    assert placed_device["x"] == 120
    assert placed_device["y"] == 240
    assert db_conn.execute(
        "SELECT COUNT(*) FROM alarm_history WHERE device_id = ? AND resolved = 0",
        (device_id,),
    ).fetchone()[0] >= 1
