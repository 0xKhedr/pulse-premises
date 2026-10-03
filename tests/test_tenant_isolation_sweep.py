from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app, create_login_session, create_password_hash

# Two tenants, each with its own client user, floor and device.
TENANT_A = {
    "user_id": 9601, "email": "tenant_a@test.local",
    "client_id": 9610, "site_id": 9611, "building_id": 9612,
    "floor_id": 9613, "room_id": 9614, "device_id": "tenant_a_dev",
    "chip_mac": "AA:BB:CC:96:0A:01",
    "access_id": 9615,
}
TENANT_B = {
    "user_id": 9701, "email": "tenant_b@test.local",
    "client_id": 9710, "site_id": 9711, "building_id": 9712,
    "floor_id": 9713, "room_id": 9714, "device_id": "tenant_b_dev",
    "chip_mac": "AA:BB:CC:96:0B:01",
    "access_id": 9715,
}

PASSWORD = "TenantSweepPass123!"


def _client_portal_routes() -> list[str]:
    """Every client-portal route that is scoped to a {user_id}."""
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or "GET" not in methods:
            continue
        if path.startswith("/client-portal/") and "{user_id}" in path:
            paths.add(path)
    return sorted(paths)


def _seed_tenant(conn: sqlite3.Connection, t: dict) -> None:
    pw = create_password_hash(PASSWORD)
    conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (?, ?, 'Tenant User', 'client', 1, ?, ?)
        """,
        (t["user_id"], t["email"], pw["password_hash"], pw["password_salt"]),
    )
    conn.execute(
        "INSERT OR REPLACE INTO clients (id, name) VALUES (?, ?)",
        (t["client_id"], f"Tenant {t['client_id']}"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, client_id, name) VALUES (?, ?, 'Site')",
        (t["site_id"], t["client_id"]),
    )
    conn.execute(
        "INSERT OR REPLACE INTO buildings (id, site_id, name) VALUES (?, ?, 'Bldg')",
        (t["building_id"], t["site_id"]),
    )
    conn.execute(
        "INSERT OR REPLACE INTO floors (id, building_id, name, floor_number)"
        " VALUES (?, ?, 'Floor', 1)",
        (t["floor_id"], t["building_id"]),
    )
    conn.execute(
        "INSERT OR REPLACE INTO rooms (id, floor_id, room_name, polygon_points, x, y)"
        " VALUES (?, ?, 'Room', '[]', 0, 0)",
        (t["room_id"], t["floor_id"]),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (chip_mac, device_id, node_type, client_id, site_id,
             building_id, floor_id, room_id, is_placed)
        VALUES (?, ?, 'environment', ?, ?, ?, ?, ?, 1)
        """,
        (
            t["chip_mac"], t["device_id"],
            t["client_id"], t["site_id"], t["building_id"],
            t["floor_id"], t["room_id"],
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO user_access
            (id, user_id, client_id, site_id, building_id, floor_id, access_level)
        VALUES (?, ?, ?, ?, ?, ?, 'view')
        """,
        (
            t["access_id"], t["user_id"], t["client_id"], t["site_id"],
            t["building_id"], t["floor_id"],
        ),
    )
    conn.commit()


@pytest.fixture
def two_tenants(db_conn: sqlite3.Connection):
    _seed_tenant(db_conn, TENANT_A)
    _seed_tenant(db_conn, TENANT_B)
    return db_conn


@pytest.fixture
def tenant_a_client(client: TestClient, two_tenants, db_conn: sqlite3.Connection):
    client.cookies.set(
        "sbms_session", create_login_session(db_conn, TENANT_A["user_id"])
    )
    return client


def _fill_for_tenant(path: str, t: dict) -> str:
    """Point every path parameter at the given tenant's own records."""
    return (
        path.replace("{user_id}", str(t["user_id"]))
        .replace("{floor_id}", str(t["floor_id"]))
        .replace("{device_id}", t["device_id"])
        .replace("{room_id}", str(t["room_id"]))
        .replace("{building_id}", str(t["building_id"]))
        .replace("{site_id}", str(t["site_id"]))
        .replace("{client_id}", str(t["client_id"]))
    )


@pytest.mark.parametrize("path", _client_portal_routes())
def test_client_cannot_read_another_tenant_via_user_id(
    path: str, tenant_a_client: TestClient
):
    """
    Tenant A, authenticated, must not read Tenant B's portal by swapping the
    {user_id} in the URL. This is the classic IDOR, swept across every
    client-portal route rather than spot-checked on one.
    """
    foreign = _fill_for_tenant(path, TENANT_B)
    response = tenant_a_client.get(foreign, follow_redirects=False)

    assert response.status_code in (302, 403, 404), (
        f"GET {path} let tenant A reach tenant B's data "
        f"(HTTP {response.status_code}): {response.text[:200]}"
    )


@pytest.mark.parametrize("path", _client_portal_routes())
def test_client_can_read_its_own_portal(path: str, tenant_a_client: TestClient):
    """
    The isolation check above is only meaningful if the same routes actually
    work for their rightful owner -- otherwise a blanket 403 would pass it.
    """
    own = _fill_for_tenant(path, TENANT_A)
    response = tenant_a_client.get(own, follow_redirects=False)

    assert response.status_code < 400, (
        f"GET {path} denied tenant A access to its own data "
        f"(HTTP {response.status_code}): {response.text[:200]}"
    )


def test_other_tenants_identifiers_never_appear_in_own_payloads(
    tenant_a_client: TestClient,
):
    """
    Even on its own endpoints, tenant A's payloads must not carry tenant B's
    records -- a scoping bug that returns everything would still pass a
    status-code-only check.
    """
    leaked = []
    for path in _client_portal_routes():
        response = tenant_a_client.get(_fill_for_tenant(path, TENANT_A))
        if response.status_code >= 400:
            continue
        body = response.text
        if TENANT_B["device_id"] in body or re.search(
            rf'\b{TENANT_B["client_id"]}\b', body
        ):
            leaked.append(path)

    assert not leaked, f"tenant B's records appeared in tenant A's payloads: {leaked}"
