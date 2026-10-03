from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from app.config import settings
from app.main import auto_provision_discovered_device

# The test database is shared across the whole session, so this module keeps
# to its own id/name namespace.
PREFIX = "inbox_limit_dev_"


def _seed_unplaced(db_conn: sqlite3.Connection, count: int) -> None:
    for index in range(count):
        db_conn.execute(
            """
            INSERT OR REPLACE INTO devices
                (chip_mac, device_id, node_type, is_placed)
            VALUES (?, ?, 'environment', 0)
            """,
            (f"AA:BB:CC:94:00:{index:02X}", f"{PREFIX}{index}"),
        )
    db_conn.commit()


def _envelope(device_id: str, dev_eui: str) -> dict:
    return {
        "end_device_ids": {
            "device_id": device_id,
            "dev_eui": dev_eui,
            "application_ids": {"application_id": "smart-building-lora-2"},
        }
    }


def test_discovery_refused_once_inbox_is_full(
    db_conn: sqlite3.Connection, monkeypatch
):
    """
    Zero-touch discovery creates a devices row per unknown DevEUI. Without a
    ceiling, anyone able to reach the webhook could grow the table without
    bound, so the inbox must refuse new discoveries once it is full.
    """
    existing_unplaced = db_conn.execute(
        "SELECT COUNT(*) FROM devices WHERE is_placed = 0"
    ).fetchone()[0]

    monkeypatch.setattr(
        settings, "MAX_UNPLACED_DISCOVERED_DEVICES", existing_unplaced + 3
    )
    _seed_unplaced(db_conn, 3)

    with pytest.raises(HTTPException) as excinfo:
        auto_provision_discovered_device(
            db_conn,
            "inbox_limit_overflow",
            _envelope("inbox_limit_overflow", "70B3D57ED0009901"),
        )

    assert excinfo.value.status_code == 429
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM devices WHERE device_id = 'inbox_limit_overflow'"
        ).fetchone()[0]
        == 0
    ), "device row must not be created once the inbox is full"


def test_discovery_still_works_below_the_limit(
    db_conn: sqlite3.Connection, monkeypatch
):
    monkeypatch.setattr(settings, "MAX_UNPLACED_DISCOVERED_DEVICES", 100_000)

    device = auto_provision_discovered_device(
        db_conn,
        "inbox_limit_allowed",
        _envelope("inbox_limit_allowed", "70B3D57ED0009902"),
    )

    assert device["device_id"] == "inbox_limit_allowed"
    assert device["is_placed"] == 0, "newly discovered devices start unplaced"
