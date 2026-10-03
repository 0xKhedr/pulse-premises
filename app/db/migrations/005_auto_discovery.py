from __future__ import annotations

import json
import sqlite3

"""
Migration 005: LoRaWAN Device Auto-Discovery & Cayenne LPP Telemetry Support.

1. Adds 'is_placed' column to the 'devices' table (default 0 for unplaced nodes).
2. Sets 'is_placed = 1' for any existing devices that already have coordinates or room assignment.
3. Adds index on devices(is_placed).
4. Seeds the universal CAYENNE_LPP_V1 sensor profile with optional self-describing fields.
"""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


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
    # 1. Add is_placed column
    add_column(conn, "devices", "is_placed INTEGER NOT NULL DEFAULT 0")

    # 2. Update existing rows
    conn.execute("""
        UPDATE devices
        SET is_placed = 1
        WHERE (x IS NOT NULL AND y IS NOT NULL) OR (building_id IS NOT NULL AND room_id IS NOT NULL)
    """)
    conn.execute("""
        UPDATE devices
        SET is_placed = 0
        WHERE x IS NULL AND y IS NULL
    """)

    # 3. Create index on is_placed
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_devices_is_placed ON devices(is_placed)
    """)

    # 4. Seed CAYENNE_LPP_V1 profile if not already present
    existing_profile = conn.execute(
        "SELECT id FROM sensor_profiles WHERE profile_code = 'CAYENNE_LPP_V1'"
    ).fetchone()

    if not existing_profile:
        cursor = conn.execute(
            """
            INSERT INTO sensor_profiles (
                profile_code, profile_name, profile_version, node_type,
                description, capabilities_json, configuration_schema_json,
                payload_encoder_key, payload_version, f_port, uplink_interval_seconds,
                ttn_formatter_code, ttn_formatter_type, tb_device_profile_name,
                icon_type, icon_color, status, enabled, is_system, schema_checksum,
                created_by, updated_by
            ) VALUES (
                'CAYENNE_LPP_V1',
                'Universal Cayenne LPP',
                1,
                'multi',
                'Universal self-describing Cayenne LPP profile supporting any combination of environment, occupancy, energy, safety, and analog/digital telemetry.',
                ?,
                ?,
                'cayenne_lpp_v1',
                1,
                1,
                60,
                '',
                'none',
                'default',
                'multi',
                '#8b5cf6',
                'active',
                1,
                1,
                'cayenne_lpp_universal_v1_checksum',
                'system',
                'system'
            )
            """,
            (
                json.dumps(["environment", "occupancy", "safety", "energy"]),
                json.dumps({"protocol": "cayenne_lpp", "properties": {}}),
            ),
        )
        profile_id = cursor.lastrowid

        # Insert self-describing fields (all optional, nullable)
        fields = [
            ("temperature", "Temperature", "°C", "number", 1, 0, 1),
            ("humidity", "Humidity", "%", "number", 2, 0, 1),
            ("motion", "Motion", None, "boolean", 3, 0, 1),
            ("alarm", "Safety Alarm", None, "boolean", 4, 0, 1),
            ("voltage", "Voltage", "V", "number", 5, 0, 1),
            ("current", "Current", "A", "number", 6, 0, 1),
            ("power", "Power", "W", "number", 7, 0, 1),
            ("battery", "Battery", "%", "number", 8, 0, 1),
            ("co2", "CO2", "ppm", "number", 9, 0, 1),
            ("lux", "Luminosity", "lx", "number", 10, 0, 1),
            ("pressure", "Pressure", "hPa", "number", 11, 0, 1),
            ("analog_in", "Analog Input", None, "number", 12, 0, 1),
            ("digital_in", "Digital Input", None, "number", 13, 0, 1),
        ]

        for field_key, label, unit, data_type, order, req, null in fields:
            conn.execute(
                """
                INSERT INTO sensor_profile_fields (
                    profile_id, field_key, label, unit, data_type,
                    payload_order, required, nullable, display_order,
                    visible_floor, visible_dashboard
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                """,
                (profile_id, field_key, label, unit, data_type, order, req, null, order),
            )

        # Seed standard alarm rules
        rules = [
            ("CAYENNE_HIGH_TEMP", "temperature", ">", 30.0, None, "warning", "environment", "High temperature: {value} °C exceeds {threshold} °C"),
            ("CAYENNE_HIGH_HUMIDITY", "humidity", ">", 80.0, None, "warning", "environment", "High humidity: {value}% exceeds {threshold}%"),
            ("CAYENNE_MOTION_DETECTED", "motion", "==", None, 1, "warning", "occupancy", "Motion detected"),
            ("CAYENNE_SAFETY_ALARM", "alarm", "==", None, 1, "critical", "safety", "Safety alarm detected"),
            ("CAYENNE_HIGH_VOLTAGE", "voltage", ">", 260.0, None, "critical", "energy", "High voltage: {value} V exceeds {threshold} V"),
            ("CAYENNE_HIGH_CURRENT", "current", ">", 20.0, None, "warning", "energy", "High current: {value} A exceeds {threshold} A"),
            ("CAYENNE_HIGH_POWER", "power", ">", 5000.0, None, "warning", "energy", "High power usage: {value} W exceeds {threshold} W"),
        ]

        for rule_code, field_key, operator, thresh, bool_val, severity, alarm_type, msg in rules:
            conn.execute(
                """
                INSERT INTO sensor_profile_rules (
                    profile_id, rule_code, field_key, operator,
                    threshold_value, expected_boolean, severity,
                    alarm_type, message_template, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (profile_id, rule_code, field_key, operator, thresh, bool_val, severity, alarm_type, msg),
            )

    conn.commit()
