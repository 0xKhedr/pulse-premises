from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params)]


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def insert_filtered(
    conn: sqlite3.Connection,
    table: str,
    row: dict[str, Any],
    *,
    exclude: set[str] | None = None,
) -> int:
    allowed = columns(conn, table) - (exclude or set())
    payload = {key: value for key, value in row.items() if key in allowed}
    names = list(payload)
    placeholders = ", ".join("?" for _ in names)
    cursor = conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
        tuple(payload[name] for name in names),
    )
    return int(cursor.lastrowid)


def natural_id(
    conn: sqlite3.Connection,
    table: str,
    matches: dict[str, Any],
) -> int | None:
    clauses = []
    values = []
    for key, value in matches.items():
        if isinstance(value, str):
            clauses.append(f"LOWER({key}) = LOWER(?)")
        elif value is None:
            clauses.append(f"{key} IS NULL")
            continue
        else:
            clauses.append(f"{key} = ?")
        values.append(value)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {' AND '.join(clauses)} LIMIT 1",
        tuple(values),
    ).fetchone()
    return int(row["id"]) if row else None


def copy_natural(
    target: sqlite3.Connection,
    table: str,
    source_row: dict[str, Any],
    matches: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> tuple[int, bool]:
    existing = natural_id(target, table, matches)
    if existing is not None:
        return existing, False
    payload = dict(source_row)
    payload.update(overrides or {})
    return insert_filtered(target, table, payload, exclude={"id"}), True


def safe_upload_path(value: Any) -> Any:
    if not value or not isinstance(value, str) or not value.startswith("/uploads/"):
        return value
    return value if (PROJECT_ROOT / value.removeprefix("/")).is_file() else None


def load_shared_credentials() -> tuple[str, str]:
    env = dotenv_values(PROJECT_ROOT / ".env")
    join_eui = str(env.get("JOIN_EUI") or env.get("TTN_JOIN_EUI") or "").strip().upper()
    app_key = str(env.get("LORAWAN_APP_KEY") or "").strip().upper()
    if len(join_eui) != 16 or len(app_key) != 32:
        raise ValueError(".env must contain a 16-hex JOIN_EUI and 32-hex LORAWAN_APP_KEY")
    int(join_eui, 16)
    int(app_key, 16)
    return join_eui, app_key


def reconcile(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    join_eui: str,
    app_key: str,
    remove_devices: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {
        "clients": 0,
        "sites": 0,
        "buildings": 0,
        "floors": 0,
        "rooms": 0,
        "sensors": 0,
        "firmware_modules": 0,
        "profiles": 0,
        "devices": 0,
        "alarms": 0,
        "removed_invalid_devices": 0,
        "skipped_orphan_rooms": 0,
    }

    client_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM clients ORDER BY id"):
        new_id, created = copy_natural(target, "clients", row, {"name": row["name"]})
        client_map[row["id"]] = new_id
        counts["clients"] += int(created)

    site_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM sites ORDER BY id"):
        client_id = client_map.get(row["client_id"])
        new_id, created = copy_natural(
            target,
            "sites",
            row,
            {"client_id": client_id, "name": row["name"]},
            {"client_id": client_id, "campus_image_path": safe_upload_path(row.get("campus_image_path"))},
        )
        site_map[row["id"]] = new_id
        counts["sites"] += int(created)

    building_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM buildings ORDER BY id"):
        site_id = site_map.get(row["site_id"])
        new_id, created = copy_natural(
            target,
            "buildings",
            row,
            {"site_id": site_id, "name": row["name"]},
            {"site_id": site_id},
        )
        building_map[row["id"]] = new_id
        counts["buildings"] += int(created)

    floor_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM floors ORDER BY id"):
        building_id = building_map.get(row["building_id"])
        new_id, created = copy_natural(
            target,
            "floors",
            row,
            {"building_id": building_id, "name": row["name"]},
            {"building_id": building_id, "image_path": safe_upload_path(row.get("image_path"))},
        )
        floor_map[row["id"]] = new_id
        counts["floors"] += int(created)

    room_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM rooms ORDER BY id"):
        floor_id = floor_map.get(row.get("floor_id"))
        if floor_id is None:
            counts["skipped_orphan_rooms"] += 1
            continue
        new_id, created = copy_natural(
            target,
            "rooms",
            row,
            {"floor_id": floor_id, "room_name": row["room_name"]},
            {"floor_id": floor_id},
        )
        room_map[row["id"]] = new_id
        counts["rooms"] += int(created)

    sensor_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM sensor_catalog ORDER BY id"):
        new_id, created = copy_natural(
            target, "sensor_catalog", row, {"sensor_code": row["sensor_code"]}
        )
        sensor_map[row["id"]] = new_id
        counts["sensors"] += int(created)

    module_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM firmware_modules ORDER BY id"):
        new_id, created = copy_natural(
            target, "firmware_modules", row, {"module_key": row["module_key"]}
        )
        module_map[row["id"]] = new_id
        counts["firmware_modules"] += int(created)

    profile_map: dict[int, int] = {}
    for row in rows(source, "SELECT * FROM sensor_profiles ORDER BY id"):
        new_id, created = copy_natural(
            target, "sensor_profiles", row, {"profile_code": row["profile_code"]}
        )
        profile_map[row["id"]] = new_id
        counts["profiles"] += int(created)

    for source_profile_id, target_profile_id in profile_map.items():
        for row in rows(source, "SELECT * FROM sensor_profile_fields WHERE profile_id=?", (source_profile_id,)):
            if natural_id(target, "sensor_profile_fields", {"profile_id": target_profile_id, "field_key": row["field_key"]}) is None:
                insert_filtered(target, "sensor_profile_fields", {**row, "profile_id": target_profile_id}, exclude={"id"})
        for row in rows(source, "SELECT * FROM sensor_profile_rules WHERE profile_id=?", (source_profile_id,)):
            if natural_id(target, "sensor_profile_rules", {"profile_id": target_profile_id, "rule_code": row["rule_code"]}) is None:
                insert_filtered(target, "sensor_profile_rules", {**row, "profile_id": target_profile_id}, exclude={"id"})
        for row in rows(source, "SELECT * FROM sensor_profile_sensors WHERE profile_id=?", (source_profile_id,)):
            sensor_id = sensor_map.get(row["sensor_id"])
            module_id = module_map.get(row["firmware_module_id"])
            match = {"profile_id": target_profile_id, "sensor_id": sensor_id, "role": row["role"]}
            if natural_id(target, "sensor_profile_sensors", match) is None:
                insert_filtered(target, "sensor_profile_sensors", {**row, "profile_id": target_profile_id, "sensor_id": sensor_id, "firmware_module_id": module_id}, exclude={"id"})
        for row in rows(source, "SELECT * FROM profile_firmware_compatibility WHERE profile_id=?", (source_profile_id,)):
            module_id = module_map.get(row["firmware_module_id"])
            match = {"profile_id": target_profile_id, "firmware_module_id": module_id}
            if natural_id(target, "profile_firmware_compatibility", match) is None:
                insert_filtered(target, "profile_firmware_compatibility", {**row, "profile_id": target_profile_id, "firmware_module_id": module_id}, exclude={"id"})

    default_profiles = {
        "environment": "ENV_SHT31_V1",
        "occupancy": "OCC_GENERIC_V1",
        "safety": "SAFETY_GENERIC_V1",
        "energy": "ENERGY_LEGACY_V1",
        "multi": "MULTI_LEGACY_V1",
    }
    target_profile_by_code = {
        row["profile_code"]: dict(row)
        for row in target.execute("SELECT id, profile_code, profile_version, payload_version FROM sensor_profiles")
    }
    touched_device_ids: set[str] = set()
    for row in rows(source, "SELECT * FROM devices ORDER BY device_id"):
        if target.execute("SELECT 1 FROM devices WHERE device_id=?", (row["device_id"],)).fetchone():
            continue
        profile_code = row.get("profile_code") or default_profiles.get(row.get("node_type"))
        profile = target_profile_by_code.get(profile_code)
        floor_id = floor_map.get(row.get("floor_id"))
        room_id = room_map.get(row.get("room_id"))
        payload = {
            **row,
            "client_id": client_map.get(row.get("client_id")),
            "site_id": site_map.get(row.get("site_id")),
            "building_id": building_map.get(row.get("building_id")),
            "floor_id": floor_id,
            "room_id": room_id,
            "profile_id": profile["id"] if profile else None,
            "profile_code": profile_code,
            "profile_version": profile["profile_version"] if profile else None,
            "payload_version": profile["payload_version"] if profile else None,
            "join_eui": join_eui,
            "app_key": app_key,
            "status": "offline" if floor_id is not None else "discovered",
            "is_placed": int(floor_id is not None),
        }
        insert_filtered(target, "devices", payload)
        counts["devices"] += 1
        touched_device_ids.add(row["device_id"])

    for row in rows(source, "SELECT * FROM alarm_history ORDER BY id"):
        exists = target.execute(
            """SELECT 1 FROM alarm_history
               WHERE device_id=? AND alarm_type=? AND alarm_message=?
                 AND COALESCE(triggered_at,'')=COALESCE(?,'') LIMIT 1""",
            (row["device_id"], row["alarm_type"], row["alarm_message"], row["triggered_at"]),
        ).fetchone()
        if exists:
            continue
        profile = target_profile_by_code.get(row.get("profile_code"))
        payload = {**row, "profile_id": profile["id"] if profile else None, "rule_id": None}
        insert_filtered(target, "alarm_history", payload, exclude={"id"})
        counts["alarms"] += 1

    for device_id in sorted(remove_devices):
        device_tables = []
        for table_row in target.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            table = table_row["name"]
            if table != "devices" and "device_id" in columns(target, table):
                device_tables.append(table)
        for table in device_tables:
            target.execute(f"DELETE FROM {table} WHERE device_id=?", (device_id,))
        cursor = target.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
        counts["removed_invalid_devices"] += cursor.rowcount

    # Scoped to only the devices this run actually inserted above -- an
    # unscoped WHERE here would rewrite join_eui/app_key (and profile_code)
    # for every already-provisioned device in the target on every invocation,
    # even ones untouched by this merge, desyncing the DB from what TTN's
    # join server actually holds if the shared credentials were rotated
    # in .env since those other devices were provisioned.
    if touched_device_ids:
        placeholders = ", ".join("?" for _ in touched_device_ids)
        target.execute(
            f"""UPDATE devices
               SET profile_code=(SELECT profile_code FROM sensor_profiles WHERE id=devices.profile_id),
                   profile_version=COALESCE(profile_version,(SELECT profile_version FROM sensor_profiles WHERE id=devices.profile_id)),
                   payload_version=COALESCE(payload_version,(SELECT payload_version FROM sensor_profiles WHERE id=devices.profile_id)),
                   join_eui=?, app_key=?
               WHERE dev_eui IS NOT NULL AND profile_id IS NOT NULL AND device_id IN ({placeholders})""",
            (join_eui, app_key, *touched_device_ids),
        )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile an older SBMS SQLite database into the active database.")
    parser.add_argument("--source", default="devices.db")
    parser.add_argument("--target", default="smarthome.db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove-device", action="append", default=[])
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    target_path = Path(args.target).resolve()
    if source_path == target_path:
        raise SystemExit("Source and target must be different files")
    join_eui, app_key = load_shared_credentials()

    if args.apply:
        backup_dir = PROJECT_ROOT / "backups" / f"reconcile-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source_path, backup_dir / source_path.name)
        shutil.copy2(target_path, backup_dir / target_path.name)
        print(f"Backups created in {backup_dir}")

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(target_path)
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    try:
        target.execute("BEGIN IMMEDIATE")
        counts = reconcile(
            source,
            target,
            join_eui=join_eui,
            app_key=app_key,
            remove_devices=set(args.remove_device),
        )
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Reconciliation produced {len(violations)} foreign-key violations")
        if args.apply:
            target.commit()
        else:
            target.rollback()
        print({"mode": "apply" if args.apply else "dry-run", **counts})
        return 0
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
