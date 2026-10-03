from __future__ import annotations

import sqlite3


def test_schema_tables_exist(db_conn: sqlite3.Connection):
    cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}

    expected_tables = {
        "users",
        "user_access",
        "auth_sessions",
        "clients",
        "sites",
        "buildings",
        "floors",
        "rooms",
        "devices",
        "sensor_catalog",
        "sensor_profiles",
        "firmware_modules",
        "schema_migrations",
    }

    missing = expected_tables - tables
    assert not missing, f"Missing expected tables in database schema: {missing}"


def test_devices_table_columns(db_conn: sqlite3.Connection):
    cursor = db_conn.execute("PRAGMA table_info(devices)")
    columns = {row["name"] for row in cursor.fetchall()}

    required_columns = {
        "chip_mac",
        "device_id",
        "dev_eui",
        "join_eui",
        "app_key",
        "node_type",
        "room_id",
        "client_id",
        "site_id",
        "building_id",
        "floor_id",
        "building",
        "floor",
        "room",
        "label",
        "x",
        "y",
        "icon_type",
        "profile_id",
        "profile_code",
        "profile_version",
        "payload_version",
        "firmware_version",
    }

    missing = required_columns - columns
    assert not missing, f"Missing expected columns in devices table: {missing}"
