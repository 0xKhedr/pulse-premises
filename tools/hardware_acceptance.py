from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.connection import get_db_connection
from app.services.device_state import build_device_state
from app.services.ttn import ttn_client


def read_local_state(device_id: str | None, dev_eui: str | None) -> dict | None:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT d.*, lt.telemetry, lt.updated_at AS telemetry_updated_at
            FROM devices d
            LEFT JOIN device_latest_telemetry lt ON lt.device_id = d.device_id
            WHERE (? IS NOT NULL AND d.device_id = ?)
               OR (? IS NOT NULL AND d.dev_eui = ? COLLATE NOCASE)
            LIMIT 1
            """,
            (device_id, device_id, dev_eui, dev_eui),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait for TTN registration, first uplink, and SBMS discovery.",
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--device-id")
    identity.add_argument("--dev-eui")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--skip-ttn", action="store_true")
    parser.add_argument("--require-commissioned", action="store_true")
    args = parser.parse_args()

    if args.device_id and not args.skip_ttn:
        try:
            ttn_client.get_application_device(args.device_id)
            print(f"PASS TTN registration: {args.device_id}")
        except Exception as exc:
            print(f"FAIL TTN registration: {exc}", file=sys.stderr)
            return 2

    deadline = time.monotonic() + max(1, args.timeout)
    while time.monotonic() < deadline:
        state = read_local_state(args.device_id, args.dev_eui)
        if state and state.get("telemetry_updated_at"):
            commissioned = build_device_state(state)["commissioned"]
            if args.require_commissioned and not commissioned:
                print("WAIT device discovered but not commissioned")
            else:
                telemetry = json.loads(state.get("telemetry") or "{}")
                print(
                    json.dumps(
                        {
                            "status": "pass",
                            "device_id": state["device_id"],
                            "dev_eui": state.get("dev_eui"),
                            "discovered": True,
                            "commissioned": commissioned,
                            "floor_id": state.get("floor_id"),
                            "room_id": state.get("room_id"),
                            "last_uplink": state["telemetry_updated_at"],
                            "rssi": telemetry.get("rssi"),
                            "snr": telemetry.get("snr"),
                            "gateway_id": telemetry.get("gateway_id"),
                        },
                        indent=2,
                    )
                )
                return 0
        time.sleep(max(0.25, args.poll_seconds))

    print("FAIL timed out waiting for a stored uplink", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
