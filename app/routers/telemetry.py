import json

from fastapi import APIRouter, HTTPException

from app.db.connection import get_db_connection as db
from app.main import read_tb_latest_telemetry

router = APIRouter()

@router.get("/floor-map-data")
def floor_map_data(building: str, floor: str):
    conn = db()
    try:

        floor_row = conn.execute("""
        SELECT f.id as floor_id, f.image_path, f.image_width, f.image_height, b.name as building_name, f.name as floor_name
        FROM floors f
        JOIN buildings b ON f.building_id = b.id
        WHERE b.name = ? AND (f.name = ? OR f.floor_number = ?)
        LIMIT 1
    """, (building, floor, floor)).fetchone()

        if not floor_row:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Floorplan not found"
            )

        floor_id = floor_row["floor_id"]

        room_rows = conn.execute("""
        SELECT id, room_name, polygon_points, x, y
        FROM rooms
        WHERE floor_id = ?
        ORDER BY room_name
    """, (floor_id,)).fetchall()

        device_rows = conn.execute("""
        SELECT
            d.device_id,
            d.node_type,
            d.label,
            d.x,
            d.y,
            d.icon_type,
            r.room_name as room
        FROM devices d
        LEFT JOIN rooms r ON d.room_id = r.id
        WHERE d.floor_id = ? OR (d.floor_id IS NULL AND r.floor_id = ?)
        ORDER BY r.room_name, d.label
    """, (floor_id, floor_id)).fetchall()

        conn.close()

        rooms = []

        for row in room_rows:
            rooms.append({
                "id": row["id"],
                "room_name": row["room_name"],
                "polygon_points": json.loads(row["polygon_points"]),
                "x": row["x"],
                "y": row["y"],
            })

        devices = []

        for row in device_rows:
            telemetry = read_tb_latest_telemetry(row["device_id"])

            devices.append({
                "device_id": row["device_id"],
                "node_type": row["node_type"],
                "building": building,
                "floor": floor,
                "room": row["room"],
                "label": row["label"],
                "x": row["x"],
                "y": row["y"],
                "icon_type": row["icon_type"],
                "telemetry": telemetry,
            })

        return {
            "building": building,
            "floor": floor,
            "image_path": floor_row["image_path"],
            "image_width": floor_row["image_width"],
            "image_height": floor_row["image_height"],
            "rooms": rooms,
            "devices": devices
        }
    finally:
        conn.close()
@router.get("/api/analytics")
def get_analytics():
    from datetime import datetime, timezone

    from app.main import parse_datetime_safe

    conn = db()
    try:
        device_rows = conn.execute("SELECT device_id FROM devices").fetchall()
        total_devices = len(device_rows)
        online_devices = 0
        offline_devices = 0

        now_utc = datetime.now(timezone.utc)

        for row in device_rows:
            dev_id = row["device_id"]
            telem = conn.execute("""
                SELECT updated_at, alarm_message
                FROM device_latest_telemetry
                WHERE device_id = ?
            """, (dev_id,)).fetchone()

            is_online = False
            if telem and telem["updated_at"]:
                updated_dt = parse_datetime_safe(telem["updated_at"])
                if updated_dt:
                    seconds_ago = (now_utc - updated_dt).total_seconds()
                    if 0 <= seconds_ago <= 300 and (telem["alarm_message"] or "").upper() != "OFFLINE":
                        is_online = True

            if is_online:
                online_devices += 1
            else:
                offline_devices += 1

        cursor = conn.cursor()

        # Gateways
        cursor.execute("SELECT COUNT(*) as c FROM gateways")
        total_gateways = cursor.fetchone()['c']

        # Alarms today
        cursor.execute("SELECT COUNT(*) as c FROM alarm_history WHERE datetime(triggered_at) >= datetime('now', 'start of day')")
        alarms_today = cursor.fetchone()['c']

        return {
            "total_devices": total_devices,
            "active_devices": online_devices,
            "online_devices": online_devices,
            "offline_devices": offline_devices,
            "total_gateways": total_gateways,
            "alarms_today": alarms_today
        }
    finally:
        conn.close()
