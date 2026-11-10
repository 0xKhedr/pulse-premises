from __future__ import annotations

import base64
import json

import pytest
import requests

from app.config import settings
from app.main import create_login_session, create_password_hash
from app.services.ttn import TTNClient

DEVICE_ID = "downlink-test-node"
DEV_EUI = "70B3D57ED0008801"


@pytest.fixture
def downlinks(client, db_conn, monkeypatch):
    monkeypatch.setattr(settings, "PREVIEW_MODE", False)
    pw = create_password_hash("DownlinkTestAdmin123!")
    db_conn.execute(
        """INSERT OR REPLACE INTO users
        (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (9826, 'downlink-admin@test.local', 'Downlink Admin', 'admin', 1, ?, ?)""",
        (pw["password_hash"], pw["password_salt"]),
    )
    db_conn.execute(
        """INSERT OR REPLACE INTO devices (device_id, chip_mac, dev_eui, label, node_type)
        VALUES (?, 'AA:11:99:88:00:01', ?, 'Lobby environment', 'environment')""",
        (DEVICE_ID, DEV_EUI),
    )
    db_conn.commit()
    client.cookies.set("sbms_session", create_login_session(db_conn, 9826))
    calls = []

    def push(device_id, **kwargs):
        calls.append({"device_id": device_id, **kwargs})

    monkeypatch.setattr("app.routers.devices.ttn_client.push_downlink", push)
    yield client, calls
    db_conn.execute("DELETE FROM devices WHERE device_id = ?", (DEVICE_ID,))
    db_conn.commit()


@pytest.mark.parametrize("encoding,payload,expected", [
    ("text", "Hello", b"Hello"),
    ("text", "café", "café".encode()),
    ("hex", "48 65 00 FF", b"He\x00\xff"),
    ("base64", "SGU A/w==\n", b"He\x00\xff"),
])
def test_queue_preserves_bytes_and_reports_pending_delivery(downlinks, db_conn, encoding, payload, expected):
    client, calls = downlinks
    response = client.post(f"/devices/{DEVICE_ID}/downlinks", json={
        "payload": payload, "encoding": encoding, "f_port": 15, "confirmed": False,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "queued"
    assert data["device_class"] == "A"
    assert "not yet confirmed" in data["message"]
    assert data["byte_count"] == len(expected)
    assert len(calls) == 1
    assert calls[0]["f_port"] == 15
    assert calls[0]["confirmed"] is False
    assert base64.b64decode(calls[0]["frm_payload"]) == expected
    assert calls[0]["correlation_id"] == data["correlation_id"]
    audit = db_conn.execute(
        "SELECT actor, details FROM audit_log WHERE action = 'device_downlink_queued' AND target_id = ? ORDER BY id DESC LIMIT 1",
        (DEVICE_ID,),
    ).fetchone()
    assert audit["actor"] == "downlink-admin@test.local"
    details = json.loads(audit["details"])
    assert details["byte_count"] == len(expected)
    assert not {"payload", "frm_payload"} & details.keys()


@pytest.mark.parametrize("data,code", [
    ({"payload": "A" * 52}, 400),
    ({"payload": "é" * 26}, 400),
    ({"payload": "0", "encoding": "hex"}, 400),
    ({"payload": "0x12", "encoding": "hex"}, 400),
    ({"payload": "XX", "encoding": "hex"}, 400),
    ({"payload": "!!!", "encoding": "base64"}, 400),
    ({"payload": " ", "encoding": "base64"}, 400),
    ({"payload": ""}, 422),
    ({"payload": "hello", "f_port": 0}, 422),
    ({"payload": "hello", "f_port": 224}, 422),
    ({"payload": "hello", "f_port": True}, 422),
    ({"payload": "hello", "confirmed": "false"}, 422),
    ({"payload": "hello", "class_b_c": {}}, 422),
])
def test_invalid_requests_never_reach_ttn(downlinks, data, code):
    client, calls = downlinks
    assert client.post(f"/devices/{DEVICE_ID}/downlinks", json=data).status_code == code
    assert calls == []


def test_exact_limit_and_confirmed_option(downlinks):
    client, calls = downlinks
    response = client.post(f"/devices/{DEVICE_ID}/downlinks", json={"payload": "a" * 51, "confirmed": True})
    assert response.status_code == 200
    assert calls[0]["confirmed"] is True
    assert response.json()["byte_count"] == 51


def test_unknown_device_and_preview_mode_do_not_send(downlinks, monkeypatch):
    client, calls = downlinks
    assert client.post("/devices/missing-downlink-node/downlinks", json={"payload": "test"}).status_code == 404
    monkeypatch.setattr(settings, "PREVIEW_MODE", True)
    assert client.post(f"/devices/{DEVICE_ID}/downlinks", json={"payload": "test"}).status_code == 409
    assert calls == []


def test_targets_do_not_fetch_telemetry_and_respect_rejection(downlinks, db_conn, monkeypatch):
    client, calls = downlinks

    def forbidden(*args, **kwargs):
        pytest.fail("Selecting a downlink target must not fetch remote telemetry")

    monkeypatch.setattr("app.routers.devices.read_tb_latest_telemetry", forbidden)
    targets = client.get("/devices/downlink-targets").json()["devices"]
    assert any(row["device_id"] == DEVICE_ID for row in targets)
    db_conn.execute(
        """INSERT INTO device_enrollment_candidates
        (application_id, dev_eui, join_eui, device_id, status, verified_key_hash)
        VALUES (?, ?, '0000000000000000', ?, 'rejected', 'test')""",
        (settings.TTN_APP_ID, DEV_EUI, DEVICE_ID),
    )
    db_conn.commit()
    try:
        targets = client.get("/devices/downlink-targets").json()["devices"]
        assert all(row["device_id"] != DEVICE_ID for row in targets)
        assert client.post(f"/devices/{DEVICE_ID}/downlinks", json={"payload": "test"}).status_code == 409
        assert calls == []
    finally:
        db_conn.execute("DELETE FROM device_enrollment_candidates WHERE dev_eui = ?", (DEV_EUI,))
        db_conn.commit()


@pytest.mark.parametrize("status,expected", [(400, 502), (401, 503), (403, 503), (404, 409), (500, 502)])
def test_ttn_errors_are_safe_and_do_not_claim_delivery(downlinks, monkeypatch, status, expected):
    client, calls = downlinks

    def fail(*args, **kwargs):
        response = requests.Response()
        response.status_code = status
        raise requests.HTTPError("private upstream error", response=response)

    monkeypatch.setattr("app.routers.devices.ttn_client.push_downlink", fail)
    response = client.post(f"/devices/{DEVICE_ID}/downlinks", json={"payload": "test"})
    assert response.status_code == expected
    assert "private upstream error" not in response.text
    if status >= 500:
        assert "unknown" in response.json()["detail"]
    assert calls == []


def test_timeout_is_not_retried_and_reports_unknown_queue_state(downlinks, monkeypatch):
    client, _ = downlinks
    attempts = []

    def timeout(*args, **kwargs):
        attempts.append(1)
        raise requests.Timeout("private transport error")

    monkeypatch.setattr("app.routers.devices.ttn_client.push_downlink", timeout)
    response = client.post(f"/devices/{DEVICE_ID}/downlinks", json={"payload": "test"})
    assert response.status_code == 504
    assert "unknown" in response.json()["detail"]
    assert len(attempts) == 1


@pytest.mark.parametrize("role", [None, "client"])
def test_downlinks_require_admin(downlinks, db_conn, role):
    client, calls = downlinks
    client.cookies.clear()
    if role:
        db_conn.execute("UPDATE users SET role = 'client' WHERE id = 9826")
        db_conn.commit()
        client.cookies.set("sbms_session", create_login_session(db_conn, 9826))
    for method, path, kwargs in [
        ("POST", f"/devices/{DEVICE_ID}/downlinks", {"json": {"payload": "test"}}),
        ("GET", "/devices/downlink-targets", {}),
    ]:
        response = client.request(method, path, follow_redirects=False, **kwargs)
        assert response.status_code in (302, 401, 403)
    assert calls == []


def test_ttn_appends_a_class_a_message_without_replacing_queue(monkeypatch):
    ttn = TTNClient()
    ttn.app_id = "my-application"
    calls = []
    monkeypatch.setattr(ttn, "_request", lambda *args, **kwargs: calls.append((args, kwargs)))
    ttn.push_downlink("my-node", f_port=15, frm_payload="SGVsbG8=", confirmed=False, correlation_id="test-id")
    args, kwargs = calls[0]
    assert args == ("POST", "/api/v3/as/applications/my-application/devices/my-node/down/push")
    message = kwargs["json"]["downlinks"][0]
    assert message == {"f_port": 15, "frm_payload": "SGVsbG8=", "confirmed": False,
                       "priority": "NORMAL", "correlation_ids": ["test-id"]}
    assert "class_b_c" not in message


@pytest.mark.parametrize("seconds,expected", [
    (30, b"\x01\x01\x00\x00\x00\x1e"),
    (300, b"\x01\x01\x00\x00\x01\x2c"),
    (86400, b"\x01\x01\x00\x01\x51\x80"),
])
def test_configuration_queues_binary_command_and_remembers_request(downlinks, seconds, expected):
    client, calls = downlinks
    response = client.post(f"/devices/{DEVICE_ID}/configuration/send-rate", json={"send_interval_seconds": seconds})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["send_interval_seconds"] == seconds
    assert len(calls) == 1
    assert calls[0]["f_port"] == 42
    assert calls[0]["confirmed"] is False
    assert base64.b64decode(calls[0]["frm_payload"]) == expected
    config = client.get(f"/devices/{DEVICE_ID}/configuration").json()
    assert config["can_queue"] is True
    assert config["send_rate"]["last_queued"]["send_interval_seconds"] == seconds
    assert config["send_rate"]["last_queued"]["correlation_id"] == calls[0]["correlation_id"]
    assert "applied" not in config


@pytest.mark.parametrize("seconds", [0, 29, 86401, -1, 1.5, True, "300", None])
def test_configuration_rejects_invalid_rates_without_sending(downlinks, seconds):
    client, calls = downlinks
    response = client.post(f"/devices/{DEVICE_ID}/configuration/send-rate", json={"send_interval_seconds": seconds})
    assert response.status_code == 422
    assert calls == []


def test_configuration_page_and_defaults(downlinks, db_conn):
    client, calls = downlinks
    db_conn.execute("DELETE FROM audit_log WHERE action = 'device_send_rate_queued' AND target_id = ?", (DEVICE_ID,))
    db_conn.execute("UPDATE devices SET label = '<script>unsafe</script>' WHERE device_id = ?", (DEVICE_ID,))
    db_conn.commit()
    config = client.get(f"/devices/{DEVICE_ID}/configuration")
    assert config.status_code == 200
    assert config.json()["send_rate"] == {"min_seconds": 30, "max_seconds": 86400, "default_seconds": 30, "last_queued": None}
    page = client.get(f"/devices/{DEVICE_ID}/configuration-page")
    assert page.status_code == 200
    assert "data-device-configuration" in page.text
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in page.text
    assert "<script>unsafe</script>" not in page.text
    assert calls == []
    for suffix in ("configuration", "configuration-page"):
        assert client.get(f"/devices/missing-configuration-device/{suffix}").status_code == 404


def test_configuration_uses_same_approval_preview_and_failure_guards(downlinks, db_conn, monkeypatch):
    client, calls = downlinks
    path = f"/devices/{DEVICE_ID}/configuration"
    monkeypatch.setattr(settings, "PREVIEW_MODE", True)
    assert client.get(path).json()["can_queue"] is False
    assert client.post(path + "/send-rate", json={"send_interval_seconds": 60}).status_code == 409
    monkeypatch.setattr(settings, "PREVIEW_MODE", False)
    db_conn.execute("UPDATE devices SET dev_eui = NULL WHERE device_id = ?", (DEVICE_ID,))
    db_conn.commit()
    assert client.get(path).json()["can_queue"] is False
    assert client.post(path + "/send-rate", json={"send_interval_seconds": 60}).status_code == 409
    assert calls == []


def test_configuration_timeout_does_not_replace_last_queued_value(downlinks, monkeypatch):
    client, calls = downlinks
    path = f"/devices/{DEVICE_ID}/configuration"
    assert client.post(path + "/send-rate", json={"send_interval_seconds": 300}).status_code == 200
    attempts = []

    def timeout(*args, **kwargs):
        attempts.append(1)
        raise requests.Timeout("unknown queue state")

    monkeypatch.setattr("app.routers.devices.ttn_client.push_downlink", timeout)
    response = client.post(path + "/send-rate", json={"send_interval_seconds": 600})
    assert response.status_code == 504
    assert "unknown" in response.json()["detail"]
    assert attempts == [1]
    assert client.get(path).json()["send_rate"]["last_queued"]["send_interval_seconds"] == 300


@pytest.mark.parametrize("role", [None, "client"])
def test_configuration_requires_admin(downlinks, db_conn, role):
    client, calls = downlinks
    client.cookies.clear()
    if role:
        db_conn.execute("UPDATE users SET role = 'client' WHERE id = 9826")
        db_conn.commit()
        client.cookies.set("sbms_session", create_login_session(db_conn, 9826))
    path = f"/devices/{DEVICE_ID}/configuration"
    for suffix in ("", "-page"):
        assert client.get(path + suffix, follow_redirects=False).status_code in (302, 401, 403)
    assert client.post(path + "/send-rate", json={"send_interval_seconds": 60}, follow_redirects=False).status_code in (302, 401, 403)
    assert calls == []
