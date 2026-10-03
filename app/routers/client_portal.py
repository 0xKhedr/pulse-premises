import io
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db.connection import get_db_connection as db
from app.main import (
    build_audit_message,
    get_local_latest_telemetry,
    get_user_access_rows,
    safe_user_dict,
    user_can_access_floor,
)
from app.services.device_state import build_device_state
from app.services.export_safety import SafeDictWriter, redact_secrets

router = APIRouter()

@router.get("/client-portal/{user_id}/allowed-floors")
def client_allowed_floors(user_id: int):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] == 0:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        access_rows = get_user_access_rows(conn, user_id)

        allowed_floors = {}

        for access in access_rows:
            query = """
            SELECT
                f.*,
                b.name AS building_name,
                s.name AS site_name,
                c.name AS client_name
            FROM floors f
            LEFT JOIN buildings b ON b.id = f.building_id
            LEFT JOIN sites s ON s.id = b.site_id
            LEFT JOIN clients c ON c.id = s.client_id
            WHERE 1 = 1
        """

            params = []

            if access["floor_id"]:
                query += " AND f.id = ?"
                params.append(access["floor_id"])

            elif access["building_id"]:
                query += " AND f.building_id = ?"
                params.append(access["building_id"])

            elif access["site_id"]:
                query += " AND b.site_id = ?"
                params.append(access["site_id"])

            elif access["client_id"]:
                query += " AND s.client_id = ?"
                params.append(access["client_id"])

            rows = conn.execute(query, params).fetchall()

            for floor in rows:
                item = dict(floor)

                item["permissions"] = {
                    "access_id": access["id"],
                    "access_level": access["access_level"],
                    "can_view_devices": access["can_view_devices"],
                    "can_view_gateways": access["can_view_gateways"],
                    "can_view_alarms": access["can_view_alarms"],
                    "can_view_telemetry": access["can_view_telemetry"],
                    "can_manage_email_settings": access["can_manage_email_settings"],
                }

                allowed_floors[item["id"]] = item

        conn.close()

        return {
            "user": safe_user_dict(user),
            "floors": list(allowed_floors.values())
        }
    finally:
        conn.close()
@router.get("/client-portal/{user_id}/floors/{floor_id}/live")
def client_floor_live(user_id: int, floor_id: int):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] == 0:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        access = user_can_access_floor(conn, user_id, floor_id)

        if not access:
            conn.close()
            raise HTTPException(status_code=403, detail="User does not have access to this floor")

        floor = conn.execute("""
        SELECT *
        FROM floors
        WHERE id = ?
    """, (floor_id,)).fetchone()

        if not floor:
            conn.close()
            raise HTTPException(status_code=404, detail="Floor not found")

        room_rows = conn.execute("""
       SELECT *
       FROM rooms
       WHERE floor_id = ?
       ORDER BY room_name
       """, (floor_id,)).fetchall()

        rooms = []

        for room in room_rows:
            room_dict = dict(room)

            device_rows = conn.execute("""
            SELECT *
            FROM devices
            WHERE room_id = ?
            ORDER BY label
        """, (room["id"],)).fetchall()

            devices = []

            for d in device_rows:
          
              device = redact_secrets(dict(d))

              local_telemetry = get_local_latest_telemetry(conn, device["device_id"])

              if local_telemetry:
              
                 device["telemetry"] = local_telemetry

              device.update(
                  build_device_state(
                      device,
                      stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
                      offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
                  )
              )

              devices.append(device)

            room_dict["devices"] = devices
            rooms.append(room_dict)

        floor_devices = []
        if access["can_view_devices"]:
            floor_device_rows = conn.execute("""
            SELECT *
            FROM devices
            WHERE floor_id = ? AND room_id IS NULL
            ORDER BY label
        """, (floor_id,)).fetchall()

            for row in floor_device_rows:
                device = redact_secrets(dict(row))
                local_telemetry = get_local_latest_telemetry(
                    conn,
                    device["device_id"],
                )
                if local_telemetry:
                    device["telemetry"] = local_telemetry
                device.update(
                    build_device_state(
                        device,
                        stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
                        offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
                    )
                )
                floor_devices.append(device)

        gateways = []

        if access["can_view_gateways"]:
            gateway_rows = conn.execute("""
            SELECT *
            FROM gateways
            WHERE floor_id = ?
            ORDER BY name
        """, (floor_id,)).fetchall()

            gateways = [dict(g) for g in gateway_rows]

        conn.close()

        return {
            "user": safe_user_dict(user),
            "permissions": {
                "access_level": access["access_level"],
                "can_view_devices": access["can_view_devices"],
                "can_view_gateways": access["can_view_gateways"],
                "can_view_alarms": access["can_view_alarms"],
                "can_view_telemetry": access["can_view_telemetry"],
                "can_manage_email_settings": access["can_manage_email_settings"],
            },
            "floor": dict(floor),
            "rooms": rooms if access["can_view_devices"] else [],
            "floor_devices": floor_devices,
            "gateways": gateways
        }
    finally:
        conn.close()
@router.get("/client-portal/{user_id}/allowed-structure")
def client_allowed_structure(user_id: int):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] == 0:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        access_rows = get_user_access_rows(conn, user_id)

        structure = {}
        added_floor_ids = set()

        for access in access_rows:
            query = """
            SELECT
                c.id AS client_id,
                c.name AS client_name,

                s.id AS site_id,
                s.name AS site_name,

                b.id AS building_id,
                b.name AS building_name,

                f.id AS floor_id,
                f.name AS floor_name,
                f.floor_number AS floor_number,
                f.image_path AS image_path,
                f.image_width AS image_width,
                f.image_height AS image_height
            FROM floors f
            LEFT JOIN buildings b ON b.id = f.building_id
            LEFT JOIN sites s ON s.id = b.site_id
            LEFT JOIN clients c ON c.id = s.client_id
            WHERE 1 = 1
        """

            params = []

            if access["floor_id"]:
                query += " AND f.id = ?"
                params.append(access["floor_id"])

            elif access["building_id"]:
                query += " AND b.id = ?"
                params.append(access["building_id"])

            elif access["site_id"]:
                query += " AND s.id = ?"
                params.append(access["site_id"])

            elif access["client_id"]:
                query += " AND c.id = ?"
                params.append(access["client_id"])

            rows = conn.execute(query, params).fetchall()

            for row in rows:
                r = dict(row)

                client_id = r["client_id"]
                site_id = r["site_id"]
                building_id = r["building_id"]
                floor_id = r["floor_id"]
                if floor_id in added_floor_ids:

                    continue

                added_floor_ids.add(floor_id)

                if client_id not in structure:
                    structure[client_id] = {
                        "id": client_id,
                        "name": r["client_name"],
                        "sites": {}
                    }

                if site_id not in structure[client_id]["sites"]:
                    structure[client_id]["sites"][site_id] = {
                        "id": site_id,
                        "name": r["site_name"],
                        "buildings": {}
                    }

                if building_id not in structure[client_id]["sites"][site_id]["buildings"]:
                    structure[client_id]["sites"][site_id]["buildings"][building_id] = {
                        "id": building_id,
                        "name": r["building_name"],
                        "floors": []
                    }

                structure[client_id]["sites"][site_id]["buildings"][building_id]["floors"].append({
                    "id": floor_id,
                    "name": r["floor_name"],
                    "floor_number": r["floor_number"],
                    "image_path": r["image_path"],
                    "image_width": r["image_width"],
                    "image_height": r["image_height"],
                    "permissions": {
                        "access_id": access["id"],
                        "access_level": access["access_level"],
                        "can_view_devices": access["can_view_devices"],
                        "can_view_gateways": access["can_view_gateways"],
                        "can_view_alarms": access["can_view_alarms"],
                        "can_view_telemetry": access["can_view_telemetry"],
                        "can_manage_email_settings": access["can_manage_email_settings"],
                    }
                })

        final_structure = []

        for client in structure.values():
            sites_list = []

            for site in client["sites"].values():
                buildings_list = []

                for building in site["buildings"].values():
                    buildings_list.append(building)

                site["buildings"] = buildings_list
                sites_list.append(site)

            client["sites"] = sites_list
            final_structure.append(client)

        conn.close()

        return {
            "user": safe_user_dict(user),
            "structure": final_structure
        }
    finally:
        conn.close()
@router.get("/client-portal/{user_id}/devices/{device_id}/latest-telemetry")
def client_device_latest_telemetry(user_id: int, device_id: str):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] == 0:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        device = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        if not device:
            conn.close()
            raise HTTPException(status_code=404, detail="Device not found")

        device_dict = dict(device)

        floor_id = device_dict.get("floor_id")

        if not floor_id and device_dict.get("room_id"):
            room = conn.execute("""
            SELECT *
            FROM rooms
            WHERE id = ?
        """, (device_dict["room_id"],)).fetchone()

            if room:
                room_dict = dict(room)
                floor_id = room_dict.get("floor_id") or room_dict.get("floorplan_id")

        if not floor_id:
            conn.close()
            raise HTTPException(status_code=403, detail="Device is not linked to a floor")

        access = user_can_access_floor(conn, user_id, floor_id)

        if not access:
            conn.close()
            raise HTTPException(status_code=403, detail="User does not have access to this device")

        if access["can_view_telemetry"] == 0:
            conn.close()
            raise HTTPException(status_code=403, detail="User is not allowed to view telemetry")

        row = conn.execute("""
        SELECT *
        FROM device_latest_telemetry
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        conn.close()

        if not row:
            return {
                "device_id": device_id,
                "telemetry": {},
                "alarm_active": 0,
                "alarm_message": "No telemetry received yet",
                "updated_at": None
            }

        item = dict(row)

        try:
            telemetry = json.loads(item["telemetry"]) if item["telemetry"] else {}
        except Exception:
            telemetry = {}

        return {
            "device_id": device_id,
            "telemetry": telemetry,
            "alarm_active": item["alarm_active"],
            "alarm_message": item["alarm_message"],
            "updated_at": item["updated_at"]
        }
    finally:
        conn.close()
@router.get("/client-portal/{user_id}/alarms/export.csv")
def client_portal_alarm_history_export_csv(
    user_id: int,
    limit: int = 1000,
    device_id: str = None,
    node_type: str = None,
    alarm_type: str = None,
    acknowledged: int = None,
    resolved: int = None,
    building_id: int = None,
    floor_id: int = None,
    room_id: int = None,
    search: str = None,
    from_date: str = None,
    to_date: str = None
):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] != 1:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        access_rows = conn.execute("""
        SELECT *
        FROM user_access
        WHERE user_id = ?
          AND can_view_alarms = 1
    """, (user_id,)).fetchall()

        if not access_rows:
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="User does not have alarm export permission"
            )

        where_clauses = []
        params = []

        scope_clauses = []
        scope_params = []

        for access in access_rows:
            access_client_id = access["client_id"]
            access_site_id = access["site_id"]
            access_building_id = access["building_id"]
            access_floor_id = access["floor_id"]

            if access_floor_id:
                scope_clauses.append("f.id = ?")
                scope_params.append(access_floor_id)

            elif access_building_id:
                scope_clauses.append("f.building_id = ?")
                scope_params.append(access_building_id)

            elif access_site_id:
                scope_clauses.append("b.site_id = ?")
                scope_params.append(access_site_id)

            elif access_client_id:
                scope_clauses.append("s.client_id = ?")
                scope_params.append(access_client_id)

        if not scope_clauses:
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="No valid alarm export scope found"
            )

        where_clauses.append("(" + " OR ".join(scope_clauses) + ")")
        params.extend(scope_params)

        def add_alarm_filter(column_name, value):
            if value is not None and value != "":
                where_clauses.append(f"ah.{column_name} = ?")
                params.append(value)

        add_alarm_filter("device_id", device_id)
        add_alarm_filter("node_type", node_type)
        add_alarm_filter("alarm_type", alarm_type)
        add_alarm_filter("acknowledged", acknowledged)
        add_alarm_filter("resolved", resolved)

        if building_id is not None:
            where_clauses.append("f.building_id = ?")
            params.append(building_id)

        if floor_id is not None:
            where_clauses.append("f.id = ?")
            params.append(floor_id)

        if room_id is not None:
            where_clauses.append("d.room_id = ?")
            params.append(room_id)

        if search:
            where_clauses.append("""
            (
                ah.device_id LIKE ?
                OR ah.node_type LIKE ?
                OR ah.building LIKE ?
                OR ah.floor LIKE ?
                OR ah.room LIKE ?
                OR ah.alarm_type LIKE ?
                OR ah.alarm_message LIKE ?
                OR ah.telemetry LIKE ?
            )
        """)

            search_value = f"%{search}%"

            params.extend([
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            ])

        if from_date:
            where_clauses.append("ah.triggered_at >= ?")
            params.append(from_date)

        if to_date:
            where_clauses.append("ah.triggered_at <= ?")

            if len(to_date) == 10:
                params.append(to_date + " 23:59:59")
            else:
                params.append(to_date)

        if limit < 1:
            limit = 1000

        limit = min(limit, 5000)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        rows = conn.execute(f"""
        SELECT
            ah.*,
            s.client_id AS scope_client_id,
            b.site_id AS scope_site_id,
            f.building_id AS scope_building_id,
            f.id AS scope_floor_id,
            d.room_id AS scope_room_id
        FROM alarm_history ah
        LEFT JOIN devices d ON ah.device_id = d.device_id
        LEFT JOIN rooms r ON d.room_id = r.id
        LEFT JOIN floors f ON f.id = COALESCE(d.floor_id, r.floor_id)
        LEFT JOIN buildings b ON f.building_id = b.id
        LEFT JOIN sites s ON b.site_id = s.id
        {where_sql}
        ORDER BY datetime(ah.triggered_at) DESC, ah.id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

        output = io.StringIO()

        fieldnames = [
            "id",
            "device_id",
            "node_type",
            "building",
            "floor",
            "room",
            "alarm_type",
            "alarm_message",
            "triggered_at",
            "acknowledged",
            "acknowledged_by",
            "acknowledged_at",
            "resolved",
            "resolved_by",
            "resolved_at",
            "client_id",
            "site_id",
            "building_id",
            "floor_id",
            "room_id",
            "telemetry"
        ]

        writer = SafeDictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            item = dict(row)

            try:
                telemetry_obj = json.loads(item["telemetry"]) if item.get("telemetry") else {}
                telemetry_text = json.dumps(telemetry_obj, ensure_ascii=False)
            except Exception:
                telemetry_text = item.get("telemetry") or ""

            writer.writerow({
                "id": item.get("id"),
                "device_id": item.get("device_id"),
                "node_type": item.get("node_type"),
                "building": item.get("building"),
                "floor": item.get("floor"),
                "room": item.get("room"),
                "alarm_type": item.get("alarm_type"),
                "alarm_message": item.get("alarm_message"),
                "triggered_at": item.get("triggered_at"),
                "acknowledged": item.get("acknowledged"),
                "acknowledged_by": item.get("acknowledged_by"),
                "acknowledged_at": item.get("acknowledged_at"),
                "resolved": item.get("resolved"),
                "resolved_by": item.get("resolved_by"),
                "resolved_at": item.get("resolved_at"),
                "client_id": item.get("scope_client_id"),
                "site_id": item.get("scope_site_id"),
                "building_id": item.get("scope_building_id"),
                "floor_id": item.get("scope_floor_id"),
                "room_id": item.get("scope_room_id"),
                "telemetry": telemetry_text
            })

        conn.close()

        output.seek(0)

        export_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"client_alarm_history_user_{user_id}_{export_date}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    finally:
        conn.close()
@router.get("/client-portal/{user_id}/export/full-structure.csv")
def client_portal_full_structure_export_csv(user_id: int):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] != 1:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        access_rows = conn.execute("""
        SELECT *
        FROM user_access
        WHERE user_id = ?
    """, (user_id,)).fetchall()

        if not access_rows:
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="User has no allowed export scope"
            )

        def rows_to_dicts(query, params=()):
            return [dict(row) for row in conn.execute(query, params).fetchall()]

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

        allowed_client_ids = set()
        allowed_site_ids = set()
        allowed_building_ids = set()
        allowed_floor_ids = set()
        allowed_room_ids = set()
        allowed_device_ids = set()
        allowed_gateway_ids = set()

        def add_client_scope(client_id):
            if not client_id:
                return

            allowed_client_ids.add(client_id)

            for site in sites:
                if site.get("client_id") == client_id:
                    add_site_scope(site.get("id"))

        def add_site_scope(site_id):
            if not site_id:
                return

            allowed_site_ids.add(site_id)

            site = sites_by_id.get(site_id)
            if site and site.get("client_id"):
                allowed_client_ids.add(site.get("client_id"))

            for building in buildings:
                if building.get("site_id") == site_id:
                    add_building_scope(building.get("id"))

        def add_building_scope(building_id):
            if not building_id:
                return

            allowed_building_ids.add(building_id)

            building = buildings_by_id.get(building_id)
            if building and building.get("site_id"):
                site_id = building.get("site_id")
                allowed_site_ids.add(site_id)

                site = sites_by_id.get(site_id)
                if site and site.get("client_id"):
                    allowed_client_ids.add(site.get("client_id"))

            for floor in floors:
                if floor.get("building_id") == building_id:
                    add_floor_scope(floor.get("id"))

        def add_floor_scope(floor_id):
            if not floor_id:
                return

            allowed_floor_ids.add(floor_id)

            floor = floors_by_id.get(floor_id)
            if floor and floor.get("building_id"):
                building_id = floor.get("building_id")
                allowed_building_ids.add(building_id)

                building = buildings_by_id.get(building_id)
                if building and building.get("site_id"):
                    site_id = building.get("site_id")
                    allowed_site_ids.add(site_id)

                    site = sites_by_id.get(site_id)
                    if site and site.get("client_id"):
                        allowed_client_ids.add(site.get("client_id"))

            for room in rooms:
                if room.get("floor_id") == floor_id:
                    allowed_room_ids.add(room.get("id"))

        def device_is_in_allowed_scope(device):
            room_id = device.get("room_id")

            if room_id and room_id in allowed_room_ids:
                return True

            floor_id = device.get("floor_id")
            if floor_id and floor_id in allowed_floor_ids:
                return True

            building_id = device.get("building_id")
            if building_id and building_id in allowed_building_ids:
                return True

            site_id = device.get("site_id")
            if site_id and site_id in allowed_site_ids:
                return True

            client_id = device.get("client_id")
            if client_id and client_id in allowed_client_ids:
                return True

            if room_id:
                room = rooms_by_id.get(room_id)
                if room and room.get("floor_id") in allowed_floor_ids:
                    return True

            return False

        def gateway_is_in_allowed_scope(gateway):
            floor_id = gateway.get("floor_id")

            if floor_id and floor_id in allowed_floor_ids:
                return True

            building_id = gateway.get("building_id")
            if building_id and building_id in allowed_building_ids:
                return True

            site_id = gateway.get("site_id")
            if site_id and site_id in allowed_site_ids:
                return True

            client_id = gateway.get("client_id")
            if client_id and client_id in allowed_client_ids:
                return True

            if floor_id:
                floor = floors_by_id.get(floor_id)
                if floor and floor.get("building_id") in allowed_building_ids:
                    return True

            return False

        for access in access_rows:
            if access["floor_id"]:
                add_floor_scope(access["floor_id"])

            elif access["building_id"]:
                add_building_scope(access["building_id"])

            elif access["site_id"]:
                add_site_scope(access["site_id"])

            elif access["client_id"]:
                add_client_scope(access["client_id"])

        for access in access_rows:
            can_view_devices = access["can_view_devices"] == 1
            can_view_gateways = access["can_view_gateways"] == 1

            temp_client_ids = set()
            temp_site_ids = set()
            temp_building_ids = set()
            temp_floor_ids = set()
            temp_room_ids = set()

            if access["floor_id"]:
                temp_floor_ids.add(access["floor_id"])

            elif access["building_id"]:
                temp_building_ids.add(access["building_id"])

                for floor in floors:
                    if floor.get("building_id") == access["building_id"]:
                        temp_floor_ids.add(floor.get("id"))

            elif access["site_id"]:
                temp_site_ids.add(access["site_id"])

                for building in buildings:
                    if building.get("site_id") == access["site_id"]:
                        temp_building_ids.add(building.get("id"))

                for floor in floors:
                    if floor.get("building_id") in temp_building_ids:
                        temp_floor_ids.add(floor.get("id"))

            elif access["client_id"]:
                temp_client_ids.add(access["client_id"])

                for site in sites:
                    if site.get("client_id") == access["client_id"]:
                        temp_site_ids.add(site.get("id"))

                for building in buildings:
                    if building.get("site_id") in temp_site_ids:
                        temp_building_ids.add(building.get("id"))

                for floor in floors:
                    if floor.get("building_id") in temp_building_ids:
                        temp_floor_ids.add(floor.get("id"))

            for room in rooms:
                if room.get("floor_id") in temp_floor_ids:
                    temp_room_ids.add(room.get("id"))

            if can_view_devices:
                for device in devices:
                    device_room_id = device.get("room_id")
                    device_floor_id = device.get("floor_id")
                    device_building_id = device.get("building_id")
                    device_site_id = device.get("site_id")
                    device_client_id = device.get("client_id")

                    if (
                        device_room_id in temp_room_ids
                        or device_floor_id in temp_floor_ids
                        or device_building_id in temp_building_ids
                        or device_site_id in temp_site_ids
                        or device_client_id in temp_client_ids
                    ):
                        allowed_device_ids.add(device.get("device_id"))

            if can_view_gateways:
                for gateway in gateways:
                    gateway_floor_id = gateway.get("floor_id")
                    gateway_building_id = gateway.get("building_id")
                    gateway_site_id = gateway.get("site_id")
                    gateway_client_id = gateway.get("client_id")

                    if (
                        gateway_floor_id in temp_floor_ids
                        or gateway_building_id in temp_building_ids
                        or gateway_site_id in temp_site_ids
                        or gateway_client_id in temp_client_ids
                    ):
                        allowed_gateway_ids.add(gateway.get("gateway_id"))

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

        for client in clients:
            if client.get("id") not in allowed_client_ids:
                continue

            write_row(
                record_type="CLIENT",
                client=client,
                asset_type="client",
                asset_id=client.get("id"),
                asset_name=get_name(client),
                created_at=client.get("created_at", ""),
                details=client
            )

        for site in sites:
            if site.get("id") not in allowed_site_ids:
                continue

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

        for building in buildings:
            if building.get("id") not in allowed_building_ids:
                continue

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

        for floor in floors:
            if floor.get("id") not in allowed_floor_ids:
                continue

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

        for room in rooms:
            if room.get("id") not in allowed_room_ids:
                continue

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

        for device in devices:
            if device.get("device_id") not in allowed_device_ids:
                continue

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

        for gateway in gateways:
            if gateway.get("gateway_id") not in allowed_gateway_ids:
                continue

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
        filename = f"client_full_structure_user_{user_id}_{export_date}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    finally:
        conn.close()
@router.get("/client-portal/{user_id}/activity-log")
def client_portal_activity_log(
    user_id: int,
    limit: int = 100,
    action: str = None,
    target_type: str = None,
    device_id: str = None,
    gateway_id: str = None,
    floor_id: int = None,
    building_id: int = None,
    search: str = None,
    from_date: str = None,
    to_date: str = None
):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        if user["enabled"] != 1:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        access_rows = conn.execute("""
        SELECT *
        FROM user_access
        WHERE user_id = ?
    """, (user_id,)).fetchall()

        if not access_rows:
            conn.close()
            return []

        allowed_actions = set()

        for access in access_rows:
            if access["can_view_alarms"]:
                allowed_actions.add("acknowledge_alarm")
                allowed_actions.add("resolve_alarm")

            if access["can_view_devices"]:
                allowed_actions.add("move_device")

            if access["can_view_gateways"]:
                allowed_actions.add("move_gateway")
                allowed_actions.add("update_gateway")
                allowed_actions.add("create_gateway")

        if not allowed_actions:
            conn.close()
            return []

        where_clauses = []
        params = []

        action_placeholders = ",".join(["?"] * len(allowed_actions))

        where_clauses.append(f"""
        action IN ({action_placeholders})
    """)

        params.extend(list(allowed_actions))

        scope_clauses = []
        scope_params = []

        for access in access_rows:
            access_client_id = access["client_id"]
            access_site_id = access["site_id"]
            access_building_id = access["building_id"]
            access_floor_id = access["floor_id"]

            if access_floor_id:
                scope_clauses.append("""
                floor_id = ?
            """)
                scope_params.append(access_floor_id)

            elif access_building_id:
                scope_clauses.append("""
                (
                    building_id = ?
                    OR floor_id IN (
                        SELECT id
                        FROM floors
                        WHERE building_id = ?
                    )
                )
            """)
                scope_params.extend([
                    access_building_id,
                    access_building_id
                ])

            elif access_site_id:
                scope_clauses.append("""
                (
                    site_id = ?
                    OR building_id IN (
                        SELECT id
                        FROM buildings
                        WHERE site_id = ?
                    )
                    OR floor_id IN (
                        SELECT f.id
                        FROM floors f
                        JOIN buildings b ON f.building_id = b.id
                        WHERE b.site_id = ?
                    )
                )
            """)
                scope_params.extend([
                    access_site_id,
                    access_site_id,
                    access_site_id
                ])

            elif access_client_id:
                scope_clauses.append("""
                (
                    client_id = ?
                    OR site_id IN (
                        SELECT id
                        FROM sites
                        WHERE client_id = ?
                    )
                    OR building_id IN (
                        SELECT b.id
                        FROM buildings b
                        JOIN sites s ON b.site_id = s.id
                        WHERE s.client_id = ?
                    )
                    OR floor_id IN (
                        SELECT f.id
                        FROM floors f
                        JOIN buildings b ON f.building_id = b.id
                        JOIN sites s ON b.site_id = s.id
                        WHERE s.client_id = ?
                    )
                )
            """)
                scope_params.extend([
                    access_client_id,
                    access_client_id,
                    access_client_id,
                    access_client_id
                ])

        if not scope_clauses:
            conn.close()
            return []

        where_clauses.append("(" + " OR ".join(scope_clauses) + ")")
        params.extend(scope_params)

        def add_filter(column_name, value):
            if value is not None and value != "":
                where_clauses.append(f"{column_name} = ?")
                params.append(value)

        add_filter("action", action)
        add_filter("target_type", target_type)
        add_filter("device_id", device_id)
        add_filter("gateway_id", gateway_id)
        add_filter("floor_id", floor_id)
        add_filter("building_id", building_id)

        if search:
            where_clauses.append("""
            (
                action LIKE ?
                OR target_type LIKE ?
                OR target_id LIKE ?
                OR message LIKE ?
                OR details LIKE ?
            )
        """)

            search_value = f"%{search}%"

            params.extend([
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            ])

        if from_date:
            where_clauses.append("created_at >= ?")
            params.append(from_date)

        if to_date:
            where_clauses.append("created_at <= ?")

            if len(to_date) == 10:
                params.append(to_date + " 23:59:59")
            else:
                params.append(to_date)

        if limit < 1:
            limit = 100

        limit = min(limit, 300)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        rows = conn.execute(f"""
        SELECT *
        FROM audit_log
        {where_sql}
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

        conn.close()

        result = []

        for row in rows:
            item = dict(row)

            try:
                item["details"] = json.loads(item["details"]) if item["details"] else {}
            except Exception:
                item["details"] = {}
            item["details"] = redact_secrets(item["details"])

            if not item.get("message"):
                item["message"] = build_audit_message(
                    action=item.get("action"),
                    actor=item.get("actor"),
                    target_type=item.get("target_type"),
                    target_id=item.get("target_id"),
                    details=item.get("details")
                )

            item["timestamp"] = item.get("created_at")

            result.append(item)

        return result
    finally:
        conn.close()
