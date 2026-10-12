"""Populate the local SQLite database with an idempotent preview dataset.

The direct-run scripts call this module before Uvicorn starts.  Only records
with stable ``preview-*`` identifiers (or the preview actor/email addresses)
are refreshed, so data created through the application is left alone.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db.connection import get_db_connection, run_migrations

PREVIEW_PASSWORD = "Preview123!"
PREVIEW_DEVICE_IDS = (
    "preview-env-lobby",
    "preview-occ-meeting",
    "preview-air-office",
    "preview-safety-server",
    "preview-energy-main",
    "preview-multi-roof",
    "preview-env-warehouse",
    "preview-occ-loading",
    "preview-energy-solar",
    "preview-camera-meter",
    "preview-spare-node",
)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _get_or_create_id(
    conn: sqlite3.Connection,
    table: str,
    where: dict[str, object],
    values: dict[str, object] | None = None,
) -> int:
    criteria = " AND ".join(f"{column} = ?" for column in where)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {criteria} LIMIT 1", tuple(where.values())
    ).fetchone()
    merged = {**where, **(values or {})}
    if row:
        assignments = ", ".join(f"{column} = ?" for column in merged)
        conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            (*merged.values(), row["id"]),
        )
        return int(row["id"])

    columns = ", ".join(merged)
    placeholders = ", ".join("?" for _ in merged)
    cursor = conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(merged.values()),
    )
    return int(cursor.lastrowid)


def _password_fields(password: str) -> tuple[str, str, int]:
    iterations = int(settings.PASSWORD_PBKDF2_ITERATIONS)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    ).hex()
    return salt, digest, iterations


def _profile(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM sensor_profiles WHERE profile_code = ?", (code,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Required system sensor profile is missing: {code}")
    return row


def _ensure_preview_profile_foundation(conn: sqlite3.Connection) -> None:
    """Supply the legacy demo catalog that older databases already contain."""
    catalog_specs = [
        ("SHT30_SHT31", "Sensirion", "SHT30 / SHT31", "environment", "i2c", "Environment temperature and humidity sensor"),
        ("GENERIC_OCCUPANCY_INPUT", "Generic", "Occupancy Input", "occupancy", "digital", "PIR or dry-contact occupancy input"),
        ("GENERIC_SAFETY_INPUT", "Generic", "Safety Input", "safety", "digital", "Leak, smoke, or safety contact input"),
        ("LEGACY_ENERGY_INPUT", "Generic", "Energy Meter Input", "energy", "virtual", "Three-value electrical meter input"),
        ("BME688", "Bosch Sensortec", "BME688", "environment", "i2c", "Temperature, humidity, pressure, and gas sensor"),
    ]
    module_specs = [
        ("sht31_i2c", "SHT30/SHT31 I2C Module", "Sht31Sensor", "i2c", "Adafruit SHT31 Library"),
        ("generic_occupancy_input", "Generic Occupancy Module", "DigitalInputSensor", "digital", None),
        ("generic_safety_input", "Generic Safety Module", "DigitalInputSensor", "digital", None),
        ("legacy_energy_input", "Legacy Energy Module", "EnergyMeterSensor", "virtual", None),
        ("bme688_i2c", "BME688 I2C Module", "Bme688Sensor", "i2c", "Adafruit BME680 Library"),
    ]
    for code, manufacturer, model, use_case, protocol, description in catalog_specs:
        conn.execute(
            """
            INSERT INTO sensor_catalog(sensor_code, manufacturer, model, use_case,
                protocol, description, enabled)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(sensor_code) DO UPDATE SET enabled=1
            """,
            (code, manufacturer, model, use_case, protocol, description),
        )
    for key, name, driver, protocol, library in module_specs:
        conn.execute(
            """
            INSERT INTO firmware_modules(module_key, display_name, driver_class,
                protocol, library_name, source_file, notes, enabled)
            VALUES (?, ?, ?, ?, ?, ?, 'Preview-ready firmware module', 1)
            ON CONFLICT(module_key) DO UPDATE SET enabled=1
            """,
            (key, name, driver, protocol, library, f"firmware/modules/{key}.h"),
        )

    profiles = [
        {
            "code": "ENV_SHT31_V1", "name": "SHT30/SHT31 Environment", "node": "environment",
            "description": "Indoor temperature and humidity monitoring", "capabilities": ["temperature", "humidity"],
            "icon": "env", "color": "#16a34a", "sensor": "SHT30_SHT31", "module": "sht31_i2c",
            "fields": [("temperature", "Temperature", "°C", "number"), ("humidity", "Humidity", "%", "number")],
            "rules": [("ENV_HIGH_TEMPERATURE", "temperature", ">", 30.0, None, "warning", "environment", "High temperature: {value} °C exceeds {threshold} °C"),
                      ("ENV_HIGH_HUMIDITY", "humidity", ">", 80.0, None, "warning", "environment", "High humidity: {value}% exceeds {threshold}%")],
        },
        {
            "code": "OCC_GENERIC_V1", "name": "Generic Occupancy", "node": "occupancy",
            "description": "Room occupancy and motion monitoring", "capabilities": ["motion", "occupancy"],
            "icon": "occupancy", "color": "#2563eb", "sensor": "GENERIC_OCCUPANCY_INPUT", "module": "generic_occupancy_input",
            "fields": [("motion", "Motion", None, "boolean"), ("occupancy", "Occupancy", "people", "integer")],
            "rules": [("OCC_MOTION_DETECTED", "motion", "==", None, 1, "warning", "occupancy", "Motion detected")],
        },
        {
            "code": "SAFETY_GENERIC_V1", "name": "Generic Safety", "node": "safety",
            "description": "Leak and safety contact monitoring", "capabilities": ["alarm", "leak_detected"],
            "icon": "alarm", "color": "#dc2626", "sensor": "GENERIC_SAFETY_INPUT", "module": "generic_safety_input",
            "fields": [("alarm", "Safety Alarm", None, "boolean"), ("leak_detected", "Leak Detected", None, "boolean")],
            "rules": [("SAFETY_ALARM_ACTIVE", "alarm", "==", None, 1, "critical", "safety", "Safety alarm detected")],
        },
        {
            "code": "ENERGY_LEGACY_V1", "name": "Legacy Energy", "node": "energy",
            "description": "Voltage, current, power, and accumulated energy", "capabilities": ["voltage", "current", "power", "energy"],
            "icon": "energy", "color": "#f59e0b", "sensor": "LEGACY_ENERGY_INPUT", "module": "legacy_energy_input",
            "fields": [("voltage", "Voltage", "V", "number"), ("current", "Current", "A", "number"),
                       ("power", "Power", "W", "number"), ("energy_kwh", "Energy", "kWh", "number")],
            "rules": [("ENERGY_HIGH_POWER", "power", ">", 5000.0, None, "warning", "energy", "High power usage: {value} W exceeds {threshold} W"),
                      ("ENERGY_HIGH_VOLTAGE", "voltage", ">", 260.0, None, "critical", "energy", "High voltage: {value} V exceeds {threshold} V"),
                      ("ENERGY_HIGH_CURRENT", "current", ">", 20.0, None, "warning", "energy", "High current: {value} A exceeds {threshold} A")],
        },
        {
            "code": "AIR_BME688_V1", "name": "BME688 Environment", "node": "environment",
            "description": "Indoor air quality and atmospheric monitoring", "capabilities": ["temperature", "humidity", "pressure", "gas_resistance"],
            "icon": "env", "color": "#3b82f6", "sensor": "BME688", "module": "bme688_i2c",
            "fields": [("temperature", "Temperature", "°C", "number"), ("humidity", "Humidity", "%", "number"),
                       ("pressure", "Atmospheric Pressure", "hPa", "number"), ("gas_resistance", "Gas Resistance", "kΩ", "number")],
            "rules": [("BME688_HIGH_TEMPERATURE", "temperature", ">", 40.0, None, "warning", "environment", "High temperature: {value} °C exceeds {threshold} °C")],
        },
    ]
    for spec in profiles:
        row = conn.execute(
            "SELECT id FROM sensor_profiles WHERE profile_code = ?", (spec["code"],)
        ).fetchone()
        if row:
            continue
        cursor = conn.execute(
            """
            INSERT INTO sensor_profiles(profile_code, profile_name, profile_version,
                node_type, description, capabilities_json, configuration_schema_json,
                payload_encoder_key, payload_version, f_port, uplink_interval_seconds,
                ttn_formatter_code, ttn_formatter_type, tb_device_profile_name,
                icon_type, icon_color, status, enabled, is_system, schema_checksum,
                created_by, updated_by)
            VALUES (?, ?, 1, ?, ?, ?, '{}', ?, 1, 1, 60, '', 'javascript', ?,
                ?, ?, 'active', 1, 1, ?, 'preview-seed', 'preview-seed')
            """,
            (spec["code"], spec["name"], spec["node"], spec["description"],
             _json(spec["capabilities"]), spec["code"].lower(), spec["name"],
             spec["icon"], spec["color"], f"preview-{spec['code'].lower()}"),
        )
        profile_id = cursor.lastrowid
        for order, (key, label, unit, data_type) in enumerate(spec["fields"], start=1):
            conn.execute(
                """
                INSERT INTO sensor_profile_fields(profile_id, field_key, label, unit,
                    data_type, payload_order, required, nullable, display_order,
                    visible_floor, visible_dashboard)
                VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, 1, 1)
                """,
                (profile_id, key, label, unit, data_type, order, order),
            )
        for code, key, operator, threshold, expected_bool, severity, alarm_type, message in spec["rules"]:
            conn.execute(
                """
                INSERT INTO sensor_profile_rules(profile_id, rule_code, field_key,
                    operator, threshold_value, expected_boolean, severity, alarm_type,
                    message_template, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (profile_id, code, key, operator, threshold, expected_bool,
                 severity, alarm_type, message),
            )
        sensor_id = conn.execute(
            "SELECT id FROM sensor_catalog WHERE sensor_code = ?", (spec["sensor"],)
        ).fetchone()["id"]
        module_id = conn.execute(
            "SELECT id FROM firmware_modules WHERE module_key = ?", (spec["module"],)
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO sensor_profile_sensors(profile_id, sensor_id,
                firmware_module_id, role, required, configuration_json, display_order)
            VALUES (?, ?, ?, 'primary', 1, '{}', 1)
            """,
            (profile_id, sensor_id, module_id),
        )
        conn.execute(
            """
            INSERT INTO profile_firmware_compatibility(profile_id,
                firmware_module_id, min_firmware_version, required_features_json)
            VALUES (?, ?, '1.0.0', '[]')
            """,
            (profile_id, module_id),
        )


def _telemetry_for_history(kind: str, index: int, base: dict) -> dict:
    wave = math.sin(index / 4.0)
    data = dict(base)
    if kind in {"environment", "multi"}:
        if "temperature" in data:
            data["temperature"] = round(float(data["temperature"]) + wave * 1.4, 1)
        if "humidity" in data:
            data["humidity"] = round(float(data["humidity"]) - wave * 3.2, 1)
    if kind == "energy":
        factor = 0.88 + (math.sin(index / 3.0) + 1) * 0.09
        data["current"] = round(float(data["current"]) * factor, 2)
        data["power"] = round(float(data["power"]) * factor, 1)
    if "pressure" in data:
        data["pressure"] = round(float(data["pressure"]) + wave * 0.8, 1)
    return data


def seed_preview_data(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[str, int]:
    """Create or refresh preview records using one caller-owned transaction."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    _ensure_preview_profile_foundation(conn)

    northstar_id = _get_or_create_id(
        conn,
        "clients",
        {"name": "Northstar Property Group"},
        {"tb_customer_id": "preview-northstar"},
    )
    helio_id = _get_or_create_id(
        conn,
        "clients",
        {"name": "Helio Retail Group"},
        {"tb_customer_id": "preview-helio"},
    )

    cairo_site_id = _get_or_create_id(
        conn,
        "sites",
        {"client_id": northstar_id, "name": "Cairo Innovation Campus"},
        {
            "campus_image_path": "/preview-assets/campus-map",
            "image_width": 1920,
            "image_height": 1286,
        },
    )
    alex_site_id = _get_or_create_id(
        conn,
        "sites",
        {"client_id": helio_id, "name": "Alexandria Logistics Hub"},
        {
            "campus_image_path": "/preview-assets/campus-map",
            "image_width": 1920,
            "image_height": 1286,
        },
    )
    for site_id in (cairo_site_id, alex_site_id):
        conn.execute("DELETE FROM site_maps WHERE site_id = ?", (site_id,))
        conn.execute(
            """
            INSERT INTO site_maps(site_id, image_path, image_width, image_height)
            VALUES (?, '/preview-assets/campus-map', 1920, 1286)
            """,
            (site_id,),
        )

    operations_id = _get_or_create_id(
        conn,
        "buildings",
        {"site_id": cairo_site_id, "name": "Operations Centre"},
        {
            "polygon_points": _json(
                [{"x": 175, "y": 470}, {"x": 520, "y": 470},
                 {"x": 510, "y": 760}, {"x": 180, "y": 755}]
            ),
            "x": 346,
            "y": 614,
        },
    )
    energy_lab_id = _get_or_create_id(
        conn,
        "buildings",
        {"site_id": cairo_site_id, "name": "Energy & Systems Lab"},
        {
            "polygon_points": _json(
                [{"x": 730, "y": 420}, {"x": 1040, "y": 425},
                 {"x": 1045, "y": 720}, {"x": 735, "y": 715}]
            ),
            "x": 887,
            "y": 570,
        },
    )
    warehouse_id = _get_or_create_id(
        conn,
        "buildings",
        {"site_id": alex_site_id, "name": "Distribution Warehouse"},
        {
            "polygon_points": _json(
                [{"x": 1110, "y": 760}, {"x": 1570, "y": 755},
                 {"x": 1585, "y": 1090}, {"x": 1100, "y": 1095}]
            ),
            "x": 1341,
            "y": 925,
        },
    )

    ground_id = _get_or_create_id(
        conn,
        "floors",
        {"building_id": operations_id, "name": "Ground Floor"},
        {"floor_number": "0", "image_path": "/preview-assets/floor-plan",
         "image_width": 904, "image_height": 608},
    )
    first_id = _get_or_create_id(
        conn,
        "floors",
        {"building_id": operations_id, "name": "First Floor"},
        {"floor_number": "1", "image_path": "/preview-assets/floor-plan",
         "image_width": 904, "image_height": 608},
    )
    lab_floor_id = _get_or_create_id(
        conn,
        "floors",
        {"building_id": energy_lab_id, "name": "Lab Level"},
        {"floor_number": "G", "image_path": "/preview-assets/floor-plan-alt",
         "image_width": 644, "image_height": 452},
    )
    warehouse_floor_id = _get_or_create_id(
        conn,
        "floors",
        {"building_id": warehouse_id, "name": "Warehouse Floor"},
        {"floor_number": "0", "image_path": "/preview-assets/floor-plan-alt",
         "image_width": 644, "image_height": 452},
    )

    room_specs = [
        (ground_id, "Reception Lobby", 70, 75, 265, 235),
        (ground_id, "Open Office", 295, 70, 600, 275),
        (ground_id, "Meeting Room Atlas", 625, 75, 835, 270),
        (first_id, "Executive Suite", 75, 75, 300, 260),
        (first_id, "Collaboration Zone", 325, 75, 610, 300),
        (first_id, "Server Room", 650, 90, 830, 280),
        (lab_floor_id, "Main Meter Room", 45, 50, 285, 215),
        (lab_floor_id, "Systems Workshop", 320, 50, 600, 280),
        (warehouse_floor_id, "Storage Hall", 40, 45, 390, 320),
        (warehouse_floor_id, "Loading Bay", 420, 55, 610, 320),
    ]
    rooms: dict[str, int] = {}
    for floor_id, name, left, top, right, bottom in room_specs:
        polygon = [
            {"x": left, "y": top}, {"x": right, "y": top},
            {"x": right, "y": bottom}, {"x": left, "y": bottom},
        ]
        rooms[name] = _get_or_create_id(
            conn,
            "rooms",
            {"floor_id": floor_id, "room_name": name},
            {"polygon_points": _json(polygon), "x": (left + right) // 2,
             "y": (top + bottom) // 2},
        )

    location = {
        "Reception Lobby": (northstar_id, cairo_site_id, operations_id, ground_id),
        "Open Office": (northstar_id, cairo_site_id, operations_id, ground_id),
        "Meeting Room Atlas": (northstar_id, cairo_site_id, operations_id, ground_id),
        "Executive Suite": (northstar_id, cairo_site_id, operations_id, first_id),
        "Collaboration Zone": (northstar_id, cairo_site_id, operations_id, first_id),
        "Server Room": (northstar_id, cairo_site_id, operations_id, first_id),
        "Main Meter Room": (northstar_id, cairo_site_id, energy_lab_id, lab_floor_id),
        "Systems Workshop": (northstar_id, cairo_site_id, energy_lab_id, lab_floor_id),
        "Storage Hall": (helio_id, alex_site_id, warehouse_id, warehouse_floor_id),
        "Loading Bay": (helio_id, alex_site_id, warehouse_id, warehouse_floor_id),
    }
    building_names = {operations_id: "Operations Centre", energy_lab_id: "Energy & Systems Lab",
                      warehouse_id: "Distribution Warehouse"}
    floor_names = {ground_id: "Ground Floor", first_id: "First Floor",
                   lab_floor_id: "Lab Level", warehouse_floor_id: "Warehouse Floor"}

    device_specs = [
        ("preview-env-lobby", "A10000000001", "ENV_SHT31_V1", "Lobby Climate", "Reception Lobby", 165, 155,
         {"temperature": 23.6, "humidity": 47.2, "battery": 91}),
        ("preview-occ-meeting", "A10000000002", "OCC_GENERIC_V1", "Atlas Occupancy", "Meeting Room Atlas", 730, 165,
         {"motion": True, "occupancy": 7, "battery": 84}),
        ("preview-air-office", "A10000000003", "AIR_BME688_V1", "Office Air Quality", "Open Office", 445, 170,
         {"temperature": 24.4, "humidity": 45.8, "pressure": 1012.6, "gas_resistance": 118.4}),
        ("preview-safety-server", "A10000000004", "SAFETY_GENERIC_V1", "Server Leak Detector", "Server Room", 735, 175,
         {"alarm": True, "leak_detected": True, "battery": 76}),
        ("preview-energy-main", "A10000000005", "ENERGY_LEGACY_V1", "Main Distribution Meter", "Main Meter Room", 160, 130,
         {"voltage": 231.4, "current": 26.5, "power": 6120.0, "energy_kwh": 18452.7}),
        ("preview-multi-roof", "A10000000006", "CAYENNE_LPP_V1", "Roof Multi Sensor", "Systems Workshop", 460, 150,
         {"temperature": 28.1, "humidity": 39.0, "motion": False, "alarm": False,
          "voltage": 3.72, "current": 0.08, "power": 0.3, "battery": 68,
          "co2": 612, "lux": 1480, "pressure": 1011.8}),
        ("preview-env-warehouse", "A10000000007", "ENV_SHT31_V1", "Warehouse Climate", "Storage Hall", 220, 180,
         {"temperature": 26.8, "humidity": 52.1, "battery": 73}),
        ("preview-occ-loading", "A10000000008", "OCC_GENERIC_V1", "Loading Bay Motion", "Loading Bay", 515, 175,
         {"motion": False, "occupancy": 0, "battery": 42}),
        ("preview-energy-solar", "A10000000009", "ENERGY_LEGACY_V1", "Solar Inverter Meter", "Systems Workshop", 515, 220,
         {"voltage": 229.8, "current": 12.3, "power": 2826.5, "energy_kwh": 9321.4}),
        ("preview-camera-meter", "A1000000000A", "ESPCAM_METER_V1", "Water Meter Camera", "Main Meter Room", 225, 165,
         {"cam_reading": 24871.6, "cam_reading_text": "024871.6", "cam_reading_ok": True,
          "cam_reading_rows": 1, "cam_locked": True, "cam_segmented": True,
          "cam_recognized": True, "cam_streaming": False, "cam_mask_preview": False}),
    ]

    profile_cache: dict[str, sqlite3.Row] = {}
    telemetry_by_device: dict[str, dict] = {}
    for index, (device_id, chip_mac, profile_code, label, room_name, x, y, telemetry) in enumerate(device_specs):
        profile = profile_cache.setdefault(profile_code, _profile(conn, profile_code))
        client_id, site_id, building_id, floor_id = location[room_name]
        age = timedelta(minutes=1 + index)
        status = "online"
        if device_id == "preview-occ-loading":
            age, status = timedelta(minutes=18), "stale"
        if device_id == "preview-env-warehouse":
            age, status = timedelta(hours=30), "offline"
        last_seen = _timestamp(now - age)
        conn.execute(
            """
            INSERT INTO devices(
                chip_mac, device_id, dev_eui, join_eui, app_key, node_type,
                room_id, label, status, x, y, last_seen, profile_id, profile_code,
                configuration_status, client_id, site_id, building_id, floor_id,
                profile_version, payload_version, firmware_version,
                configuration_updated_at, building, floor, room, icon_type,
                updated_at, is_placed
            ) VALUES (?, ?, ?, '0000000000000000', '', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'applied', ?, ?, ?, ?, ?, ?, 'preview-1.4.0', ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(chip_mac) DO UPDATE SET
                device_id=excluded.device_id, dev_eui=excluded.dev_eui,
                node_type=excluded.node_type, room_id=excluded.room_id,
                label=excluded.label, status=excluded.status, x=excluded.x, y=excluded.y,
                last_seen=excluded.last_seen, profile_id=excluded.profile_id,
                profile_code=excluded.profile_code, configuration_status='applied',
                client_id=excluded.client_id, site_id=excluded.site_id,
                building_id=excluded.building_id, floor_id=excluded.floor_id,
                profile_version=excluded.profile_version, payload_version=excluded.payload_version,
                firmware_version=excluded.firmware_version,
                configuration_updated_at=excluded.configuration_updated_at,
                building=excluded.building, floor=excluded.floor, room=excluded.room,
                icon_type=excluded.icon_type, updated_at=excluded.updated_at, is_placed=1
            """,
            (
                chip_mac, device_id, chip_mac, profile["node_type"], rooms[room_name],
                label, status, x, y, last_seen, profile["id"], profile_code,
                client_id, site_id, building_id, floor_id, profile["profile_version"],
                profile["payload_version"], last_seen, building_names[building_id],
                floor_names[floor_id], room_name, profile["icon_type"], last_seen,
            ),
        )
        alarm_active = device_id in {"preview-safety-server", "preview-energy-main", "preview-env-warehouse"}
        alarm_message = {
            "preview-safety-server": "Water detected near server cooling unit",
            "preview-energy-main": "High power usage: 6120 W exceeds 5000 W",
            "preview-env-warehouse": "OFFLINE",
        }.get(device_id, "OK")
        conn.execute(
            """
            INSERT INTO device_latest_telemetry(device_id, telemetry, alarm_active, alarm_message, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET telemetry=excluded.telemetry,
                alarm_active=excluded.alarm_active, alarm_message=excluded.alarm_message,
                updated_at=excluded.updated_at
            """,
            (device_id, _json(telemetry), int(alarm_active), alarm_message, last_seen),
        )
        telemetry_by_device[device_id] = telemetry

        capabilities = json.loads(profile["capabilities_json"] or "[]")
        if not capabilities:
            capabilities = [profile["node_type"]]
        conn.execute("DELETE FROM device_capabilities WHERE device_id = ?", (device_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO device_capabilities(device_id, capability) VALUES (?, ?)",
            [(device_id, capability) for capability in capabilities],
        )

    # An unplaced discovery record gives the placement inbox useful content.
    spare_profile = _profile(conn, "CAYENNE_LPP_V1")
    conn.execute(
        """
        INSERT INTO devices(chip_mac, device_id, dev_eui, join_eui, app_key,
            node_type, label, status, profile_id, profile_code, profile_version,
            payload_version, firmware_version, icon_type, updated_at, is_placed)
        VALUES ('A1000000000B', 'preview-spare-node', 'A10000000000000B',
            '0000000000000000', '', 'multi', 'Unassigned Demo Sensor', 'discovered',
            ?, 'CAYENNE_LPP_V1', ?, ?, 'preview-1.4.0', 'multi', ?, 0)
        ON CONFLICT(chip_mac) DO UPDATE SET label=excluded.label, status='discovered',
            client_id=NULL, site_id=NULL, building_id=NULL, floor_id=NULL,
            room_id=NULL, is_placed=0, updated_at=excluded.updated_at
        """,
        (spare_profile["id"], spare_profile["profile_version"],
         spare_profile["payload_version"], _timestamp(now - timedelta(minutes=4))),
    )

    placeholders = ",".join("?" for _ in PREVIEW_DEVICE_IDS)
    conn.execute(
        f"DELETE FROM historical_telemetry WHERE device_id IN ({placeholders})",
        PREVIEW_DEVICE_IDS,
    )
    for device_id, _, profile_code, _, _, _, _, base in device_specs:
        kind = _profile(conn, profile_code)["node_type"]
        for sample in range(48):
            at = now - timedelta(minutes=(47 - sample) * 30)
            telemetry = _telemetry_for_history(kind, sample, base)
            conn.execute(
                "INSERT INTO historical_telemetry(device_id, telemetry, timestamp) VALUES (?, ?, ?)",
                (device_id, _json(telemetry), _timestamp(at)),
            )

    gateway_specs = [
        ("preview-gw-cairo-roof", "Cairo Roof Gateway", northstar_id, cairo_site_id,
         operations_id, first_id, 445, 330, "Campus Roof Gateway", "online", timedelta(minutes=2)),
        ("preview-gw-lab", "Energy Lab Gateway", northstar_id, cairo_site_id,
         energy_lab_id, lab_floor_id, 360, 335, "Lab LoRaWAN Gateway", "online", timedelta(minutes=5)),
        ("preview-gw-alex", "Alexandria Warehouse Gateway", helio_id, alex_site_id,
         warehouse_id, warehouse_floor_id, 330, 350, "Warehouse Gateway", "offline", timedelta(hours=6)),
    ]
    for gateway_id, name, client_id, site_id, building_id, floor_id, x, y, label, status, age in gateway_specs:
        conn.execute(
            """
            INSERT INTO gateways(gateway_id, name, client_id, site_id, status, last_seen,
                building_id, floor_id, x, y, label, location_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gateway_id) DO UPDATE SET name=excluded.name,
                client_id=excluded.client_id, site_id=excluded.site_id,
                status=excluded.status, last_seen=excluded.last_seen,
                building_id=excluded.building_id, floor_id=excluded.floor_id,
                x=excluded.x, y=excluded.y, label=excluded.label,
                location_note=excluded.location_note
            """,
            (gateway_id, name, client_id, site_id, status, _timestamp(now - age),
             building_id, floor_id, x, y, label, "Preview data — no TTN hardware required"),
        )

    salt, password_hash, iterations = _password_fields(PREVIEW_PASSWORD)
    user_specs = [
        ("Maya Hassan", "maya.hassan@example.com", "client", 1),
        ("Omar Farouk", "omar.farouk@example.com", "client", 1),
        ("Archived Preview User", "archived.preview@example.com", "client", 0),
    ]
    user_ids = {}
    for name, email, role, enabled in user_specs:
        conn.execute(
            """
            INSERT INTO users(name, email, role, enabled, password_salt,
                password_hash, password_iterations, password_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET name=excluded.name, role=excluded.role,
                enabled=excluded.enabled, password_salt=excluded.password_salt,
                password_hash=excluded.password_hash,
                password_iterations=excluded.password_iterations,
                password_updated_at=excluded.password_updated_at
            """,
            (name, email, role, enabled, salt, password_hash, iterations, _timestamp(now)),
        )
        user_ids[email] = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()["id"]

    for email, client_id, site_id in (
        ("maya.hassan@example.com", northstar_id, cairo_site_id),
        ("omar.farouk@example.com", helio_id, alex_site_id),
    ):
        user_id = user_ids[email]
        conn.execute("DELETE FROM user_access WHERE user_id = ?", (user_id,))
        conn.execute(
            """
            INSERT INTO user_access(user_id, client_id, site_id, access_level,
                can_view_devices, can_view_gateways, can_view_alarms,
                can_view_telemetry, can_manage_email_settings)
            VALUES (?, ?, ?, 'viewer', 1, 1, 1, 1, 0)
            """,
            (user_id, client_id, site_id),
        )

    conn.executemany(
        "INSERT OR IGNORE INTO alarm_recipients(email, enabled) VALUES (?, ?)",
        [("facilities.preview@example.com", 1), ("security.preview@example.com", 1),
         ("afterhours.preview@example.com", 0)],
    )

    conn.execute(
        f"DELETE FROM alarm_history WHERE source = 'preview_seed' OR device_id IN ({placeholders})",
        PREVIEW_DEVICE_IDS,
    )
    alarms = [
        ("preview-safety-server", "safety", "Operations Centre", "First Floor", "Server Room",
         "safety", "Water detected near server cooling unit", telemetry_by_device["preview-safety-server"],
         timedelta(minutes=12), 0, 0, "critical", "SAFETY_ALARM_ACTIVE", "alarm"),
        ("preview-energy-main", "energy", "Energy & Systems Lab", "Lab Level", "Main Meter Room",
         "energy", "High power usage: 6120 W exceeds 5000 W", telemetry_by_device["preview-energy-main"],
         timedelta(minutes=34), 1, 0, "warning", "ENERGY_HIGH_POWER", "power"),
        ("preview-env-warehouse", "environment", "Distribution Warehouse", "Warehouse Floor", "Storage Hall",
         "SYSTEM_OFFLINE", "Device has not sent data in 24 hours", telemetry_by_device["preview-env-warehouse"],
         timedelta(hours=6), 0, 0, "critical", "DEVICE_OFFLINE", None),
        ("preview-env-lobby", "environment", "Operations Centre", "Ground Floor", "Reception Lobby",
         "environment", "High temperature cleared after HVAC adjustment", {"temperature": 30.8, "humidity": 49.0},
         timedelta(days=1, hours=2), 1, 1, "warning", "ENV_HIGH_TEMPERATURE", "temperature"),
        ("preview-multi-roof", "multi", "Energy & Systems Lab", "Lab Level", "Systems Workshop",
         "maintenance", "Low battery warning cleared after service", {"battery": 18},
         timedelta(days=2, hours=4), 1, 1, "warning", "MULTI_LOW_BATTERY", "battery"),
        ("preview-occ-meeting", "occupancy", "Operations Centre", "Ground Floor", "Meeting Room Atlas",
         "occupancy", "Motion detected after scheduled hours", {"motion": True},
         timedelta(days=3), 1, 1, "warning", "OCC_MOTION_DETECTED", "motion"),
    ]
    for (device_id, node_type, building, floor, room, alarm_type, message, telemetry,
         age, acknowledged, resolved, severity, rule_code, field_key) in alarms:
        triggered = now - age
        conn.execute(
            """
            INSERT INTO alarm_history(device_id, node_type, building, floor, room,
                alarm_type, alarm_message, telemetry, triggered_at, acknowledged,
                acknowledged_by, acknowledged_at, resolved, resolved_by, resolved_at,
                rule_code, severity, field_key, source, auto_resolved, resolved_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'preview_seed', ?, ?)
            """,
            (device_id, node_type, building, floor, room, alarm_type, message,
             _json(telemetry), _timestamp(triggered), acknowledged,
             "Preview Operations" if acknowledged else None,
             _timestamp(triggered + timedelta(minutes=15)) if acknowledged else None,
             resolved, "Preview Operations" if resolved else None,
             _timestamp(triggered + timedelta(minutes=45)) if resolved else None,
             rule_code, severity, field_key, resolved,
             "Preview incident closed" if resolved else None),
        )

    conn.execute("DELETE FROM audit_log WHERE actor = 'Preview Seeder'")
    audit_specs = [
        ("create_client", "client", str(northstar_id), "Added Northstar Property Group", northstar_id, None, None),
        ("create_site", "site", str(cairo_site_id), "Created Cairo Innovation Campus", northstar_id, cairo_site_id, None),
        ("upload_floor_image", "floor", str(ground_id), "Attached repository floor plan", northstar_id, cairo_site_id, ground_id),
        ("place_device", "device", "preview-env-lobby", "Placed Lobby Climate in Reception Lobby", northstar_id, cairo_site_id, ground_id),
        ("place_gateway", "gateway", "preview-gw-cairo-roof", "Positioned Cairo Roof Gateway", northstar_id, cairo_site_id, first_id),
        ("assign_profile", "device", "preview-energy-main", "Assigned Legacy Energy profile", northstar_id, cairo_site_id, lab_floor_id),
        ("acknowledge_alarm", "alarm", "preview-energy-main", "Acknowledged high power usage", northstar_id, cairo_site_id, lab_floor_id),
        ("create_user", "user", str(user_ids["maya.hassan@example.com"]), "Granted Maya Hassan campus access", northstar_id, cairo_site_id, None),
    ]
    for index, (action, target_type, target_id, message, client_id, site_id, floor_id) in enumerate(audit_specs):
        details = {"preview_seed": True, "summary": message}
        conn.execute(
            """
            INSERT INTO audit_log(actor, action, target_type, target_id, details,
                message, created_at, client_id, site_id, floor_id, device_id,
                gateway_id, user_id)
            VALUES ('Preview Seeder', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (action, target_type, target_id, _json(details), message,
             _timestamp(now - timedelta(hours=index * 5 + 1)), client_id, site_id,
             floor_id, target_id if target_type == "device" else None,
             target_id if target_type == "gateway" else None,
             int(target_id) if target_type == "user" else None),
        )

    conn.commit()
    return {
        "clients": 2,
        "sites": 2,
        "buildings": 3,
        "floors": 4,
        "rooms": len(room_specs),
        "devices": len(device_specs) + 1,
        "gateways": len(gateway_specs),
        "telemetry_samples": len(device_specs) * 48,
        "alarms": len(alarms),
        "users": len(user_specs),
    }


def main() -> None:
    run_migrations()
    conn = get_db_connection()
    try:
        summary = seed_preview_data(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    db_path = Path(settings.DB_FILE)
    print(f"Preview data ready in {db_path}")
    print("  " + ", ".join(f"{name}={count}" for name, count in summary.items()))
    print("  Client login: maya.hassan@example.com / Preview123!")


if __name__ == "__main__":
    main()
