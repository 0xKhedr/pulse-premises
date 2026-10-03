from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Routes whose handlers reach out to TTN / ThingsBoard. The network layer is
# stubbed out below so these still get exercised, just without real traffic.
PATH_PARAM_VALUES = {
    "device_id": "smoke_dev_1",
    "user_id": "1",
    "client_id": "1",
    "site_id": "1",
    "building_id": "1",
    "floor_id": "1",
    "room_id": "1",
    "gateway_id": "smoke-gw-1",
    "gateway_db_id": "1",
    "profile_id": "1",
    "profile_code": "SMOKE_V1",
    "sensor_id": "1",
    "module_id": "1",
    "alarm_id": "1",
    "access_id": "1",
    "recipient_id": "1",
}

# Query params that are genuinely required; a 422 for these is correct
# behaviour, not a failure.
ROUTES_REQUIRING_QUERY_PARAMS = {
    "/floor-map-data",
    "/api/firmware/generate-sensor-template",
}


def _fill_path(path: str) -> str:
    return re.sub(
        r"\{([^}]+)\}",
        lambda m: PATH_PARAM_VALUES.get(m.group(1).split(":")[0], "1"),
        path,
    )


@pytest.fixture
def seeded_hierarchy(db_conn: sqlite3.Connection):
    db_conn.execute("INSERT OR REPLACE INTO clients (id, name) VALUES (1, 'Smoke Client')")
    db_conn.execute("INSERT OR REPLACE INTO sites (id, client_id, name) VALUES (1, 1, 'Smoke Site')")
    db_conn.execute("INSERT OR REPLACE INTO buildings (id, site_id, name) VALUES (1, 1, 'Smoke Building')")
    db_conn.execute(
        "INSERT OR REPLACE INTO floors (id, building_id, name, floor_number)"
        " VALUES (1, 1, 'Smoke Floor', 1)"
    )
    db_conn.execute(
        "INSERT OR REPLACE INTO rooms (id, floor_id, room_name, polygon_points, x, y)"
        " VALUES (1, 1, 'Smoke Room', '[]', 0, 0)"
    )
    db_conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (chip_mac, device_id, node_type, client_id, site_id, building_id, floor_id, room_id)
        VALUES ('AA:BB:CC:DD:EE:01', 'smoke_dev_1', 'environment', 1, 1, 1, 1, 1)
        """
    )
    db_conn.commit()
    yield db_conn


@pytest.fixture
def admin_client(client: TestClient, db_conn: sqlite3.Connection):
    """
    A TestClient carrying a real admin session cookie.

    Most routes are admin-only, so an anonymous client would only ever prove
    that the login redirect fires -- it would never reach the handlers, which
    is where the bugs actually live.
    """
    from app.main import create_password_hash

    password = "SmokeAdminPass123!"
    pw = create_password_hash(password)
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (9001, 'smoke_admin@test.local', 'Smoke Admin', 'admin', 1, ?, ?)
        """,
        (pw["password_hash"], pw["password_salt"]),
    )
    db_conn.commit()

    response = client.post(
        "/auth/login",
        json={"email": "smoke_admin@test.local", "password": password},
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def offline_upstreams(monkeypatch):
    """Make TTN/ThingsBoard behave as if unreachable, with no real network."""

    def boom(*args, **kwargs):
        raise ConnectionError("upstream unavailable (stubbed in tests)")

    import app.main as main_module

    for name in (
        "read_tb_latest_telemetry",
        "ttn_get_gateway_status",
        "ttn_get_gateway_connection_stats",
    ):
        if hasattr(main_module, name):
            monkeypatch.setattr(main_module, name, boom)

    for module_name in ("app.routers.devices", "app.routers.gateways", "app.routers.admin"):
        module = __import__(module_name, fromlist=["*"])
        for name in (
            "read_tb_latest_telemetry",
            "ttn_get_gateway_status",
            "ttn_get_gateway_connection_stats",
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, boom)


def _get_routes():
    routes = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path or "GET" not in methods:
            continue
        if path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
            continue
        routes.append(path)
    return sorted(routes)


@pytest.mark.parametrize("path", _get_routes())
def test_get_route_does_not_500(
    path: str, admin_client: TestClient, seeded_hierarchy, offline_upstreams
):
    """
    Every GET route must return a real HTTP response rather than raising.

    An unhandled exception here means the route is dead in production: this
    is the check that would have caught the missing-import breakage and the
    legacy-column queries (``rooms.building``, ``rooms.floorplan_id``) that
    made the hierarchy and provisioning APIs unusable.
    """
    response = admin_client.get(_fill_path(path), follow_redirects=False)

    if path in ROUTES_REQUIRING_QUERY_PARAMS:
        assert response.status_code == 422
        return

    # An authenticated admin should never be bounced back to a login page.
    # /logout is the one route whose whole job is to redirect.
    if path != "/logout":
        assert response.status_code != 302, (
            f"GET {path} redirected an authenticated admin to "
            f"{response.headers.get('location')}"
        )
    assert response.status_code < 500, (
        f"GET {path} returned {response.status_code}: {response.text[:400]}"
    )


ANONYMOUS_ALLOWED = {
    "/", "/login", "/admin-login", "/client-login", "/auth/status",
    "/logout", "/me", "/service-worker.js", "/healthz",
    "/docs/oauth2-redirect",
}


@pytest.mark.parametrize("path", _get_routes())
def test_sensitive_routes_reject_anonymous(path: str, client: TestClient, seeded_hierarchy):
    """
    Asset, device and tenant data must not be readable without a session.

    These endpoints were all anonymously reachable, so the entire building
    hierarchy and device inventory could be read or deleted with no login.
    """
    if path in ANONYMOUS_ALLOWED or path.startswith(("/uploads", "/static")):
        return

    response = client.get(_fill_path(path), follow_redirects=False)
    assert response.status_code in (302, 401, 403), (
        f"GET {path} served an anonymous caller with {response.status_code}"
    )
