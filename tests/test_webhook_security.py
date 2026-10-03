from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings


def test_webhook_rejects_when_secret_not_configured(client: TestClient, monkeypatch):
    """
    An unset secret must reject, not wave the request through.

    The check used to be `if expected:` -- and the secret is empty by default,
    so the signature verification was skipped entirely and anyone could POST
    arbitrary telemetry into the processing queue.
    """
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "")

    response = client.post("/ttn-webhook", json={"end_device_ids": {}})
    assert response.status_code == 503, response.text
    assert "TTN_WEBHOOK_SECRET" in response.text


def test_webhook_rejects_wrong_secret(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "the-real-secret")

    response = client.post(
        "/ttn-webhook",
        json={"end_device_ids": {}},
        headers={"X-Webhook-Secret": "not-the-secret"},
    )
    assert response.status_code == 401, response.text


def test_webhook_rejects_missing_header(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "the-real-secret")

    response = client.post("/ttn-webhook", json={"end_device_ids": {}})
    assert response.status_code == 401, response.text


def test_webhook_accepts_correct_secret(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "the-real-secret")

    response = client.post(
        "/ttn-webhook",
        json={"end_device_ids": {}},
        headers={"X-Webhook-Secret": "the-real-secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"


def test_valid_webhook_auto_provisions_unknown_device(
    client: TestClient,
    db_conn,
    monkeypatch,
):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "discovery-secret")
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)
    monkeypatch.setattr("app.main.send_alarm_email", lambda **kwargs: None)

    device_id = "unknown-webhook-device"
    dev_eui = "70B3D57ED0000042"
    response = client.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "discovery-secret"},
        json={
            "end_device_ids": {
                "device_id": device_id,
                "dev_eui": dev_eui,
                "join_eui": "0000000000000000",
                "application_ids": {
                    "application_id": "smart-building-lora-2",
                },
            },
            "uplink_message": {
                "decoded_payload": {
                    "temperature_1": 23.5,
                    "relative_humidity_2": 47.0,
                },
                "rx_metadata": [
                    {
                        "gateway_ids": {"gateway_id": "field-gateway"},
                        "rssi": -91,
                        "snr": 7.25,
                    }
                ],
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"

    device = db_conn.execute(
        "SELECT * FROM devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    assert device is not None
    assert device["dev_eui"] == dev_eui
    assert device["is_placed"] == 0
    assert device["floor_id"] is None
    assert device["room_id"] is None

    latest = db_conn.execute(
        "SELECT telemetry FROM device_latest_telemetry WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    assert latest is not None
    assert '"temperature": 23.5' in latest["telemetry"]

    repeated = client.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "discovery-secret"},
        json={
            "end_device_ids": {
                "device_id": "renamed-in-ttn",
                "dev_eui": dev_eui.lower(),
                "application_ids": {"application_id": "smart-building-lora-2"},
            },
            "uplink_message": {
                "decoded_payload": {"temperature_1": 24.0},
            },
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert db_conn.execute(
        "SELECT COUNT(*) FROM devices WHERE dev_eui = ? COLLATE NOCASE",
        (dev_eui,),
    ).fetchone()[0] == 1
    updated = db_conn.execute(
        "SELECT telemetry FROM device_latest_telemetry WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    assert '"temperature": 24.0' in updated["telemetry"]
