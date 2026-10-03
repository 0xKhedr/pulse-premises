from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.main import (
    get_sensor_profile_detail,
    register_device_in_ttn,
    set_device_formatter_from_profile,
    set_ttn_application_server,
    set_ttn_join_server,
    set_ttn_network_server,
    validate_config,
)
from app.services.lorawan_identity import derive_dev_eui_from_mac, normalize_hex
from app.services.ttn import ttn_client


def normalize_mac(value: str) -> str:
    return normalize_hex(value, 12, "chip_mac")


def remote_exists(device_id: str) -> tuple[bool, str | None]:
    try:
        payload = ttn_client.get_application_device(device_id)
        device = payload.get("end_device", payload)
        return True, str(device.get("ids", {}).get("dev_eui") or "").upper() or None
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return False, None
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Align SBMS device DevEUIs and shared root credentials with TTN."
    )
    parser.add_argument("--database", default=settings.DB_FILE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    validate_config()
    join_eui = normalize_hex(settings.JOIN_EUI, 16, "JOIN_EUI")
    app_key = normalize_hex(settings.LORAWAN_APP_KEY, 32, "LORAWAN_APP_KEY")
    database = Path(args.database).resolve()
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    candidates = conn.execute(
        """SELECT device_id, chip_mac, dev_eui, app_key, join_eui, profile_code
           FROM devices
           WHERE dev_eui IS NOT NULL AND chip_mac IS NOT NULL
           ORDER BY device_id"""
    ).fetchall()

    plan = []
    for row in candidates:
        try:
            expected_eui = derive_dev_eui_from_mac(
                normalize_mac(row["chip_mac"]), namespace=settings.TTN_APP_ID
            )
        except ValueError:
            plan.append({"device_id": row["device_id"], "action": "skip_invalid_mac"})
            continue
        exists, remote_eui = remote_exists(row["device_id"])
        local_credentials_match = (
            str(row["app_key"] or "").upper() == app_key
            and str(row["join_eui"] or "").upper() == join_eui
        )
        if not exists:
            action = "create"
        elif remote_eui != expected_eui:
            action = "recreate"
        elif not local_credentials_match:
            action = "update_credentials"
        else:
            action = "noop"
        plan.append(
            {
                "device_id": row["device_id"],
                "action": action,
                "remote_present": exists,
                "identity_matches": remote_eui == expected_eui,
                "expected_dev_eui": expected_eui,
            }
        )

    if not args.apply:
        print(json.dumps({"mode": "dry-run", "devices": plan}, indent=2))
        conn.close()
        return 0

    backup_dir = PROJECT_ROOT / "backups" / f"ttn-sync-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(database, backup_dir / database.name)
    completed = []
    try:
        for item in plan:
            if item["action"] in {"skip_invalid_mac", "noop"}:
                continue
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id=?", (item["device_id"],)
            ).fetchone()
            profile = get_sensor_profile_detail(
                conn, row["profile_code"], include_formatter=True
            )
            if not profile or not profile.get("enabled") or profile.get("status") != "active":
                raise RuntimeError(f"Active profile missing for {row['device_id']}")

            old_eui = row["dev_eui"]
            old_key = row["app_key"]
            old_join = row["join_eui"]
            if item["action"] == "recreate":
                ttn_client.delete_device(row["device_id"])
            try:
                if item["action"] == "update_credentials":
                    set_ttn_join_server(
                        row["device_id"], item["expected_dev_eui"], join_eui, app_key
                    )
                    set_ttn_network_server(
                        row["device_id"], item["expected_dev_eui"], join_eui
                    )
                    set_ttn_application_server(
                        row["device_id"], item["expected_dev_eui"], join_eui
                    )
                    set_device_formatter_from_profile(row["device_id"], profile)
                else:
                    register_device_in_ttn(
                        device_id=row["device_id"],
                        dev_eui=item["expected_dev_eui"],
                        join_eui=join_eui,
                        app_key=app_key,
                        profile=profile,
                    )
            except Exception:
                if item["action"] == "recreate" and old_eui and old_key and old_join:
                    register_device_in_ttn(
                        device_id=row["device_id"],
                        dev_eui=old_eui,
                        join_eui=old_join,
                        app_key=old_key,
                        profile=profile,
                    )
                raise
            conn.execute(
                """UPDATE devices
                   SET dev_eui=?, join_eui=?, app_key=?, updated_at=CURRENT_TIMESTAMP
                   WHERE device_id=?""",
                (item["expected_dev_eui"], join_eui, app_key, row["device_id"]),
            )
            conn.commit()
            completed.append(row["device_id"])
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "updated_count": len(completed),
                    "devices": completed,
                    "backup": str(backup_dir),
                },
                indent=2,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
