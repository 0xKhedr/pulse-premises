from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_login_session, create_password_hash

SECRET_APP_KEY = "0123456789ABCDEF0123456789ABCDEF"

# The test database is session-scoped and shared by every test, so this module
# owns a private id namespace and must never reuse low ids other tests seed.
USER_ID = 9301
CLIENT_ID = 9310
SITE_ID = 9311
BUILDING_ID = 9312
FLOOR_ID = 9313
ROOM_ID = 9314


@pytest.fixture
def admin_client(client: TestClient, db_conn: sqlite3.Connection):
    pw = create_password_hash("HierRedactPass123!")
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (?, 'hier_redact_admin@test.local', 'Hier Redact', 'admin', 1, ?, ?)
        """,
        (USER_ID, pw["password_hash"], pw["password_salt"]),
    )
    db_conn.commit()
    client.cookies.set("sbms_session", create_login_session(db_conn, USER_ID))
    return client


@pytest.fixture
def floor_with_device(db_conn: sqlite3.Connection):
    """Two placed devices carrying a LoRaWAN root key."""
    db_conn.execute(
        "INSERT OR REPLACE INTO clients (id, name) VALUES (?, 'Redact Co')",
        (CLIENT_ID,),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO sites (id, client_id, name) VALUES (?, ?, 'Redact Site')",
        (SITE_ID, CLIENT_ID),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO buildings (id, site_id, name) VALUES (?, ?, 'Redact Bldg')",
        (BUILDING_ID, SITE_ID),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO floors (id, building_id, name, floor_number)"
        " VALUES (?, ?, 'Redact Floor', 1)",
        (FLOOR_ID, BUILDING_ID),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO rooms (id, floor_id, room_name, polygon_points, x, y)"
        " VALUES (?, ?, 'Redact Room', '[]', 0, 0)",
        (ROOM_ID, FLOOR_ID),
    )
    # One device inside a room, one placed directly on the floor: these are
    # served by two different code paths inside get_floor_live.
    db_conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (chip_mac, device_id, node_type, app_key,
             client_id, site_id, building_id, floor_id, room_id, is_placed)
        VALUES ('AA:BB:CC:93:00:01', 'redact_room_dev', 'environment', ?,
                ?, ?, ?, ?, ?, 1)
        """,
        (SECRET_APP_KEY, CLIENT_ID, SITE_ID, BUILDING_ID, FLOOR_ID, ROOM_ID),
    )
    db_conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (chip_mac, device_id, node_type, app_key,
             client_id, site_id, building_id, floor_id, room_id, is_placed)
        VALUES ('AA:BB:CC:93:00:02', 'redact_floor_dev', 'environment', ?,
                ?, ?, ?, ?, NULL, 1)
        """,
        (SECRET_APP_KEY, CLIENT_ID, SITE_ID, BUILDING_ID, FLOOR_ID),
    )
    db_conn.commit()
    return db_conn


def test_floor_live_does_not_leak_app_key(admin_client, floor_with_device):
    """
    /floors/{id}/live builds device dicts from SELECT *, so the LoRaWAN root
    key rides along unless it is explicitly stripped. Covers both the in-room
    path and the floor-level path, which are built by separate code.
    """
    response = admin_client.get(f"/floors/{FLOOR_ID}/live")
    assert response.status_code == 200, response.text

    assert SECRET_APP_KEY not in response.text, (
        "floor-live response leaked the LoRaWAN app_key"
    )

    payload = response.json()
    room_devices = [d for room in payload.get("rooms", []) for d in room.get("devices", [])]
    floor_devices = payload.get("floor_devices", [])
    assert room_devices, "expected the seeded in-room device"
    assert floor_devices, "expected the seeded floor-level device"

    for device in room_devices + floor_devices:
        if "app_key" in device:
            assert device["app_key"] == "***hidden***"


def test_floor_details_does_not_leak_app_key(admin_client, floor_with_device):
    response = admin_client.get(f"/floors/{FLOOR_ID}/details")
    assert response.status_code == 200, response.text
    assert SECRET_APP_KEY not in response.text, (
        "floor-details response leaked the LoRaWAN app_key"
    )
