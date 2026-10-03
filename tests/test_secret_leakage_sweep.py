from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_login_session, create_password_hash
from tests.route_inventory import fill_path, get_routes

# Distinctive sentinels: if any of these strings reaches a response body, a
# credential escaped. They are deliberately unlike anything the app generates,
# so a match cannot be coincidental.
SENTINEL_APP_KEY = "SENTINELAPPKEY00000000000000DEAD"
SENTINEL_PASSWORD_HASH = "sentinel-password-hash-must-never-be-served"
SENTINEL_PASSWORD_SALT = "sentinel-password-salt-must-never-be-served"

ADMIN_ID = 9501
CLIENT_ID = 9510
SITE_ID = 9511
BUILDING_ID = 9512
FLOOR_ID = 9513
ROOM_ID = 9514
GATEWAY_DB_ID = 9515


@pytest.fixture
def offline_upstreams(monkeypatch):
    """Make TTN/ThingsBoard behave as unreachable, with no real network."""

    def boom(*args, **kwargs):
        raise ConnectionError("upstream unavailable (stubbed in tests)")

    import app.main as main_module

    targets = (
        "read_tb_latest_telemetry",
        "ttn_get_gateway_status",
        "ttn_get_gateway_connection_stats",
    )
    for name in targets:
        if hasattr(main_module, name):
            monkeypatch.setattr(main_module, name, boom)

    for module_name in (
        "app.routers.devices",
        "app.routers.gateways",
        "app.routers.admin",
        "app.routers.hierarchy",
    ):
        module = __import__(module_name, fromlist=["*"])
        for name in targets:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, boom)


@pytest.fixture
def seeded_secrets(db_conn: sqlite3.Connection):
    """A fully placed device and a user, both carrying sentinel credentials."""
    pw = create_password_hash("SweepAdminPass123!")
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (?, 'sweep_admin@test.local', 'Sweep Admin', 'admin', 1, ?, ?)
        """,
        (ADMIN_ID, pw["password_hash"], pw["password_salt"]),
    )
    # A second user whose stored credential material is a sentinel: no
    # endpoint should ever serialize password columns.
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (9502, 'sweep_victim@test.local', 'Sweep Victim', 'client', 1, ?, ?)
        """,
        (SENTINEL_PASSWORD_HASH, SENTINEL_PASSWORD_SALT),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO clients (id, name) VALUES (?, 'Sweep Co')",
        (CLIENT_ID,),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO sites (id, client_id, name) VALUES (?, ?, 'Sweep Site')",
        (SITE_ID, CLIENT_ID),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO buildings (id, site_id, name) VALUES (?, ?, 'Sweep Bldg')",
        (BUILDING_ID, SITE_ID),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO floors (id, building_id, name, floor_number)"
        " VALUES (?, ?, 'Sweep Floor', 1)",
        (FLOOR_ID, BUILDING_ID),
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO rooms (id, floor_id, room_name, polygon_points, x, y)"
        " VALUES (?, ?, 'Sweep Room', '[]', 0, 0)",
        (ROOM_ID, FLOOR_ID),
    )
    # Placed in a room, and placed directly on the floor: distinct code paths.
    db_conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (chip_mac, device_id, node_type, app_key, dev_eui,
             client_id, site_id, building_id, floor_id, room_id, is_placed)
        VALUES ('AA:BB:CC:95:00:01', 'sweep_dev_1', 'environment', ?, '70B3D57ED0009501',
                ?, ?, ?, ?, ?, 1)
        """,
        (SENTINEL_APP_KEY, CLIENT_ID, SITE_ID, BUILDING_ID, FLOOR_ID, ROOM_ID),
    )
    db_conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (chip_mac, device_id, node_type, app_key, dev_eui,
             client_id, site_id, building_id, floor_id, room_id, is_placed)
        VALUES ('AA:BB:CC:95:00:02', 'sweep_dev_2', 'environment', ?, '70B3D57ED0009502',
                ?, ?, ?, ?, NULL, 1)
        """,
        (SENTINEL_APP_KEY, CLIENT_ID, SITE_ID, BUILDING_ID, FLOOR_ID),
    )
    db_conn.execute(
        """
        INSERT OR REPLACE INTO gateways
            (id, gateway_id, name, client_id, site_id, building_id, floor_id)
        VALUES (?, 'sweep-gw-1', 'Sweep GW', ?, ?, ?, ?)
        """,
        (GATEWAY_DB_ID, CLIENT_ID, SITE_ID, BUILDING_ID, FLOOR_ID),
    )
    # An audit row whose details carry a secret, inserted directly so it
    # bypasses log_audit_event's write-time redaction.
    db_conn.execute(
        """
        INSERT INTO audit_log (actor, action, target_type, target_id, details)
        VALUES ('sweep', 'sweep_seed', 'device', 'sweep_dev_1', ?)
        """,
        (json.dumps({"app_key": SENTINEL_APP_KEY}),),
    )
    db_conn.commit()
    return db_conn


@pytest.fixture
def admin_client(client: TestClient, db_conn: sqlite3.Connection, seeded_secrets):
    client.cookies.set("sbms_session", create_login_session(db_conn, ADMIN_ID))
    return client


@pytest.mark.parametrize("path", get_routes())
def test_no_get_route_leaks_credentials(
    path: str, admin_client: TestClient, offline_upstreams
):
    """
    No endpoint may serve a LoRaWAN root key or stored password material.

    Several endpoints build responses with ``SELECT *``, so a credential
    column rides along unless it is explicitly stripped -- this already
    happened in three separate places in hierarchy.py alone. Sweeping every
    route means the next endpoint written that way fails here instead of
    shipping, and it covers routes added after this test was written.
    """
    response = admin_client.get(fill_path(path), follow_redirects=False)

    # 4xx/5xx bodies are error text, not data payloads.
    if response.status_code >= 400:
        return

    body = response.text
    for label, sentinel in (
        ("LoRaWAN app_key", SENTINEL_APP_KEY),
        ("password hash", SENTINEL_PASSWORD_HASH),
        ("password salt", SENTINEL_PASSWORD_SALT),
    ):
        assert sentinel not in body, (
            f"GET {path} leaked a {label} "
            f"(HTTP {response.status_code}, {len(body)} bytes)"
        )
