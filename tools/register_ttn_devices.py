from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.connection import get_db_connection
from app.main import (
    APP_ID,
    JOIN_EUI,
    LORAWAN_APP_KEY,
    get_sensor_profile_detail,
    make_device_id,
    normalize_eui,
    normalize_mac,
    register_device_in_ttn,
    validate_config,
)
from app.services.lorawan_identity import derive_dev_eui_from_mac


def load_inventory(args: argparse.Namespace) -> list[dict[str, str]]:
    rows = [{"chip_mac": value, "device_id": ""} for value in args.mac]
    if args.csv:
        with Path(args.csv).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if "chip_mac" not in (reader.fieldnames or []):
                raise ValueError("CSV must contain a chip_mac column")
            rows.extend(
                {
                    "chip_mac": row.get("chip_mac", ""),
                    "device_id": row.get("device_id", ""),
                }
                for row in reader
            )
    if not rows:
        raise ValueError("Provide at least one --mac or --csv inventory file")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-register ESP32 identities in TTN without placing them in SBMS.",
    )
    parser.add_argument("--mac", action="append", default=[], help="ESP32 MAC; repeatable")
    parser.add_argument("--csv", help="CSV containing chip_mac and optional device_id")
    parser.add_argument("--profile-code", default="CAYENNE_LPP_V1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        inventory = load_inventory(args)
        join_eui = normalize_eui(JOIN_EUI, 16)
        app_key = normalize_eui(LORAWAN_APP_KEY, 32)
        conn = get_db_connection()
        try:
            profile = get_sensor_profile_detail(
                conn,
                args.profile_code,
                include_formatter=True,
            )
        finally:
            conn.close()
        if not profile or not profile.get("enabled") or profile.get("status") != "active":
            raise ValueError(f"Active sensor profile not found: {args.profile_code}")
        if not args.dry_run:
            validate_config()

        results = []
        for item in inventory:
            mac = normalize_mac(item["chip_mac"])
            dev_eui = derive_dev_eui_from_mac(mac, namespace=APP_ID)
            device_id = str(item.get("device_id") or "").strip() or make_device_id(mac)
            result = {
                "chip_mac": mac,
                "device_id": device_id,
                "dev_eui": dev_eui,
                "join_eui": join_eui,
                "status": "dry_run" if args.dry_run else "registered",
            }
            if not args.dry_run:
                register_device_in_ttn(
                    device_id=device_id,
                    dev_eui=dev_eui,
                    join_eui=join_eui,
                    app_key=app_key,
                    profile=profile,
                )
            results.append(result)

        print(json.dumps({"count": len(results), "devices": results}, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
