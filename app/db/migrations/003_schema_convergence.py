from __future__ import annotations

import sqlite3

"""
Converge the fresh-install schema with the schema that long-lived
deployments actually carry.

Migration 001 builds the relational core, but a number of columns and the
``floorplans`` table only ever existed on databases that grew through the
legacy bootstrap path. ``ensure_profile_alarm_runtime_schema`` and
``ensure_sensor_profile_schema`` in ``app/main.py`` were meant to add them at
startup, but nothing calls those functions, so a brand-new database ended up
missing columns that the routers query unconditionally -- most visibly
``users.last_login_at``, which made every successful login return a 500.

Everything here is additive and idempotent: existing deployments already have
most of these and simply skip them.
"""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    if not table_exists(conn, table):
        return
    name = definition.strip().split()[0].strip('"`[]')
    if name not in columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def up(conn: sqlite3.Connection) -> None:
    # --- users -------------------------------------------------------
    # Written on every successful login and read by GET /auth/status.
    add_column(conn, "users", "last_login_at TEXT")

    # --- floorplans --------------------------------------------------
    # Queried by GET /floorplans and GET /floors/{id}/live.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS floorplans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            building TEXT NOT NULL,
            floor TEXT NOT NULL,
            image_path TEXT NOT NULL,
            image_width INTEGER,
            image_height INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            floor_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_floorplans_floor ON floorplans(floor_id)"
    )

    # --- alarm_history -----------------------------------------------
    # Structured rule information written by the profile alarm engine.
    for definition in (
        "profile_id INTEGER",
        "profile_code TEXT",
        "profile_version INTEGER",
        "rule_id INTEGER",
        "rule_code TEXT",
        "severity TEXT",
        "field_key TEXT",
        "actual_value_json TEXT",
        "operator TEXT",
        "threshold_value REAL",
        "threshold_value_2 REAL",
        "expected_boolean INTEGER",
        "expected_text TEXT",
        "source TEXT DEFAULT 'legacy'",
        "auto_resolved INTEGER DEFAULT 0",
        "resolved_reason TEXT",
    ):
        add_column(conn, "alarm_history", definition)

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alarm_history_profile_rule
        ON alarm_history(device_id, profile_code, rule_code)
        """
    )

    # --- audit_log ---------------------------------------------------
    # Scope columns written by log_audit_event and filtered on by
    # GET /audit-log and the client-portal activity log.
    for definition in (
        "message TEXT",
        "client_id INTEGER",
        "site_id INTEGER",
        "building_id INTEGER",
        "floor_id INTEGER",
        "room_id INTEGER",
        "device_id TEXT",
        "gateway_id TEXT",
        "user_id INTEGER",
    ):
        add_column(conn, "audit_log", definition)

    # --- gateways ----------------------------------------------------
    # Placement metadata used by the gateway placement editor.
    for definition in (
        "building_id INTEGER",
        "floor_id INTEGER",
        "x INTEGER",
        "y INTEGER",
        "label TEXT",
        "location_note TEXT",
    ):
        add_column(conn, "gateways", definition)

    # --- devices -----------------------------------------------------
    # Legacy denormalised location strings and display/status metadata.
    # Databases created by 001 have the first group; older databases that
    # predate the relational refactor have the second.
    for definition in (
        "building TEXT",
        "floor TEXT",
        "room TEXT",
        "icon_type TEXT",
        "updated_at TEXT",
        "status TEXT",
        "last_seen TEXT",
    ):
        add_column(conn, "devices", definition)
