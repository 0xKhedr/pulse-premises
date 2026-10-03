"""
Shared route enumeration for the whole-application invariant sweeps.

Deliberately not named ``test_*`` so pytest does not collect it. The sweeps
that import this derive their cases from ``app.routes`` at collection time, so
a route added later is covered automatically rather than needing someone to
remember to write a test for it.
"""

from __future__ import annotations

import re

from app.main import app

MUTATING_VERBS = ("POST", "PUT", "DELETE", "PATCH")

# Concrete values substituted into path parameters. Kept in one place so the
# sweeps all address the same seeded fixtures.
PATH_PARAM_VALUES = {
    "device_id": "sweep_dev_1",
    "user_id": "9501",
    "client_id": "9510",
    "site_id": "9511",
    "building_id": "9512",
    "floor_id": "9513",
    "room_id": "9514",
    "gateway_id": "sweep-gw-1",
    "gateway_db_id": "9515",
    "profile_id": "9516",
    "profile_code": "SWEEP_V1",
    "sensor_id": "9517",
    "module_id": "9518",
    "alarm_id": "9519",
    "access_id": "9520",
    "recipient_id": "9521",
}

# Mirrors auth_middleware's allowlist in app/main.py. If that list changes,
# these sweeps should be updated in the same commit -- which is the point:
# widening public access becomes a visible, reviewable diff in the tests.
PUBLIC_EXACT = {
    "/", "/login", "/admin-login", "/client-login", "/auth/login",
    "/auth/status", "/logout", "/me", "/docs", "/redoc", "/openapi.json",
    "/favicon.ico", "/service-worker.js", "/manifest.json", "/healthz",
    "/docs/oauth2-redirect",
}

PUBLIC_PREFIXES = ("/uploads", "/ttn-webhook", "/static")

# Documentation/schema endpoints are not application surface.
SKIP_PATHS = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}


def fill_path(path: str) -> str:
    """Substitute concrete ids for ``{param}`` placeholders."""
    return re.sub(
        r"\{([^}]+)\}",
        lambda m: PATH_PARAM_VALUES.get(m.group(1).split(":")[0], "1"),
        path,
    )


def is_public(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in PUBLIC_PREFIXES
    )


def get_routes() -> list[str]:
    """Every GET path the application serves."""
    paths = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path or "GET" not in methods or path in SKIP_PATHS:
            continue
        paths.add(path)
    return sorted(paths)


def mutating_routes() -> list[tuple[str, str]]:
    """Every (verb, path) pair that can change server state."""
    pairs = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path or path in SKIP_PATHS:
            continue
        for verb in MUTATING_VERBS:
            if verb in methods:
                pairs.add((verb, path))
    return sorted(pairs)


# Tables whose row counts must not change as a result of an unauthenticated
# request. Excludes auth_sessions/audit_log, which legitimately record
# rejected access attempts.
STATE_TABLES = (
    "clients", "sites", "buildings", "floors", "rooms", "devices",
    "gateways", "users", "user_access", "sensor_profiles", "sensor_catalog",
    "firmware_modules", "floorplans", "alarm_history",
)


def snapshot_state(conn) -> dict[str, int]:
    counts = {}
    for table in STATE_TABLES:
        try:
            counts[table] = conn.execute(
                # Table names come from the fixed STATE_TABLES tuple above,
                # never from user input.
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        except Exception:
            # Table may not exist on older schemas; absence is not a failure.
            continue
    return counts
