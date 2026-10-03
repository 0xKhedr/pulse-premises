from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.services.websockets import ConnectionManager


def _seed_user(db_conn: sqlite3.Connection, user_id: int, email: str, role: str) -> str:
    from app.main import create_password_hash

    password = "WsTestPass123!"
    pw = create_password_hash(password)
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (user_id, email, f"WS {role}", role, pw["password_hash"], pw["password_salt"]),
    )
    db_conn.commit()
    return password


def test_websocket_rejects_anonymous(client: TestClient):
    """
    /ws/alarms streams live alarm and telemetry data. The HTTP auth
    middleware does not run for websocket scopes, so an unauthenticated
    handshake must be closed by the endpoint itself.
    """
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/alarms") as ws:
        ws.receive_text()


def test_websocket_accepts_admin(client: TestClient, db_conn: sqlite3.Connection):
    password = _seed_user(db_conn, 9101, "ws_admin@test.local", "admin")
    res = client.post(
        "/auth/login", json={"email": "ws_admin@test.local", "password": password}
    )
    assert res.status_code == 200, res.text

    with client.websocket_connect("/ws/alarms") as ws:
        ws.send_text('{"type": "subscribe"}')  # must not raise


class _FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


@pytest.mark.anyio
async def test_broadcast_is_fail_closed_without_scope():
    """
    A connection whose scope was never established must receive nothing.
    The previous filter fell through and delivered every tenant's events to
    any socket that had not sent a subscribe message.
    """
    manager = ConnectionManager()
    ws = _FakeSocket()
    # Simulate a connection that bypassed scope resolution entirely.
    manager.active_connections.append({
        "ws": ws, "role": "", "user_id": None,
        "allowed_client_ids": set(), "client_id": None, "site_id": None,
    })

    await manager.broadcast({"type": "ALARM"}, client_id=1, site_id=1)
    assert ws.sent == []


@pytest.mark.anyio
async def test_broadcast_isolates_tenants():
    manager = ConnectionManager()
    tenant_a, tenant_b, admin = _FakeSocket(), _FakeSocket(), _FakeSocket()

    manager.active_connections.extend([
        {"ws": tenant_a, "role": "client", "user_id": 1,
         "allowed_client_ids": {1}, "client_id": None, "site_id": None},
        {"ws": tenant_b, "role": "client", "user_id": 2,
         "allowed_client_ids": {2}, "client_id": None, "site_id": None},
        {"ws": admin, "role": "admin", "user_id": 3,
         "allowed_client_ids": set(), "client_id": None, "site_id": None},
    ])

    await manager.broadcast({"type": "ALARM"}, client_id=1, site_id=10)

    assert len(tenant_a.sent) == 1, "client 1 should receive its own tenant's alarm"
    assert tenant_b.sent == [], "client 2 must not see client 1's alarm"
    assert len(admin.sent) == 1, "admins see all tenants"


@pytest.mark.anyio
async def test_client_cannot_widen_its_own_scope():
    manager = ConnectionManager()
    ws = _FakeSocket()
    manager.active_connections.append({
        "ws": ws, "role": "client", "user_id": 1,
        "allowed_client_ids": {1}, "client_id": None, "site_id": None,
    })

    # Claim a tenant the session does not grant.
    manager.set_scope(ws, client_id=2)
    assert manager.active_connections[0]["client_id"] is None

    await manager.broadcast({"type": "ALARM"}, client_id=2)
    assert ws.sent == []


@pytest.mark.anyio
async def test_floor_grant_does_not_receive_other_floor_alarm():
    manager = ConnectionManager()
    ws = _FakeSocket()
    manager.active_connections.append({
        "ws": ws,
        "role": "client",
        "user_id": 1,
        "allowed_client_ids": {1},
        "allowed_scopes": [{
            "client_id": 1,
            "site_id": 10,
            "building_id": 20,
            "floor_id": 30,
        }],
        "client_id": None,
        "site_id": None,
    })

    await manager.broadcast(
        {"type": "ALARM"},
        client_id=1,
        site_id=10,
        building_id=20,
        floor_id=31,
    )
    assert ws.sent == []

    await manager.broadcast(
        {"type": "ALARM"},
        client_id=1,
        site_id=10,
        building_id=20,
        floor_id=30,
    )
    assert len(ws.sent) == 1
