from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import main
from app.services.device_profiles import mark_formatter_synced


@pytest.fixture
def camera_device(client, db_conn, monkeypatch):
    suffix = uuid4().hex[:12]
    password = main.create_password_hash("ProfileAssignmentPass123!")
    email = f"profiles-{suffix}@test.local"
    user = db_conn.execute(
        """INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
           VALUES (?, 'Profile Admin', 'admin', 1, ?, ?)""",
        (email, password["password_hash"], password["password_salt"]),
    )
    auth_user_ids = [user.lastrowid]
    client.cookies.set("sbms_session", main.create_login_session(db_conn, auth_user_ids[0]))
    old_profile = main.get_sensor_profile_detail(db_conn, "CAYENNE_LPP_V1")
    camera_profile = main.get_sensor_profile_detail(db_conn, "ESPCAM_METER_V1")
    device_id = f"camera-{suffix}"
    client_id = db_conn.execute("INSERT INTO clients (name) VALUES (?)", (suffix,)).lastrowid
    site_id = db_conn.execute("INSERT INTO sites (client_id, name) VALUES (?, ?)", (client_id, suffix)).lastrowid
    building_id = db_conn.execute("INSERT INTO buildings (site_id, name) VALUES (?, ?)", (site_id, suffix)).lastrowid
    floor_id = db_conn.execute("INSERT INTO floors (building_id, name, floor_number) VALUES (?, 'First Floor', '1')", (building_id,)).lastrowid
    room_id = db_conn.execute("INSERT INTO rooms (floor_id, room_name, polygon_points, x, y) VALUES (?, 'Meter Room', '[]', 0, 0)", (floor_id,)).lastrowid
    db_conn.execute(
        """INSERT INTO devices
               (device_id, chip_mac, dev_eui, join_eui, app_key, label, node_type,
                profile_id, profile_code, profile_version, payload_version, configuration_checksum,
                firmware_version, floor, room, x, y, is_placed,
                floor_id, room_id, building_id, site_id, client_id)
           VALUES (?, ?, ?, '0000000000000000', 'private-test-key', 'Meter Camera', 'multi',
                   ?, ?, ?, ?, ?, '2.3.4', 'First Floor', 'Meter Room', 123, 456, 1,
                   ?, ?, ?, ?, ?)""",
        (device_id, suffix, "ABCD" + suffix.upper(), old_profile["id"], old_profile["profile_code"],
         old_profile["profile_version"], old_profile["payload_version"], old_profile["schema_checksum"],
         floor_id, room_id, building_id, site_id, client_id),
    )
    db_conn.commit()
    original = dict(db_conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone())
    formatter = Mock(return_value={"status": "formatter_synced"})
    thingsboard = Mock(return_value={"status": "attributes_synced"})
    monkeypatch.setattr(main, "set_device_formatter_from_profile", formatter)
    monkeypatch.setattr(main, "sync_tb_attributes_from_profile", thingsboard)
    yield {
        "client": client, "id": device_id, "profile": camera_profile, "original": original,
        "formatter": formatter, "thingsboard": thingsboard, "actor": email,
        "auth_user_ids": auth_user_ids,
    }
    with db_conn:
        db_conn.execute("DELETE FROM device_configuration_history WHERE device_id = ?", (device_id,))
        db_conn.execute("DELETE FROM audit_log WHERE device_id = ?", (device_id,))
        db_conn.execute("DELETE FROM device_capabilities WHERE device_id = ?", (device_id,))
        db_conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        db_conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        db_conn.execute("DELETE FROM floors WHERE id = ?", (floor_id,))
        db_conn.execute("DELETE FROM buildings WHERE id = ?", (building_id,))
        db_conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        db_conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        for user_id in auth_user_ids:
            db_conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            db_conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def assign(camera_device):
    return camera_device["client"].put(
        f"/devices/{camera_device['id']}/profile",
        json={"profile_id": camera_device["profile"]["id"]},
    )


def stored(db_conn, device):
    return dict(db_conn.execute("SELECT * FROM devices WHERE device_id = ?", (device["id"],)).fetchone())


def test_switch_preserves_identity_location_and_firmware(camera_device, db_conn):
    response = assign(camera_device)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["formatter_synced"] is True
    assert payload["profile_code"] == "ESPCAM_METER_V1"
    assert "private-test-key" not in response.text
    updated = stored(db_conn, camera_device)
    for key in ("device_id", "chip_mac", "dev_eui", "join_eui", "app_key", "label",
                "room_id", "floor_id", "building_id", "site_id", "client_id",
                "x", "y", "is_placed", "firmware_version"):
        assert updated[key] == camera_device["original"][key]
    profile = camera_device["profile"]
    assert updated["profile_id"] == profile["id"]
    assert updated["profile_version"] == profile["profile_version"]
    assert updated["payload_version"] == profile["payload_version"]
    assert updated["configuration_checksum"] == profile["schema_checksum"]
    assert updated["configuration_status"] == "applied"
    assert main.get_device_capabilities_list(db_conn, camera_device["id"], "multi") == profile["capabilities"]
    called = camera_device["formatter"].call_args.kwargs
    assert called["device_id"] == camera_device["id"]
    assert called["profile"]["payload_encoder_key"] == "espcam_meter_v1"
    assert "cam_reading_text" in called["profile"]["ttn_formatter_code"]
    camera_device["thingsboard"].assert_called_once()
    history = db_conn.execute(
        "SELECT * FROM device_configuration_history WHERE device_id = ?", (camera_device["id"],)
    ).fetchone()
    assert history["profile_code"] == "ESPCAM_METER_V1"
    assert history["requested_by"] == camera_device["actor"]
    audit = db_conn.execute(
        "SELECT * FROM audit_log WHERE action = 'device_profile_changed' AND device_id = ?",
        (camera_device["id"],),
    ).fetchone()
    assert audit["actor"] == camera_device["actor"]

    # The assignment must pass the webhook's version/checksum checks immediately.
    context = main.load_device_profile_context_for_webhook(db_conn, camera_device["id"])
    result = main.validate_decoded_payload_against_profile(
        context["profile"], {"cam_reading": 123.45, "cam_reading_text": "123.45", "cam_locked": True}
    )
    assert result["valid"] is True
    assert result["telemetry"]["cam_reading"] == 123.45


def test_unplaced_device_without_chip_mac_can_switch(camera_device, db_conn):
    db_conn.execute(
        "UPDATE devices SET chip_mac = NULL, room_id = NULL, floor_id = NULL, is_placed = 0 WHERE device_id = ?",
        (camera_device["id"],),
    )
    db_conn.commit()
    assert assign(camera_device).status_code == 200
    updated = stored(db_conn, camera_device)
    assert updated["chip_mac"] is None
    assert updated["room_id"] is None and updated["is_placed"] == 0


def test_formatter_failure_is_visible_and_retryable(camera_device, db_conn, monkeypatch):
    camera_device["formatter"].side_effect = HTTPException(status_code=502, detail="private upstream response")
    response = assign(camera_device)
    assert response.status_code == 200
    assert response.json()["formatter_synced"] is False
    assert "private upstream response" not in response.text
    assert stored(db_conn, camera_device)["configuration_status"] == "formatter_pending"
    assert stored(db_conn, camera_device)["profile_code"] == "ESPCAM_METER_V1"

    # Retry uses the existing endpoint and clears the persisted pending state.
    from app.routers import devices

    monkeypatch.setattr(devices, "set_device_formatter_from_profile", Mock(return_value={"status": "formatter_synced"}))
    retry = camera_device["client"].post(f"/devices/{camera_device['id']}/sync-formatter")
    assert retry.status_code == 200, retry.text
    assert retry.json()["configuration_status"] == "applied"
    assert stored(db_conn, camera_device)["configuration_status"] == "applied"


def test_optional_thingsboard_failure_does_not_undo_assignment(camera_device, db_conn):
    camera_device["thingsboard"].side_effect = RuntimeError("ThingsBoard unavailable")
    response = assign(camera_device)
    assert response.status_code == 200
    assert response.json()["formatter_synced"] is True
    assert response.json()["thingsboard_synced"] is False
    assert stored(db_conn, camera_device)["configuration_status"] == "applied"


def test_missing_device_or_profile_does_not_change_assignment(camera_device, db_conn):
    client = camera_device["client"]
    assert client.put("/devices/does-not-exist/profile", json={"profile_id": camera_device["profile"]["id"]}).status_code == 404
    response = client.put(f"/devices/{camera_device['id']}/profile", json={"profile_id": 99999999})
    assert response.status_code == 404
    assert stored(db_conn, camera_device)["profile_id"] == camera_device["original"]["profile_id"]
    camera_device["formatter"].assert_not_called()


@pytest.mark.parametrize("payload", [{}, {"profile_id": 0}, {"profile_id": -1}, {"profile_id": True},
                                     {"profile_id": "1"}, {"profile_id": 1, "app_key": "overwrite"}])
def test_invalid_request_is_rejected(camera_device, payload):
    response = camera_device["client"].put(f"/devices/{camera_device['id']}/profile", json=payload)
    assert response.status_code == 422
    camera_device["formatter"].assert_not_called()


@pytest.mark.parametrize("change", [{"enabled": False}, {"status": "draft"}, {"fields": []}])
def test_inactive_or_empty_profiles_are_rejected(camera_device, db_conn, monkeypatch, change):
    profile = {**camera_device["profile"], **change}
    monkeypatch.setattr(main, "get_sensor_profile_detail", lambda *args, **kwargs: profile)
    assert assign(camera_device).status_code == 409
    assert stored(db_conn, camera_device)["profile_id"] == camera_device["original"]["profile_id"]
    camera_device["formatter"].assert_not_called()


def test_assignment_is_admin_only(camera_device, db_conn):
    client = camera_device["client"]
    client.cookies.clear()
    response = client.put(f"/devices/{camera_device['id']}/profile", json={"profile_id": camera_device["profile"]["id"]}, follow_redirects=False)
    assert response.status_code in (302, 401, 403)
    user = db_conn.execute("INSERT INTO users (email, name, role, enabled) VALUES (?, 'Client', 'client', 1)",
                           (f"client-{uuid4().hex}@test.local",))
    camera_device["auth_user_ids"].append(user.lastrowid)
    client.cookies.set("sbms_session", main.create_login_session(db_conn, user.lastrowid))
    assert assign(camera_device).status_code == 403
    camera_device["formatter"].assert_not_called()


def test_late_formatter_response_does_not_mark_another_profile_applied(camera_device, db_conn):
    db_conn.execute("UPDATE devices SET configuration_status = 'formatter_pending' WHERE device_id = ?", (camera_device["id"],))
    db_conn.commit()
    assert mark_formatter_synced(camera_device["id"], camera_device["profile"]) is False
    assert stored(db_conn, camera_device)["configuration_status"] == "formatter_pending"


def test_history_failure_rolls_back_the_assignment(camera_device, db_conn, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("history write failed")

    monkeypatch.setattr(main, "record_device_configuration_history", fail)
    with pytest.raises(RuntimeError, match="history write failed"):
        assign(camera_device)
    assert stored(db_conn, camera_device)["profile_id"] == camera_device["original"]["profile_id"]
    camera_device["formatter"].assert_not_called()
