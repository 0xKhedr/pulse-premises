from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_login_session, create_password_hash

ADMIN_ID = 9801


@pytest.fixture
def admin_client(client: TestClient, db_conn: sqlite3.Connection):
    pw = create_password_hash("LifecycleAdminPass123!")
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (?, 'lifecycle_admin@test.local', 'Lifecycle Admin', 'admin', 1, ?, ?)
        """,
        (ADMIN_ID, pw["password_hash"], pw["password_salt"]),
    )
    db_conn.commit()
    client.cookies.set("sbms_session", create_login_session(db_conn, ADMIN_ID))
    return client


def _orphans(conn: sqlite3.Connection) -> dict[str, int]:
    """Rows whose parent no longer exists, across the whole hierarchy."""
    checks = {
        "sites_without_client":
            "SELECT COUNT(*) FROM sites s"
            " WHERE s.client_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM clients c WHERE c.id = s.client_id)",
        "buildings_without_site":
            "SELECT COUNT(*) FROM buildings b"
            " WHERE b.site_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM sites s WHERE s.id = b.site_id)",
        "floors_without_building":
            "SELECT COUNT(*) FROM floors f"
            " WHERE f.building_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM buildings b WHERE b.id = f.building_id)",
        "rooms_without_floor":
            "SELECT COUNT(*) FROM rooms r"
            " WHERE r.floor_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = r.floor_id)",
        "devices_with_dangling_room":
            "SELECT COUNT(*) FROM devices d"
            " WHERE d.room_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM rooms r WHERE r.id = d.room_id)",
        "devices_with_dangling_floor":
            "SELECT COUNT(*) FROM devices d"
            " WHERE d.floor_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = d.floor_id)",
        "devices_with_dangling_client":
            "SELECT COUNT(*) FROM devices d"
            " WHERE d.client_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM clients c WHERE c.id = d.client_id)",
        "gateways_with_dangling_client":
            "SELECT COUNT(*) FROM gateways g"
            " WHERE g.client_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM clients c WHERE c.id = g.client_id)",
        "user_access_with_dangling_client":
            "SELECT COUNT(*) FROM user_access ua"
            " WHERE ua.client_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM clients c WHERE c.id = ua.client_id)",
    }
    return {
        name: conn.execute(sql).fetchone()[0]
        for name, sql in checks.items()
    }


def test_full_hierarchy_lifecycle_leaves_no_orphans(
    admin_client: TestClient, db_conn: sqlite3.Connection, monkeypatch
):
    """
    Build a complete tenant through the HTTP API, attach a discovered device,
    a gateway and a user grant, then delete the tenant and prove nothing is
    left dangling.

    Cascading deletes touch roughly ten tables in one un-guarded sequence.
    Nothing previously asserted that the cascade is *correct* -- only that it
    returns 200 -- so a half-finished cascade would leave devices and access
    grants pointing at a client that no longer exists, and no test would
    notice.
    """
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "lifecycle-secret")
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)

    orphans_before = _orphans(db_conn)

    # --- build the tenant through the public API -----------------------
    client_id = admin_client.post(
        "/clients", json={"name": "Lifecycle Client"}
    ).json()["client_id"]
    site_id = admin_client.post(
        "/sites", json={"client_id": client_id, "name": "Lifecycle Site"}
    ).json()["site_id"]
    building_id = admin_client.post(
        "/buildings", json={"site_id": site_id, "name": "Lifecycle Building"}
    ).json()["building_id"]
    floor_id = admin_client.post(
        "/floors", json={"building_id": building_id, "name": "Lifecycle Floor"}
    ).json()["floor_id"]
    room_response = admin_client.post(
        f"/floors/{floor_id}/rooms",
        json={
            "room_name": "Lifecycle Room",
            "polygon_points": [{"x": 0, "y": 0}, {"x": 8, "y": 0}, {"x": 8, "y": 8}],
        },
    )
    assert room_response.status_code == 200, room_response.text

    gateway_response = admin_client.post(
        "/gateways",
        json={
            "gateway_id": "lifecycle-gw",
            "name": "Lifecycle GW",
            "client_id": client_id,
            "site_id": site_id,
        },
    )
    assert gateway_response.status_code == 200, gateway_response.text

    # --- a real device arrives by uplink, then gets commissioned -------
    device_id = "lifecycle-device"
    uplink = admin_client.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "lifecycle-secret"},
        json={
            "end_device_ids": {
                "device_id": device_id,
                "dev_eui": "70B3D57ED0009801",
                "application_ids": {"application_id": "smart-building-lora-2"},
            },
            "uplink_message": {"decoded_payload": {"temperature_1": 21.5}},
        },
    )
    assert uplink.status_code == 200, uplink.text

    placed = admin_client.put(
        f"/devices/{device_id}/position",
        json={"floor_id": floor_id, "x": 10, "y": 20},
    )
    assert placed.status_code == 200, placed.text

    # A user granted access to this tenant.
    db_conn.execute(
        """
        INSERT OR REPLACE INTO user_access
            (id, user_id, client_id, site_id, access_level)
        VALUES (9802, ?, ?, ?, 'view')
        """,
        (ADMIN_ID, client_id, site_id),
    )
    db_conn.commit()

    # --- tear the whole tenant down ------------------------------------
    deleted = admin_client.delete(f"/clients/{client_id}")
    assert deleted.status_code == 200, deleted.text

    # --- the tenant and everything under it must be gone ---------------
    assert db_conn.execute(
        "SELECT COUNT(*) FROM clients WHERE id = ?", (client_id,)
    ).fetchone()[0] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) FROM sites WHERE client_id = ?", (client_id,)
    ).fetchone()[0] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) FROM floors WHERE building_id = ?", (building_id,)
    ).fetchone()[0] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) FROM user_access WHERE client_id = ?", (client_id,)
    ).fetchone()[0] == 0

    # --- and nothing anywhere may be left pointing at it ---------------
    orphans_after = _orphans(db_conn)
    introduced = {
        name: (orphans_before[name], count)
        for name, count in orphans_after.items()
        if count > orphans_before[name]
    }
    assert not introduced, (
        f"cascade delete left dangling rows (before -> after): {introduced}"
    )


def test_database_integrity_holds_after_lifecycle(db_conn: sqlite3.Connection):
    """SQLite's own structural and referential checks must stay clean."""
    assert db_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    violations = db_conn.execute("PRAGMA foreign_key_check").fetchall()
    assert not violations, f"foreign key violations: {violations[:10]}"


@pytest.mark.parametrize("level", ["floor", "building", "site"])
def test_deleting_a_level_holding_a_placed_device_succeeds(
    level: str, admin_client: TestClient, db_conn: sqlite3.Connection, monkeypatch
):
    """
    Deleting any hierarchy level that still holds a commissioned device must
    succeed.

    Migration 006 added a trigger aborting any update that leaves
    ``is_placed = 1`` with a NULL ``floor_id``. Every cascade clears the
    device's location, so unless it clears the flag in the same statement the
    trigger aborts and the whole delete fails with a 500 -- meaning a tenant
    with even one placed device could not be torn down at all.
    """
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "cascade-secret")
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)

    suffix = {"floor": "f", "building": "b", "site": "s"}[level]
    client_id = admin_client.post(
        "/clients", json={"name": f"Cascade {suffix}"}
    ).json()["client_id"]
    site_id = admin_client.post(
        "/sites", json={"client_id": client_id, "name": "S"}
    ).json()["site_id"]
    building_id = admin_client.post(
        "/buildings", json={"site_id": site_id, "name": "B"}
    ).json()["building_id"]
    floor_id = admin_client.post(
        "/floors", json={"building_id": building_id, "name": "F"}
    ).json()["floor_id"]

    device_id = f"cascade-device-{suffix}"
    admin_client.post(
        "/ttn-webhook",
        headers={"X-Webhook-Secret": "cascade-secret"},
        json={
            "end_device_ids": {
                "device_id": device_id,
                "dev_eui": f"70B3D57ED00098{ord(suffix):02X}",
                "application_ids": {"application_id": "smart-building-lora-2"},
            },
            "uplink_message": {"decoded_payload": {"temperature_1": 20.0}},
        },
    )
    placed = admin_client.put(
        f"/devices/{device_id}/position",
        json={"floor_id": floor_id, "x": 5, "y": 5},
    )
    assert placed.status_code == 200, placed.text
    assert db_conn.execute(
        "SELECT is_placed FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()["is_placed"] == 1

    target = {
        "floor": f"/floors/{floor_id}",
        "building": f"/buildings/{building_id}",
        "site": f"/sites/{site_id}",
    }[level]
    response = admin_client.delete(target)
    assert response.status_code == 200, (
        f"DELETE {target} failed with a placed device attached: {response.text[:300]}"
    )

    # The device survives as unplaced inventory rather than being destroyed.
    row = db_conn.execute(
        "SELECT is_placed, floor_id FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    assert row is not None, "device row should be retained as unplaced inventory"
    assert row["is_placed"] == 0
    assert row["floor_id"] is None
