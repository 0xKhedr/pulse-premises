import logging

logger = logging.getLogger(__name__)

import io
import json
from datetime import datetime, timezone

import requests
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.db.connection import get_db_connection as db
from app.main import (
    TB_PASSWORD,
    TB_USERNAME,
    THINGSBOARD_URL,
    get_local_latest_telemetry,
    read_tb_latest_telemetry,
)
from app.services.export_safety import SafeDictWriter, redact_secrets

router = APIRouter()

@router.get("/admin/export/full-structure.csv")
def admin_full_structure_export_csv():
    conn = db()
    try:

        def rows_to_dicts(query):
            return [dict(row) for row in conn.execute(query).fetchall()]

        def clean_details(data):
            try:
                return json.dumps(redact_secrets(data), ensure_ascii=False, default=str)
            except Exception:
                return str(data)

        def get_name(row, keys=("name", "label", "title")):
            if not row:
                return ""

            for key in keys:
                value = row.get(key)
                if value:
                    return value

            return ""

        clients = rows_to_dicts("SELECT * FROM clients ORDER BY id")
        sites = rows_to_dicts("SELECT * FROM sites ORDER BY id")
        buildings = rows_to_dicts("SELECT * FROM buildings ORDER BY id")
        floors = rows_to_dicts("SELECT * FROM floors ORDER BY id")
        rooms = rows_to_dicts("SELECT * FROM rooms ORDER BY id")
        devices = rows_to_dicts("SELECT * FROM devices ORDER BY device_id")
        gateways = rows_to_dicts("SELECT * FROM gateways ORDER BY id")

        clients_by_id = {row.get("id"): row for row in clients}
        sites_by_id = {row.get("id"): row for row in sites}
        buildings_by_id = {row.get("id"): row for row in buildings}
        floors_by_id = {row.get("id"): row for row in floors}
        rooms_by_id = {row.get("id"): row for row in rooms}

        output = io.StringIO()

        fieldnames = [
            "record_type",
            "client_id",
            "client_name",
            "site_id",
            "site_name",
            "building_id",
            "building_name",
            "floor_id",
            "floor_name",
            "floor_number",
            "room_id",
            "room_name",
            "asset_type",
            "asset_id",
            "asset_name",
            "device_id",
            "gateway_id",
            "node_type",
            "status",
            "x",
            "y",
            "label",
            "location_note",
            "last_seen",
            "created_at",
            "details"
        ]

        writer = SafeDictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        def write_row(
            record_type,
            client=None,
            site=None,
            building=None,
            floor=None,
            room=None,
            asset_type="",
            asset_id="",
            asset_name="",
            device_id="",
            gateway_id="",
            node_type="",
            status="",
            x="",
            y="",
            label="",
            location_note="",
            last_seen="",
            created_at="",
            details=None
        ):
            writer.writerow({
                "record_type": record_type,

                "client_id": client.get("id") if client else "",
                "client_name": get_name(client),

                "site_id": site.get("id") if site else "",
                "site_name": get_name(site),

                "building_id": building.get("id") if building else "",
                "building_name": get_name(building),

                "floor_id": floor.get("id") if floor else "",
                "floor_name": get_name(floor),
                "floor_number": floor.get("floor_number") if floor else "",

                "room_id": room.get("id") if room else "",
                "room_name": get_name(room),

                "asset_type": asset_type,
                "asset_id": asset_id,
                "asset_name": asset_name,
                "device_id": device_id,
                "gateway_id": gateway_id,
                "node_type": node_type,
                "status": status,
                "x": x,
                "y": y,
                "label": label,
                "location_note": location_note,
                "last_seen": last_seen,
                "created_at": created_at,
                "details": clean_details(details or {})
            })

        # 1. Clients
        for client in clients:
            write_row(
                record_type="CLIENT",
                client=client,
                asset_type="client",
                asset_id=client.get("id"),
                asset_name=get_name(client),
                created_at=client.get("created_at", ""),
                details=client
            )

        # 2. Sites
        for site in sites:
            client = clients_by_id.get(site.get("client_id"))

            write_row(
                record_type="SITE",
                client=client,
                site=site,
                asset_type="site",
                asset_id=site.get("id"),
                asset_name=get_name(site),
                created_at=site.get("created_at", ""),
                details=site
            )

        # 3. Buildings
        for building in buildings:
            site = sites_by_id.get(building.get("site_id"))
            client = clients_by_id.get(site.get("client_id")) if site else None

            write_row(
                record_type="BUILDING",
                client=client,
                site=site,
                building=building,
                asset_type="building",
                asset_id=building.get("id"),
                asset_name=get_name(building),
                created_at=building.get("created_at", ""),
                details=building
            )

        # 4. Floors
        for floor in floors:
            building = buildings_by_id.get(floor.get("building_id"))
            site = sites_by_id.get(building.get("site_id")) if building else None
            client = clients_by_id.get(site.get("client_id")) if site else None

            write_row(
                record_type="FLOOR",
                client=client,
                site=site,
                building=building,
                floor=floor,
                asset_type="floor",
                asset_id=floor.get("id"),
                asset_name=get_name(floor),
                created_at=floor.get("created_at", ""),
                details=floor
            )

        # 5. Rooms
        for room in rooms:
            floor = floors_by_id.get(room.get("floor_id"))
            building = buildings_by_id.get(floor.get("building_id")) if floor else None
            site = sites_by_id.get(building.get("site_id")) if building else None
            client = clients_by_id.get(site.get("client_id")) if site else None

            write_row(
                record_type="ROOM",
                client=client,
                site=site,
                building=building,
                floor=floor,
                room=room,
                asset_type="room",
                asset_id=room.get("id"),
                asset_name=get_name(room),
                x=room.get("x", ""),
                y=room.get("y", ""),
                created_at=room.get("created_at", ""),
                details=room
            )

        # 6. Devices / Nodes
        for device in devices:
            room = rooms_by_id.get(device.get("room_id"))
            floor = floors_by_id.get(room.get("floor_id")) if room else floors_by_id.get(device.get("floor_id"))
            building = buildings_by_id.get(floor.get("building_id")) if floor else buildings_by_id.get(device.get("building_id"))
            site = sites_by_id.get(building.get("site_id")) if building else sites_by_id.get(device.get("site_id"))
            client = clients_by_id.get(site.get("client_id")) if site else clients_by_id.get(device.get("client_id"))

            write_row(
                record_type="DEVICE",
                client=client,
                site=site,
                building=building,
                floor=floor,
                room=room,
                asset_type="device",
                asset_id=device.get("device_id"),
                asset_name=device.get("label") or device.get("device_id"),
                device_id=device.get("device_id"),
                node_type=device.get("node_type", ""),
                status=device.get("status", ""),
                x=device.get("x", ""),
                y=device.get("y", ""),
                label=device.get("label", ""),
                last_seen=device.get("last_seen", ""),
                created_at=device.get("created_at", ""),
                details=device
            )

        # 7. Gateways
        for gateway in gateways:
            floor = floors_by_id.get(gateway.get("floor_id"))
            building = buildings_by_id.get(gateway.get("building_id")) or (
                buildings_by_id.get(floor.get("building_id")) if floor else None
            )
            site = sites_by_id.get(gateway.get("site_id")) or (
                sites_by_id.get(building.get("site_id")) if building else None
            )
            client = clients_by_id.get(gateway.get("client_id")) or (
                clients_by_id.get(site.get("client_id")) if site else None
            )

            write_row(
                record_type="GATEWAY",
                client=client,
                site=site,
                building=building,
                floor=floor,
                room=None,
                asset_type="gateway",
                asset_id=gateway.get("gateway_id"),
                asset_name=gateway.get("label") or gateway.get("name") or gateway.get("gateway_id"),
                gateway_id=gateway.get("gateway_id"),
                status=gateway.get("status", ""),
                x=gateway.get("x", ""),
                y=gateway.get("y", ""),
                label=gateway.get("label", ""),
                location_note=gateway.get("location_note", ""),
                last_seen=gateway.get("last_seen", ""),
                created_at=gateway.get("created_at", ""),
                details=gateway
            )

        conn.close()

        output.seek(0)

        export_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"admin_full_structure_export_{export_date}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    finally:
        conn.close()
@router.get("/admin/export/device-inventory.csv")
def admin_device_inventory_export_csv():
    conn = db()
    try:

        def rows_to_dicts(query, params=()):
            try:
                return [dict(row) for row in conn.execute(query, params).fetchall()]
            except Exception as e:
                logger.error("CSV query error: %s", e)
                return []

        def get_name(row, keys=("name", "label", "title", "room_name")):
            if not row:
                return ""

            for key in keys:
                value = row.get(key)
                if value:
                    return value

            return ""

        def clean_details(data):
            try:
                return json.dumps(redact_secrets(data or {}), ensure_ascii=False, default=str)
            except Exception:
                return str(data)

        def parse_json_maybe(value):
            if value is None:
                return {}

            if isinstance(value, dict):
                return value

            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return {}

            return {}

        def has_value(value):
            return value is not None and value != "" and value != "null"

        def merge_telemetry(target, source):
            source = source or {}

            for key, value in source.items():
                if has_value(value):
                    target[key] = value

            return target

        def tb_has_real_values(tb_telemetry):
            if not tb_telemetry:
                return False

            if tb_telemetry.get("last_seen_ts") is not None:
                return True

            useful_keys = [
                "temperature",
                "humidity",
                "motion",
                "alarm",
                "voltage",
                "current",
                "power"
            ]

            for key in useful_keys:
                if tb_telemetry.get(key) is not None:
                    return True

            return False

        clients = rows_to_dicts("SELECT * FROM clients ORDER BY id")
        sites = rows_to_dicts("SELECT * FROM sites ORDER BY id")
        buildings = rows_to_dicts("SELECT * FROM buildings ORDER BY id")
        floors = rows_to_dicts("SELECT * FROM floors ORDER BY id")
        rooms = rows_to_dicts("SELECT * FROM rooms ORDER BY id")
        devices = rows_to_dicts("SELECT * FROM devices ORDER BY device_id")

        clients_by_id = {row.get("id"): row for row in clients}
        sites_by_id = {row.get("id"): row for row in sites}
        buildings_by_id = {row.get("id"): row for row in buildings}
        floors_by_id = {row.get("id"): row for row in floors}
        rooms_by_id = {row.get("id"): row for row in rooms}

        capability_rows = rows_to_dicts("""
        SELECT device_id, capability
        FROM device_capabilities
        ORDER BY device_id, capability
    """)

        capabilities_by_device = {}

        for row in capability_rows:
            capability_device_id = row.get("device_id")

            if capability_device_id not in capabilities_by_device:
                capabilities_by_device[capability_device_id] = []

            capabilities_by_device[capability_device_id].append(row.get("capability"))

        alarm_rows = rows_to_dicts("""
        SELECT *
        FROM alarm_history
        ORDER BY datetime(triggered_at) DESC, id DESC
    """)

        latest_alarm_by_device = {}
        active_alarm_by_device = {}

        for alarm in alarm_rows:
            alarm_device_id = alarm.get("device_id")

            if not alarm_device_id:
                continue

            if alarm_device_id not in latest_alarm_by_device:
                latest_alarm_by_device[alarm_device_id] = alarm

            if alarm.get("resolved") in [0, "0", False, None]:
                if alarm_device_id not in active_alarm_by_device:
                    active_alarm_by_device[alarm_device_id] = alarm

        def resolve_device_location(device):
            room = None
            floor = None
            building = None
            site = None
            client = None

            room_id = device.get("room_id")
            floor_id = device.get("floor_id")
            building_id = device.get("building_id")
            site_id = device.get("site_id")
            client_id = device.get("client_id")

            if room_id:
                room = rooms_by_id.get(room_id)

            if room:
                room_floor_id = room.get("floor_id")
                if room_floor_id:
                    floor = floors_by_id.get(room_floor_id)

            if not floor and floor_id:
                floor = floors_by_id.get(floor_id)

            if floor:
                floor_building_id = floor.get("building_id")
                if floor_building_id:
                    building = buildings_by_id.get(floor_building_id)

            if not building and building_id:
                building = buildings_by_id.get(building_id)

            if building:
                building_site_id = building.get("site_id")
                if building_site_id:
                    site = sites_by_id.get(building_site_id)

            if not site and site_id:
                site = sites_by_id.get(site_id)

            if site:
                site_client_id = site.get("client_id")
                if site_client_id:
                    client = clients_by_id.get(site_client_id)

            if not client and client_id:
                client = clients_by_id.get(client_id)

            return client, site, building, floor, room

        def get_inventory_telemetry(device_id):
            telemetry = {}
            sources = []

            latest_alarm = latest_alarm_by_device.get(device_id)

            if latest_alarm and latest_alarm.get("telemetry"):
                alarm_telemetry = parse_json_maybe(latest_alarm.get("telemetry"))
                telemetry = merge_telemetry(telemetry, alarm_telemetry)
                sources.append("alarm_history")

            try:
                tb_telemetry = read_tb_latest_telemetry(device_id) or {}
            except Exception as e:
                logger.error("TB telemetry failed during CSV export: %s", e)
                tb_telemetry = {}

            if tb_has_real_values(tb_telemetry):
                telemetry = merge_telemetry(telemetry, tb_telemetry)
                sources.append("thingsboard")

            try:
                local_telemetry = get_local_latest_telemetry(conn, device_id) or {}
            except Exception as e:
                logger.error("Local telemetry failed during CSV export: %s", e)
                local_telemetry = {}

            if local_telemetry:
                telemetry = merge_telemetry(telemetry, local_telemetry)
                sources.append("local_latest")

            active_alarm = active_alarm_by_device.get(device_id)

            if active_alarm:
                telemetry["alarm_active"] = True
                telemetry["alarm_message"] = active_alarm.get("alarm_message", "Active alarm")
            else:
                if "alarm_active" not in telemetry:
                    telemetry["alarm_active"] = False

                if not telemetry.get("alarm_message"):
                    telemetry["alarm_message"] = "OK"

            if not telemetry.get("latest_telemetry_source"):
                if sources:
                    telemetry["latest_telemetry_source"] = ", ".join(sources)
                else:
                    telemetry["latest_telemetry_source"] = "none"

            return telemetry

        output = io.StringIO()

        fieldnames = [
            "client_id",
            "client_name",
            "site_id",
            "site_name",
            "building_id",
            "building_name",
            "floor_id",
            "floor_name",
            "floor_number",
            "room_id",
            "room_name",

            "device_id",
            "chip_mac",
            "dev_eui",
            "join_eui",
            "node_type",
            "label",
            "icon_type",
            "capabilities",

            "x",
            "y",

            "device_status",
            "last_seen_iso",
            "last_seen_seconds_ago",
            "local_updated_at",

            "battery_percent",
            "battery_voltage",
            "power_source",
            "battery_status",

            "signal_status",
            "gateway_id",
            "received_by_gateways",
            "rssi",
            "snr",
            "spreading_factor",
            "bandwidth",
            "frequency",

            "alarm_active",
            "alarm_message",
            "active_alarm_id",
            "active_alarm_type",
            "active_alarm_triggered_at",

            "temperature",
            "humidity",
            "co2",
            "voc",
            "motion",
            "presence",
            "water_leak",
            "gas_alarm",
            "smoke_alarm",
            "power",
            "voltage",
            "current",

            "latest_telemetry_source",
            "telemetry_json",
            "details"
        ]

        writer = SafeDictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for device in devices:
            device_id = device.get("device_id")

            client, site, building, floor, room = resolve_device_location(device)

            telemetry = get_inventory_telemetry(device_id)

            active_alarm = active_alarm_by_device.get(device_id)

            capabilities = capabilities_by_device.get(device_id, [])

            if not capabilities:
                node_type = device.get("node_type", "")

                if node_type == "multi":
                    capabilities = [
                        "environment",
                        "energy",
                        "safety",
                        "occupancy"
                    ]
                elif node_type:
                    capabilities = [node_type]

            last_seen_value = (
                telemetry.get("last_seen_iso")
                or telemetry.get("local_updated_at")
                or device.get("last_seen", "")
            )

            device_status = (
                telemetry.get("device_status")
                or device.get("status", "")
            )

            if not device_status and telemetry.get("local_updated_at"):
                device_status = "telemetry_received"

            writer.writerow({
                "client_id": client.get("id") if client else "",
                "client_name": get_name(client),

                "site_id": site.get("id") if site else "",
                "site_name": get_name(site),

                "building_id": building.get("id") if building else "",
                "building_name": get_name(building) or device.get("building", ""),

                "floor_id": floor.get("id") if floor else "",
                "floor_name": get_name(floor) or device.get("floor", ""),
                "floor_number": floor.get("floor_number") if floor else device.get("floor", ""),

                "room_id": room.get("id") if room else device.get("room_id", ""),
                "room_name": get_name(room) or device.get("room", ""),

                "device_id": device_id,
                "chip_mac": device.get("chip_mac", ""),
                "dev_eui": device.get("dev_eui", ""),
                "join_eui": device.get("join_eui", ""),
                "node_type": device.get("node_type", ""),
                "label": device.get("label", ""),
                "icon_type": device.get("icon_type", ""),
                "capabilities": ", ".join([str(item) for item in capabilities]),

                "x": device.get("x", ""),
                "y": device.get("y", ""),

                "device_status": device_status,
                "last_seen_iso": last_seen_value,
                "last_seen_seconds_ago": telemetry.get("last_seen_seconds_ago", ""),
                "local_updated_at": telemetry.get("local_updated_at", ""),

                "battery_percent": telemetry.get("battery_percent", telemetry.get("battery", "")),
                "battery_voltage": telemetry.get("battery_voltage", ""),
                "power_source": telemetry.get("power_source", ""),
                "battery_status": telemetry.get("battery_status", ""),

                "signal_status": telemetry.get("signal_status", ""),
                "gateway_id": telemetry.get("gateway_id", ""),
                "received_by_gateways": telemetry.get("received_by_gateways", ""),
                "rssi": telemetry.get("rssi", ""),
                "snr": telemetry.get("snr", ""),
                "spreading_factor": telemetry.get("spreading_factor", ""),
                "bandwidth": telemetry.get("bandwidth", ""),
                "frequency": telemetry.get("frequency", ""),

                "alarm_active": telemetry.get("alarm_active", True if active_alarm else False),
                "alarm_message": telemetry.get(
                    "alarm_message",
                    active_alarm.get("alarm_message") if active_alarm else "OK"
                ),
                "active_alarm_id": active_alarm.get("id") if active_alarm else "",
                "active_alarm_type": active_alarm.get("alarm_type") if active_alarm else "",
                "active_alarm_triggered_at": active_alarm.get("triggered_at") if active_alarm else "",

                "temperature": telemetry.get("temperature", ""),
                "humidity": telemetry.get("humidity", ""),
                "co2": telemetry.get("co2", ""),
                "voc": telemetry.get("voc", ""),
                "motion": telemetry.get("motion", ""),
                "presence": telemetry.get("presence", ""),
                "water_leak": telemetry.get("water_leak", ""),
                "gas_alarm": telemetry.get("gas_alarm", ""),
                "smoke_alarm": telemetry.get("smoke_alarm", ""),
                "power": telemetry.get("power", ""),
                "voltage": telemetry.get("voltage", ""),
                "current": telemetry.get("current", ""),

                "latest_telemetry_source": telemetry.get("latest_telemetry_source", "none"),
                "telemetry_json": json.dumps(telemetry, ensure_ascii=False, default=str),
                "details": clean_details(device)
            })

        conn.close()

        output.seek(0)

        export_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"admin_device_inventory_export_{export_date}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    finally:
        conn.close()
@router.get("/admin/summary")
def admin_summary():
    conn = db()
    try:

        def count_table(table_name):
            try:
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
                return row["c"]
            except Exception:
                return 0

        def parse_datetime_safe(value):
            if not value:
                return None

            text = str(value).strip()

            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))

                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc)
                else:
                    dt = dt.replace(tzinfo=timezone.utc)

                return dt
            except Exception:
                pass

            for fmt in [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f"
            ]:
                try:
                    dt = datetime.strptime(text.replace("Z", ""), fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            return None

        def is_true(value):
            return value in [True, "true", "True", "1", 1]

        def has_real_tb_telemetry(telemetry):
            if not telemetry:
                return False

            if telemetry.get("last_seen_ts") is not None:
                return True

            useful_keys = [
                "temperature",
                "humidity",
                "motion",
                "alarm",
                "voltage",
                "current",
                "power"
            ]

            for key in useful_keys:
                if telemetry.get(key) is not None:
                    return True

            return False

        def thingsboard_available_quick():
            global TB_TOKEN_CACHE

            if not THINGSBOARD_URL:
                return False

            try:
                response = requests.post(
                    f"{THINGSBOARD_URL}/api/auth/login",
                    json={
                        "username": TB_USERNAME,
                        "password": TB_PASSWORD
                    },
                    timeout=2
                )

                if response.status_code >= 300:
                    return False

                data = response.json()

                if data.get("token"):
                    TB_TOKEN_CACHE = data["token"]
                    return True

                return False

            except Exception:
                return False

        def read_local_latest_telemetry(device_id):
            row = conn.execute("""
            SELECT *
            FROM device_latest_telemetry
            WHERE device_id = ?
            LIMIT 1
        """, (device_id,)).fetchone()

            if not row:
                return None

            item = dict(row)

            try:
                telemetry = json.loads(item["telemetry"]) if item["telemetry"] else {}
            except Exception:
                telemetry = {}

            telemetry["alarm_active"] = bool(item["alarm_active"])
            telemetry["alarm_message"] = item["alarm_message"]
            telemetry["local_updated_at"] = item["updated_at"]

            return telemetry

        now_utc = datetime.now(timezone.utc)

        def get_local_device_status(local_telemetry):
            if not local_telemetry:
                return "offline", None

            updated_at = local_telemetry.get("local_updated_at")
            updated_dt = parse_datetime_safe(updated_at)

            if not updated_dt:
                return "offline", updated_at

            seconds_ago = (now_utc - updated_dt).total_seconds()

            if 0 <= seconds_ago <= 300 and (local_telemetry.get("alarm_message") or "").upper() != "OFFLINE":
                return "online", updated_at

            return "offline", updated_at

        total_clients = count_table("clients")
        total_sites = count_table("sites")
        total_buildings = count_table("buildings")
        total_floors = count_table("floors")
        total_rooms = count_table("rooms")

        device_rows = conn.execute("""
        SELECT device_id
        FROM devices
        ORDER BY device_id
    """).fetchall()

        active_alarm_rows = conn.execute("""
        SELECT DISTINCT device_id
        FROM alarm_history
        WHERE resolved = 0
    """).fetchall()

        active_alarm_device_ids = set()

        for row in active_alarm_rows:
            active_alarm_device_ids.add(row["device_id"])

        total_devices = len(device_rows)
        online_devices = 0
        offline_devices = 0
        last_device_seen = None
        device_summary_source = "local_database"

        tb_available = thingsboard_available_quick()

        if tb_available:
            device_summary_source = "thingsboard_with_local_fallback"

        for row in device_rows:
            device_id = row["device_id"]

            local_telemetry = read_local_latest_telemetry(device_id)
            local_status, local_last_seen = get_local_device_status(local_telemetry)

            selected_status = local_status
            selected_last_seen = local_last_seen

            if tb_available:
                try:
                    tb_telemetry = read_tb_latest_telemetry(device_id)

                    if has_real_tb_telemetry(tb_telemetry):
                        selected_status = tb_telemetry.get("device_status", selected_status)
                        selected_last_seen = tb_telemetry.get("last_seen_iso", selected_last_seen)

                except Exception:
                    pass

            if selected_last_seen:
                selected_dt = parse_datetime_safe(selected_last_seen)
                if selected_dt:
                    seconds_ago = (now_utc - selected_dt).total_seconds()
                    if seconds_ago > 300 or seconds_ago < 0:
                        selected_status = "offline"

            if selected_status == "online":
                online_devices += 1
            else:
                offline_devices += 1

            if selected_last_seen:
                selected_dt = parse_datetime_safe(selected_last_seen)

                if selected_dt:
                    if last_device_seen is None:
                        last_device_seen = selected_last_seen
                    else:
                        current_last_dt = parse_datetime_safe(last_device_seen)

                        if current_last_dt and selected_dt > current_last_dt:
                            last_device_seen = selected_last_seen

        active_alarms = len(active_alarm_device_ids)

        gateway_rows = conn.execute("""
        SELECT *
        FROM gateways
        ORDER BY gateway_id
    """).fetchall()

        total_gateways = len(gateway_rows)
        online_gateways = 0
        offline_gateways = 0
        gateway_errors = 0
        last_gateway_seen = None

        for row in gateway_rows:
            gateway = dict(row)
            status = (gateway.get("status") or "unknown").lower()

            if status == "online":
                online_gateways += 1
            elif status == "error":
                gateway_errors += 1
            else:
                offline_gateways += 1

            gateway_last_seen = gateway.get("last_seen")

            if gateway_last_seen:
                gateway_dt = parse_datetime_safe(gateway_last_seen)

                if gateway_dt:
                    if last_gateway_seen is None:
                        last_gateway_seen = gateway_last_seen
                    else:
                        current_gateway_dt = parse_datetime_safe(last_gateway_seen)

                        if current_gateway_dt and gateway_dt > current_gateway_dt:
                            last_gateway_seen = gateway_last_seen

        conn.close()

        return {
            "clients": total_clients,
            "sites": total_sites,
            "buildings": total_buildings,
            "floors": total_floors,
            "rooms": total_rooms,

            "data_sources": {
                "thingsboard_available": tb_available,
                "device_summary_source": device_summary_source,
                "gateway_summary_source": "backend_database"
            },

            "devices": {
                "total": total_devices,
                "online": online_devices,
                "offline": offline_devices,
                "active_alarms": active_alarms,
                "last_seen": last_device_seen
            },

            "gateways": {
                "total": total_gateways,
                "online": online_gateways,
                "offline": offline_gateways,
                "errors": gateway_errors,
                "last_seen": last_gateway_seen
            }
        }
    finally:
        conn.close()
