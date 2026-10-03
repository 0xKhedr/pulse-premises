from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.main import process_ttn_webhook_background
from app.services.device_state import build_device_state
from app.services.lorawan_identity import derive_dev_eui_from_mac


def _uplink(device_id: str, dev_eui: str, temperature: float) -> dict:
    return {
        "end_device_ids": {
            "device_id": device_id,
            "dev_eui": dev_eui,
            "application_ids": {"application_id": "smart-building-lora-2"},
        },
        "uplink_message": {
            "decoded_payload": {"temperature_1": temperature},
            "rx_metadata": [
                {
                    "gateway_ids": {"gateway_id": "commissioning-gateway"},
                    "rssi": -87,
                    "snr": 8.5,
                }
            ],
        },
    }


def test_shared_dev_eui_derivation_matches_firmware():
    assert derive_dev_eui_from_mac("AA:BB:CC:DD:EE:FF") == "AABBCCFFFEDDEEFF"
    assert derive_dev_eui_from_mac(
        "AA:BB:CC:DD:EE:FF", namespace="smart-building-lora-2"
    ) == "0B519742C84A7C5D"


def test_connection_and_monitoring_states():
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    never = build_device_state({"is_placed": 0, "floor_id": None}, now=now)
    stale = build_device_state(
        {
            "is_placed": 1,
            "floor_id": 5,
            "last_seen": (now - timedelta(minutes=10)).isoformat(),
        },
        now=now,
    )
    offline = build_device_state(
        {
            "is_placed": 1,
            "floor_id": 5,
            "last_seen": (now - timedelta(days=2)).isoformat(),
        },
        now=now,
    )
    assert never["connection_state"] == "never_connected"
    assert never["monitoring_state"] == "discovery"
    assert stale["connection_state"] == "stale"
    assert stale["monitoring_state"] == "operational"
    assert offline["connection_state"] == "offline"


def test_database_rejects_placed_device_without_floor(db_conn):
    with pytest.raises(sqlite3.IntegrityError, match="placed device requires floor_id"):
        db_conn.execute(
            """
            INSERT INTO devices (chip_mac, device_id, is_placed)
            VALUES ('invalid-placed-chip', 'invalid-placed-device', 1)
            """
        )
    db_conn.rollback()


def test_discovery_suppresses_alarms_until_floor_commissioning(
    client,
    db_conn,
    monkeypatch,
):
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)
    device_id = "commissioning-lifecycle-device"
    dev_eui = "70B3D57ED0000099"

    discovery = process_ttn_webhook_background(_uplink(device_id, dev_eui, 55.0))
    assert discovery["telemetry_stored"] is True
    assert discovery["monitoring_state"] == "discovery"
    assert discovery["alarms_suppressed"] is True
    assert db_conn.execute(
        "SELECT COUNT(*) FROM alarm_history WHERE device_id = ?",
        (device_id,),
    ).fetchone()[0] == 0

    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Commissioning Client')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Commissioning Site')",
        (client_id,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Commissioning Building')",
        (site_id,),
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'Commissioning Floor')",
        (building_id,),
    ).lastrowid
    db_conn.execute(
        """
        UPDATE devices
        SET client_id = ?, site_id = ?, building_id = ?, floor_id = ?,
            x = 10, y = 20, is_placed = 1
        WHERE device_id = ?
        """,
        (client_id, site_id, building_id, floor_id, device_id),
    )
    db_conn.commit()

    operational = process_ttn_webhook_background(_uplink(device_id, dev_eui, 55.0))
    assert operational["telemetry_stored"] is True
    assert operational["monitoring_state"] == "operational"
    assert operational["alarms_suppressed"] is False
    assert db_conn.execute(
        "SELECT COUNT(*) FROM alarm_history WHERE device_id = ? AND resolved = 0",
        (device_id,),
    ).fetchone()[0] >= 1


def test_webhook_alarm_writes_roll_back_if_telemetry_storage_fails(
    db_conn,
    monkeypatch,
):
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)
    device_id = "atomic-uplink-device"
    dev_eui = "70B3D57ED0000100"
    process_ttn_webhook_background(_uplink(device_id, dev_eui, 20.0))

    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Atomic Client')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Atomic Site')",
        (client_id,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Atomic Building')",
        (site_id,),
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'Atomic Floor')",
        (building_id,),
    ).lastrowid
    db_conn.execute(
        "UPDATE devices SET floor_id = ?, is_placed = 1 WHERE device_id = ?",
        (floor_id, device_id),
    )
    db_conn.commit()

    def fail_storage(*args, **kwargs):
        raise RuntimeError("simulated telemetry storage failure")

    monkeypatch.setattr("app.main.save_latest_telemetry", fail_storage)
    result = process_ttn_webhook_background(_uplink(device_id, dev_eui, 55.0))
    assert result["status"] == "error"
    assert db_conn.execute(
        "SELECT COUNT(*) FROM alarm_history WHERE device_id = ?",
        (device_id,),
    ).fetchone()[0] == 0
    assert db_conn.execute(
        """
        SELECT COUNT(*) FROM profile_alarm_runtime_state
        WHERE device_id = ? AND active_alarm_history_id IS NOT NULL
        """,
        (device_id,),
    ).fetchone()[0] == 0
