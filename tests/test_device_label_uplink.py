"""
Nicknames sent over LoRa.

The nickname is cosmetic, but it is written straight into the admin UI and the
CSV exports, and it arrives from radio traffic rather than from an
authenticated request. So it is validated like untrusted input: the tests care
as much about what is refused as about what is stored.
"""

from __future__ import annotations

import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.device_label import (
    LABEL_F_PORT,
    MAX_LABEL_LENGTH,
    is_label_port,
    parse_label_uplink,
    sanitize_label,
)


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nickname",
    [
        "temp_c_floor3_308",
        "Lobby North",
        "AHU-2/return",
        "room 308",
        "hvac#4",
        "a",
        "x" * MAX_LABEL_LENGTH,
    ],
)
def test_accepts_the_nicknames_an_installer_would_type(nickname: str):
    assert parse_label_uplink(b64(nickname)) == nickname


@pytest.mark.parametrize(
    "raw, reason",
    [
        (b"", "empty payload"),
        (b"   ", "whitespace only"),
        (b"x" * (MAX_LABEL_LENGTH + 1), "too long"),
        (b"\xff\xfe\xfd", "not utf-8"),
        (b"floor\x003", "embedded NUL"),
        (b"=SUM(A1)", "spreadsheet formula"),
        (b"<script>alert(1)</script>", "markup"),
        (b"room\x07308", "non-whitespace control character"),
        (b"caf\xc3\xa9", "outside the allowed set"),
    ],
)
def test_refuses_payloads_that_are_not_safe_nicknames(raw: bytes, reason: str):
    assert parse_label_uplink(raw) is None, reason


def test_collapses_surrounding_and_repeated_whitespace():
    assert parse_label_uplink(b64("  floor 3   room  308 ")) == "floor 3 room 308"


def test_stray_newlines_and_tabs_normalize_to_spaces():
    """
    A newline is whitespace, so it folds into a space rather than being
    refused. That removes the log-injection and layout risks a raw control
    character would carry, while still accepting a nickname whose terminal
    added a stray line break. Control characters that are not whitespace are
    refused outright, above.
    """
    assert parse_label_uplink(b"room" + bytes([10]) + b"308") == "room 308"
    assert parse_label_uplink(b"floor3" + bytes([9]) + b"room308") == "floor3 room308"


@pytest.mark.parametrize("value", [None, "not base64!!", "", b""])
def test_unusable_input_returns_none_rather_than_raising(value):
    assert parse_label_uplink(value) is None


def test_sanitize_is_reusable_on_its_own():
    assert sanitize_label(" a  b ") == "a b"
    assert sanitize_label("=cmd") is None


@pytest.mark.parametrize(
    "port, expected",
    [(10, True), ("10", True), (1, False), (None, False), ("abc", False), (0, False)],
)
def test_only_the_reserved_port_is_treated_as_a_nickname(port, expected):
    assert is_label_port(port) is expected


# ---------------------------------------------------------------------------
# End to end through the webhook
# ---------------------------------------------------------------------------

DEV_EUI = "70B3D57ED0007701"
DEVICE_ID = "label-test-node"


@pytest.fixture
def webhook(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "label-secret")
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)
    return client


def send(webhook: TestClient, *, f_port: int, frm_payload=None, decoded=None):
    uplink: dict = {"f_port": f_port}
    if frm_payload is not None:
        uplink["frm_payload"] = frm_payload
    if decoded is not None:
        uplink["decoded_payload"] = decoded
    return webhook.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "label-secret"},
        json={
            "end_device_ids": {
                "device_id": DEVICE_ID,
                "dev_eui": DEV_EUI,
                "application_ids": {"application_id": "smart-building-lora-2"},
            },
            "uplink_message": uplink,
        },
    )


def label_of(db_conn: sqlite3.Connection) -> str | None:
    row = db_conn.execute(
        "SELECT label FROM devices WHERE device_id = ?", (DEVICE_ID,)
    ).fetchone()
    return row["label"] if row else None


def test_a_nickname_uplink_renames_a_discovered_device(
    webhook: TestClient, db_conn: sqlite3.Connection
):
    """The whole point: fifty identical boards become distinguishable."""
    assert send(webhook, f_port=1, decoded={"temperature_1": 21.0}).status_code == 200
    assert label_of(db_conn) == f"Discovered {DEVICE_ID}"

    assert send(
        webhook, f_port=LABEL_F_PORT, frm_payload=b64("temp_c_floor3_308")
    ).status_code == 200

    assert label_of(db_conn) == "temp_c_floor3_308"


def test_a_nickname_uplink_does_not_disturb_telemetry(
    webhook: TestClient, db_conn: sqlite3.Connection
):
    """
    Port 10 carries no sensor data. It must not overwrite the last reading
    with an empty one, which is what would happen if it fell through into
    normal profile validation.
    """
    send(webhook, f_port=1, decoded={"temperature_1": 23.5})
    before = db_conn.execute(
        "SELECT telemetry FROM device_latest_telemetry WHERE device_id = ?",
        (DEVICE_ID,),
    ).fetchone()["telemetry"]

    send(webhook, f_port=LABEL_F_PORT, frm_payload=b64("Lobby North"))

    after = db_conn.execute(
        "SELECT telemetry FROM device_latest_telemetry WHERE device_id = ?",
        (DEVICE_ID,),
    ).fetchone()["telemetry"]
    assert after == before


def test_a_rejected_nickname_leaves_the_existing_one_alone(
    webhook: TestClient, db_conn: sqlite3.Connection
):
    send(webhook, f_port=1, decoded={"temperature_1": 21.0})
    send(webhook, f_port=LABEL_F_PORT, frm_payload=b64("Lobby North"))
    assert label_of(db_conn) == "Lobby North"

    response = send(
        webhook, f_port=LABEL_F_PORT, frm_payload=base64.b64encode(b"=SUM(A1)").decode()
    )

    assert response.status_code == 200, "a bad nickname is not a webhook failure"
    assert label_of(db_conn) == "Lobby North"


def test_resending_the_same_nickname_is_a_no_op(
    webhook: TestClient, db_conn: sqlite3.Connection
):
    """
    The device re-sends its nickname periodically so a missed uplink heals.
    That must not write a database row or an audit entry every time.
    """
    send(webhook, f_port=1, decoded={"temperature_1": 21.0})
    send(webhook, f_port=LABEL_F_PORT, frm_payload=b64("Roof AHU"))

    audit_before = db_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'device_label_from_uplink'"
    ).fetchone()[0]

    send(webhook, f_port=LABEL_F_PORT, frm_payload=b64("Roof AHU"))

    audit_after = db_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'device_label_from_uplink'"
    ).fetchone()[0]
    assert audit_after == audit_before
    assert label_of(db_conn) == "Roof AHU"
