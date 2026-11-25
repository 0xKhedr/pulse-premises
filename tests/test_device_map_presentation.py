import base64
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.connection import get_db_connection, run_migrations
from app.main import app, apply_device_label_uplink, create_login_session


@pytest.fixture
def presentation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_FILE", str(tmp_path / "presentation.db"))
    monkeypatch.setattr("app.routers.devices.read_tb_latest_telemetry", lambda *args, **kwargs: {})
    run_migrations()
    conn = get_db_connection()
    conn.execute("INSERT INTO clients(id,name) VALUES (10,'Client')")
    conn.execute("INSERT INTO sites(id,client_id,name) VALUES (10,10,'Site')")
    conn.execute("INSERT INTO buildings(id,site_id,name) VALUES (10,10,'Building')")
    conn.execute("INSERT INTO floors(id,building_id,name) VALUES (10,10,'Floor')")
    conn.execute("INSERT INTO rooms(id,floor_id,room_name,polygon_points,x,y) VALUES (10,10,'Room','[]',100,100)")
    for device_id, room_id in [("floor-sensor", None), ("room-sensor", 10)]:
        conn.execute("""INSERT INTO devices(device_id,chip_mac,label,node_type,floor_id,room_id,
                        is_placed,last_seen) VALUES (?,?,'Firmware name','environment',10,?,1,?)""",
                     (device_id, device_id, room_id, datetime.now(timezone.utc).isoformat()))
    user_id = conn.execute("INSERT INTO users(email,name,role,enabled) VALUES ('map@local','Admin','admin',1)").lastrowid
    conn.commit()
    client = TestClient(app)
    client.cookies.set("sbms_session", create_login_session(conn, user_id))
    yield conn, client
    client.close()
    conn.close()


def test_rename_and_icon_persist_across_map_and_management_responses(presentation):
    conn, client = presentation
    for device_id in ("floor-sensor", "room-sensor"):
        response = client.patch(f"/devices/{device_id}/presentation", json={"label": "  Lobby   North ", "map_icon": "water"})
        assert response.status_code == 200, response.text
        assert response.json() == {"device_id": device_id, "label": "Lobby North", "map_icon": "water"}
    live = client.get("/floors/10/live").json()
    assert live["floor_devices"][0]["map_icon"] == "water"
    assert live["rooms"][0]["devices"][0]["map_icon"] == "water"
    assert live["floor_devices"][0]["label"] == "Lobby North"
    devices = client.get("/devices-status").json()
    assert all(device["map_icon"] == "water" for device in devices)
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='device_presentation_updated'").fetchone()[0] == 2
    conn.close()
    with get_db_connection() as reopened:
        assert reopened.execute("SELECT label,map_icon,label_locked FROM devices WHERE device_id='floor-sensor'").fetchone()[:] == ("Lobby North", "water", 1)


def test_website_name_survives_later_firmware_nickname_with_old_snapshot(presentation):
    conn, client = presentation
    snapshot = dict(conn.execute("SELECT * FROM devices WHERE device_id='floor-sensor'").fetchone())
    assert client.patch('/devices/floor-sensor/presentation', json={"label": "Operator name"}).status_code == 200
    result = apply_device_label_uplink(conn, snapshot, base64.b64encode(b"Firmware name").decode("ascii"))
    assert result["label"] == "Operator name"
    assert conn.execute("SELECT label FROM devices WHERE device_id='floor-sensor'").fetchone()[0] == "Operator name"


def test_icon_only_keeps_firmware_naming_and_automatic_restores_profile_icon(presentation):
    conn, client = presentation
    assert client.patch('/devices/floor-sensor/presentation', json={"map_icon": "solar"}).status_code == 200
    assert conn.execute("SELECT label_locked FROM devices WHERE device_id='floor-sensor'").fetchone()[0] == 0
    device = dict(conn.execute("SELECT * FROM devices WHERE device_id='floor-sensor'").fetchone())
    assert apply_device_label_uplink(conn, device, base64.b64encode(b"New firmware name").decode("ascii"))["label"] == "New firmware name"
    response = client.patch('/devices/floor-sensor/presentation', json={"map_icon": "auto"})
    assert response.json()["map_icon"] is None
    assert response.json()["label"] == "New firmware name"


@pytest.mark.parametrize("payload", [{}, {"label": ""}, {"label": None}, {"label": "<script>"},
                                     {"label": "x" * 49}, {"map_icon": "<svg>"}, {"map_icon": None},
                                     {"label": "Valid", "dev_eui": "1234"}])
def test_invalid_metadata_cannot_change_device(presentation, payload):
    conn, client = presentation
    assert client.patch('/devices/floor-sensor/presentation', json=payload).status_code == 422
    assert conn.execute("SELECT label,map_icon,label_locked FROM devices WHERE device_id='floor-sensor'").fetchone()[:] == ("Firmware name", None, 0)


def test_missing_device_and_non_admin_cannot_edit(presentation):
    conn, client = presentation
    assert client.patch('/devices/missing/presentation', json={"label": "Name"}).status_code == 404
    conn.execute("UPDATE users SET role='client' WHERE email='map@local'")
    conn.commit()
    assert client.patch('/devices/floor-sensor/presentation', json={"label": "Name"}).status_code == 403
    assert conn.execute("SELECT label FROM devices WHERE device_id='floor-sensor'").fetchone()[0] == "Firmware name"
