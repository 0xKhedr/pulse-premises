from __future__ import annotations

import sqlite3

"""
Create the query indexes as tracked schema.

Every index the running system relies on was created only by
``ensure_sensor_profile_schema`` / ``ensure_profile_alarm_runtime_schema`` in
``app/main.py`` -- functions nothing ever calls. Long-lived databases picked
them up from an older bootstrap path, but a database built from the migration
chain had none at all, so every hierarchy join and telemetry lookup on a fresh
deployment was a full table scan.

The second block adds foreign-key indexes that never existed anywhere. SQLite
does not index foreign keys automatically, and these columns are joined and
filtered on constantly by the hierarchy, client-portal and provisioning paths.
"""

# (index name, table, columns) -- created only when the table exists, so this
# stays safe on databases that predate some of these features.
INDEXES = [
    # Previously created only by the unreachable bootstrap functions.
    ("idx_alarm_history_profile_rule", "alarm_history", "device_id, profile_code, rule_code"),
    ("idx_device_configuration_history_device", "device_configuration_history", "device_id, requested_at"),
    ("idx_device_profile_code", "devices", "profile_code"),
    ("idx_device_profile_id", "devices", "profile_id"),
    ("idx_historical_telemetry_device_id", "historical_telemetry", "device_id"),
    ("idx_historical_telemetry_timestamp", "historical_telemetry", "timestamp"),
    ("idx_profile_alarm_runtime_active", "profile_alarm_runtime_state", "device_id, is_condition_active"),
    ("idx_profile_alarm_runtime_device", "profile_alarm_runtime_state", "device_id"),
    ("idx_profile_sensors_profile", "sensor_profile_sensors", "profile_id, display_order"),
    ("idx_profile_test_runs_profile", "sensor_profile_test_runs", "profile_id, created_at"),
    ("idx_sensor_profile_fields_profile", "sensor_profile_fields", "profile_id, display_order"),
    ("idx_sensor_profile_rules_profile", "sensor_profile_rules", "profile_id, enabled"),
    ("idx_sensor_profiles_enabled", "sensor_profiles", "enabled, status"),

    # Foreign-key columns joined on by nearly every hierarchy query.
    ("idx_sites_client", "sites", "client_id"),
    ("idx_buildings_site", "buildings", "site_id"),
    ("idx_floors_building", "floors", "building_id"),
    ("idx_rooms_floor", "rooms", "floor_id"),
    ("idx_devices_room", "devices", "room_id"),
    ("idx_devices_floor", "devices", "floor_id"),
    ("idx_devices_building", "devices", "building_id"),
    ("idx_devices_site", "devices", "site_id"),
    ("idx_devices_client", "devices", "client_id"),
    ("idx_gateways_site", "gateways", "site_id"),
    ("idx_gateways_client", "gateways", "client_id"),
    ("idx_user_access_user", "user_access", "user_id"),
    ("idx_auth_sessions_token", "auth_sessions", "token_hash"),
    ("idx_auth_sessions_user", "auth_sessions", "user_id"),
    ("idx_alarm_history_device", "alarm_history", "device_id"),
    ("idx_audit_log_created", "audit_log", "created_at"),
    ("idx_device_latest_telemetry_device", "device_latest_telemetry", "device_id"),
]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def up(conn: sqlite3.Connection) -> None:
    for name, table, cols in INDEXES:
        if not table_exists(conn, table):
            continue

        present = columns(conn, table)
        wanted = [c.strip() for c in cols.split(",")]
        if not all(c in present for c in wanted):
            # Column set differs on this database; skip rather than fail the
            # whole migration.
            continue

        conn.execute(
            f'CREATE INDEX IF NOT EXISTS {name} ON "{table}"({cols})'
        )
