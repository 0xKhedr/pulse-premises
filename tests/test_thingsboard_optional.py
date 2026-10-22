from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app import main
from app.config import settings
from app.services.thingsboard import ThingsBoardClient


def test_enabled_client_still_authenticates_and_fetches_devices(monkeypatch):
    monkeypatch.setattr(settings, "THINGSBOARD_ENABLED", True)
    login = Mock(return_value=Mock(json=lambda: {"token": "a-test-token"}))
    request = Mock(return_value=Mock(json=lambda: {"id": {"id": "test-device-id"}}))
    monkeypatch.setattr("requests.post", login)
    monkeypatch.setattr("requests.request", request)
    result = ThingsBoardClient().get_device_by_name("test-device")
    assert result["id"]["id"] == "test-device-id"
    login.assert_called_once()
    assert request.call_args.kwargs["headers"]["X-Authorization"] == "Bearer a-test-token"


def test_disabled_client_makes_no_network_requests(monkeypatch):
    monkeypatch.setattr(settings, "THINGSBOARD_ENABLED", False)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Disabled ThingsBoard attempted a network request")

    monkeypatch.setattr("requests.post", unexpected_request)
    monkeypatch.setattr("requests.request", unexpected_request)
    client = ThingsBoardClient()
    assert client.get_device_by_name("a-device") is None
    with pytest.raises(RuntimeError, match="disabled"):
        client.login()
    with pytest.raises(RuntimeError, match="disabled"):
        client.request("GET", "/api/device")


def test_disabled_integration_keeps_telemetry_history_and_alarm_email(db_conn, monkeypatch):
    monkeypatch.setattr(settings, "THINGSBOARD_ENABLED", False)
    monkeypatch.setattr(main, "LOCAL_TEST_MODE", False)
    emails = []
    monkeypatch.setattr(main, "send_alarm_email", lambda **kwargs: emails.append(kwargs))

    def unexpected_tb(*args, **kwargs):
        pytest.fail("Webhook tried ThingsBoard while integration was disabled")

    monkeypatch.setattr(main, "ensure_tb_device_from_profile", unexpected_tb)
    device_id = "without-thingsboard-device"
    envelope = {
        "end_device_ids": {"device_id": device_id, "dev_eui": "70B3D57ED0000B01"},
        "uplink_message": {"decoded_payload": {"temperature_1": 55.0}},
    }
    discovery = main.process_ttn_webhook_background(envelope)
    assert discovery["status"] == "ok_profile_validated"
    assert discovery["telemetry_stored"] is True
    assert discovery["thingsboard_enabled"] is False
    assert discovery["thingsboard_error"] is None
    assert emails == []

    client_id = db_conn.execute("INSERT INTO clients (name) VALUES ('No TB Client')").lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'No TB Site')", (client_id,)
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'No TB Building')", (site_id,)
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'No TB Floor')", (building_id,)
    ).lastrowid
    db_conn.execute(
        "UPDATE devices SET floor_id = ?, is_placed = 1 WHERE device_id = ?", (floor_id, device_id)
    )
    db_conn.commit()
    operational = main.process_ttn_webhook_background(envelope)
    assert operational["status"] == "ok_profile_validated"
    assert operational["alarms_suppressed"] is False
    assert emails and emails[0]["device_id"] == device_id
    assert db_conn.execute(
        "SELECT COUNT(*) FROM historical_telemetry WHERE device_id = ?", (device_id,)
    ).fetchone()[0] == 2
    assert db_conn.execute(
        "SELECT COUNT(*) FROM alarm_history WHERE device_id = ? AND resolved = 0", (device_id,)
    ).fetchone()[0] >= 1
    assert main.read_tb_latest_telemetry(device_id)["temperature_1"] == 55.0
    assert main.sync_tb_attributes_from_profile({}, {})["status"] == "disabled"
    assert main.sync_tb_attributes_from_row({}) is None
    stored = db_conn.execute(
        "SELECT telemetry FROM device_latest_telemetry WHERE device_id = ?", (device_id,)
    ).fetchone()[0]
    assert json.loads(stored)["temperature_1"] == 55.0
