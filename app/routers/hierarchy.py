import logging

logger = logging.getLogger(__name__)

import json
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.db.connection import get_db_connection as db
from app.main import (
    UPLOAD_DIR,
    build_profile_display_telemetry,
    build_profile_live_field_metadata,
    build_profile_live_metadata,
    delete_by_ids,
    get_device_capabilities_list,
    get_floorplan_ids_for_floors_and_rooms,
    get_ids,
    get_local_latest_telemetry,
    get_room_ids_for_floors,
    load_device_profile_for_live_telemetry,
    merge_profile_live_telemetry_sources,
    read_tb_latest_telemetry,
    safe_unassign_gateways,
)
from app.services.device_state import build_device_state
from app.services.export_safety import redact_secrets
from app.services.uploads import save_image_securely

router = APIRouter()

@router.post("/floors/{floor_id}/upload-image")
async def upload_floor_image(
    floor_id: int,
    image: UploadFile = File(...)
):
    conn = db()
    try:

        floor = conn.execute("""
        SELECT *
        FROM floors
        WHERE id = ?
    """, (floor_id,)).fetchone()

        if not floor:
            conn.close()
            raise HTTPException(status_code=404, detail="Floor not found")

        filename, width, height = await save_image_securely(image, UPLOAD_DIR, prefix=f"floor_{floor_id}")
        image_path = f"/uploads/{filename}"

        conn.execute("""
        UPDATE floors
        SET image_path = ?,
            image_width = ?,
            image_height = ?
        WHERE id = ?
    """, (
            image_path,
            width,
            height,
            floor_id,
        ))

        conn.commit()
        conn.close()

        return {
            "status": "uploaded",
            "floor_id": floor_id,
            "image_path": image_path,
            "image_width": width,
            "image_height": height,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/floorplans/upload")
async def upload_floorplan(
    building: str = Form(...),
    floor: str = Form(...),
    image: UploadFile = File(...)
):
    safe_building = re.sub(r"[^a-zA-Z0-9_-]", "_", building)
    safe_floor = re.sub(r"[^a-zA-Z0-9_-]", "_", floor)
    prefix = f"{safe_building}_floor_{safe_floor}"

    filename, width, height = await save_image_securely(image, UPLOAD_DIR, prefix=prefix)
    image_path = f"/uploads/{filename}"

    conn = db()
    try:
        cur = conn.execute(
            """
        INSERT INTO floorplans (
            building,
            floor,
            image_path,
            image_width,
            image_height
        ) VALUES (?, ?, ?, ?, ?)
        """,
            (
                building,
                floor,
                image_path,
                width,
                height,
            ),
        )
        conn.commit()
        floorplan_id = cur.lastrowid
        conn.close()

        return {
            "status": "uploaded",
            "floorplan_id": floorplan_id,
            "building": building,
            "floor": floor,
            "image_path": image_path,
            "image_width": width,
            "image_height": height,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/floorplans")
def get_floorplans():
    conn = db()
    try:

        rows = conn.execute("""
        SELECT id, building, floor, image_path, image_width, image_height, created_at
        FROM floorplans
        ORDER BY id DESC
    """).fetchall()

        conn.close()

        return [
            {
                "id": row["id"],
                "building": row["building"],
                "floor": row["floor"],
                "image_path": row["image_path"],
                "image_width": row["image_width"],
                "image_height": row["image_height"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()
@router.post("/rooms")
def create_room(data: dict):
    required_fields = [
        "floor_id",
        "room_name",
        "polygon_points",
    ]

    for field in required_fields:
        if field not in data:
            raise HTTPException(
                status_code=400,
                detail=f"Missing field: {field}"
            )

    polygon_points = data["polygon_points"]

    if not isinstance(polygon_points, list) or len(polygon_points) < 3:
        raise HTTPException(
            status_code=400,
            detail="polygon_points must contain at least 3 points"
        )

    try:
        x_values = [p["x"] for p in polygon_points]
        y_values = [p["y"] for p in polygon_points]

        center_x = int(sum(x_values) / len(x_values))
        center_y = int(sum(y_values) / len(y_values))

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid polygon_points format"
        )

    conn = db()
    try:

        cur = conn.execute(
            """
        INSERT INTO rooms (
            floor_id,
            room_name,
            polygon_points,
            x,
            y
        ) VALUES (?, ?, ?, ?, ?)
        """,
            (
                data["floor_id"],
                data["room_name"],
                json.dumps(polygon_points),
                center_x,
                center_y,
            ),
        )

        conn.commit()

        room_id = cur.lastrowid

        conn.close()

        return {
            "status": "created",
            "room_id": room_id,
            "floor_id": data["floor_id"],
            "room_name": data["room_name"],
            "polygon_points": polygon_points,
            "x": center_x,
            "y": center_y,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/rooms")
def get_rooms(floor_id: int | None = None):
    conn = db()
    try:

        if floor_id is not None:
            rows = conn.execute("""
            SELECT id, floor_id, room_name, polygon_points, x, y, created_at
            FROM rooms
            WHERE floor_id = ?
            ORDER BY id DESC
        """, (floor_id,)).fetchall()
        else:
            rows = conn.execute("""
            SELECT id, floor_id, room_name, polygon_points, x, y, created_at
            FROM rooms
            ORDER BY id DESC
        """).fetchall()

        conn.close()

        return [
            {
                "id": row["id"],
                "floor_id": row["floor_id"],
                "room_name": row["room_name"],
                "polygon_points": json.loads(row["polygon_points"]),
                "x": row["x"],
                "y": row["y"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()
@router.post("/sites")
def create_site(data: dict):
    client_id = data.get("client_id")
    name = data.get("name", "").strip()

    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")

    if not name:
        raise HTTPException(status_code=400, detail="Site name required")

    conn = db()
    try:

        cur = conn.execute("""
        INSERT INTO sites(
            client_id,
            name
        )
        VALUES (?, ?)
    """, (
            client_id,
            name,
        ))

        conn.commit()

        site_id = cur.lastrowid

        conn.close()

        return {
            "status": "created",
            "site_id": site_id,
            "client_id": client_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/sites")
def get_sites(client_id: int | None = None):
    conn = db()
    try:

        if client_id is not None:
            rows = conn.execute("""
            SELECT *
            FROM sites
            WHERE client_id = ?
            ORDER BY id DESC
        """, (client_id,)).fetchall()

        else:
            rows = conn.execute("""
            SELECT *
            FROM sites
            ORDER BY id DESC
        """).fetchall()

        conn.close()

        return [dict(r) for r in rows]
    finally:
        conn.close()
@router.post("/buildings")
def create_building(data: dict):
    site_id = data.get("site_id")
    name = data.get("name", "").strip()

    if not site_id:
        raise HTTPException(status_code=400, detail="site_id required")

    if not name:
        raise HTTPException(status_code=400, detail="Building name required")

    conn = db()
    try:

        cur = conn.execute("""
        INSERT INTO buildings(
            site_id,
            name,
            polygon_points,
            x,
            y
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
            site_id,
            name,
            json.dumps(data.get("polygon_points", [])),
            data.get("x"),
            data.get("y"),
        ))

        conn.commit()

        building_id = cur.lastrowid

        conn.close()

        return {
            "status": "created",
            "building_id": building_id,
            "site_id": site_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/buildings")
def get_buildings(site_id: int | None = None):
    conn = db()
    try:

        if site_id is not None:
            rows = conn.execute("""
            SELECT *
            FROM buildings
            WHERE site_id = ?
            ORDER BY id DESC
        """, (site_id,)).fetchall()

        else:
            rows = conn.execute("""
            SELECT *
            FROM buildings
            ORDER BY id DESC
        """).fetchall()

        conn.close()

        result = []

        for r in rows:
            item = dict(r)

            if item.get("polygon_points"):
                item["polygon_points"] = json.loads(item["polygon_points"])
            else:
                item["polygon_points"] = []

            result.append(item)

        return result
    finally:
        conn.close()
@router.post("/floors")
def create_floor(data: dict):
    building_id = data.get("building_id")

    name = data.get("name", "").strip()

    if not building_id:
        raise HTTPException(status_code=400, detail="building_id required")

    if not name:
        raise HTTPException(status_code=400, detail="Floor name required")

    conn = db()
    try:

        cur = conn.execute("""
        INSERT INTO floors(
            building_id,
            name,
            floor_number,
            image_path,
            image_width,
            image_height
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
            building_id,
            name,
            data.get("floor_number"),
            data.get("image_path"),
            data.get("image_width"),
            data.get("image_height"),
        ))

        conn.commit()

        floor_id = cur.lastrowid

        conn.close()

        return {
            "status": "created",
            "floor_id": floor_id,
            "building_id": building_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/floors")
def get_floors(building_id: int | None = None):
    conn = db()
    try:

        if building_id is not None:
            rows = conn.execute("""
            SELECT f.*, b.name AS building_name,
                   s.id AS site_id, s.name AS site_name,
                   c.id AS client_id, c.name AS client_name
            FROM floors f
            JOIN buildings b ON b.id = f.building_id
            JOIN sites s ON s.id = b.site_id
            JOIN clients c ON c.id = s.client_id
            WHERE f.building_id = ?
            ORDER BY c.name, s.name, b.name, f.floor_number, f.name
        """, (building_id,)).fetchall()

        else:
            rows = conn.execute("""
            SELECT f.*, b.name AS building_name,
                   s.id AS site_id, s.name AS site_name,
                   c.id AS client_id, c.name AS client_name
            FROM floors f
            JOIN buildings b ON b.id = f.building_id
            JOIN sites s ON s.id = b.site_id
            JOIN clients c ON c.id = s.client_id
            ORDER BY c.name, s.name, b.name, f.floor_number, f.name
        """).fetchall()

        conn.close()

        return [dict(r) for r in rows]
    finally:
        conn.close()
@router.delete("/sites/{site_id}")
def delete_site(site_id: int):
    conn = db()
    try:

        site = conn.execute("""
        SELECT *
        FROM sites
        WHERE id = ?
    """, (site_id,)).fetchone()

        if not site:
            conn.close()
            raise HTTPException(status_code=404, detail="Site not found")

        building_ids = get_ids(conn, """
        SELECT id
        FROM buildings
        WHERE site_id = ?
    """, (site_id,))

        floor_ids = []

        if building_ids:
            placeholders = ",".join("?" for _ in building_ids)
            floor_ids = get_ids(conn, f"""
            SELECT id
            FROM floors
            WHERE building_id IN ({placeholders})
        """, building_ids)

        room_ids = get_room_ids_for_floors(conn, floor_ids)
        floorplan_ids = get_floorplan_ids_for_floors_and_rooms(conn, floor_ids, room_ids)

        devices_count = conn.execute("""
        SELECT COUNT(*)
        FROM devices
        WHERE site_id = ?
    """, (site_id,)).fetchone()[0]

        conn.execute("""
        UPDATE devices
        SET is_placed = 0,
            site_id = NULL,
            building_id = NULL,
            floor_id = NULL,
            room_id = NULL,
            building = NULL,
            floor = NULL,
            room = NULL,
            x = NULL,
            y = NULL
        WHERE site_id = ?
    """, (site_id,))

        gateways_unassigned = safe_unassign_gateways(
            conn,
            site_ids=[site_id],
            building_ids=building_ids,
            floor_ids=floor_ids,
            clear_site=True,
            clear_building=True,
            clear_floor=True
        )

        deleted_user_access = conn.execute("""
        DELETE FROM user_access
        WHERE site_id = ?
    """, (site_id,)).rowcount

        if building_ids:
            placeholders = ",".join("?" for _ in building_ids)
            deleted_user_access += conn.execute(f"""
            DELETE FROM user_access
            WHERE building_id IN ({placeholders})
        """, building_ids).rowcount

        if floor_ids:
            placeholders = ",".join("?" for _ in floor_ids)
            deleted_user_access += conn.execute(f"""
            DELETE FROM user_access
            WHERE floor_id IN ({placeholders})
        """, floor_ids).rowcount

        deleted_site_maps = conn.execute("""
        DELETE FROM site_maps
        WHERE site_id = ?
    """, (site_id,)).rowcount

        deleted_rooms = delete_by_ids(conn, "rooms", room_ids)
        deleted_floorplans = delete_by_ids(conn, "floorplans", floorplan_ids)
        deleted_floors = delete_by_ids(conn, "floors", floor_ids)
        deleted_buildings = delete_by_ids(conn, "buildings", building_ids)

        conn.execute("""
        DELETE FROM sites
        WHERE id = ?
    """, (site_id,))

        conn.commit()
        conn.close()

        return {
            "status": "deleted",
            "site_id": site_id,
            "deleted": {
                "buildings": deleted_buildings,
                "floors": deleted_floors,
                "rooms": deleted_rooms,
                "floorplans": deleted_floorplans,
                "site_maps": deleted_site_maps,
                "user_access_records": deleted_user_access
            },
            "devices_preserved_and_unassigned": devices_count,
            "gateways_preserved_and_unassigned": gateways_unassigned
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/buildings/{building_id}")
def delete_building(building_id: int):
    conn = db()
    try:

        building = conn.execute("""
        SELECT *
        FROM buildings
        WHERE id = ?
    """, (building_id,)).fetchone()

        if not building:
            conn.close()
            raise HTTPException(status_code=404, detail="Building not found")

        floor_ids = get_ids(conn, """
        SELECT id
        FROM floors
        WHERE building_id = ?
    """, (building_id,))

        room_ids = get_room_ids_for_floors(conn, floor_ids)
        floorplan_ids = get_floorplan_ids_for_floors_and_rooms(conn, floor_ids, room_ids)

        devices_count = conn.execute("""
        SELECT COUNT(*)
        FROM devices
        WHERE building_id = ?
    """, (building_id,)).fetchone()[0]

        conn.execute("""
        UPDATE devices
        SET is_placed = 0,
            building_id = NULL,
            floor_id = NULL,
            room_id = NULL,
            building = NULL,
            floor = NULL,
            room = NULL,
            x = NULL,
            y = NULL
        WHERE building_id = ?
    """, (building_id,))

        gateways_unassigned = safe_unassign_gateways(
            conn,
            building_ids=[building_id],
            floor_ids=floor_ids,
            clear_building=True,
            clear_floor=True
        )

        deleted_user_access = conn.execute("""
        DELETE FROM user_access
        WHERE building_id = ?
    """, (building_id,)).rowcount

        if floor_ids:
            placeholders = ",".join("?" for _ in floor_ids)
            deleted_user_access += conn.execute(f"""
            DELETE FROM user_access
            WHERE floor_id IN ({placeholders})
        """, floor_ids).rowcount

        deleted_rooms = delete_by_ids(conn, "rooms", room_ids)
        deleted_floorplans = delete_by_ids(conn, "floorplans", floorplan_ids)
        deleted_floors = delete_by_ids(conn, "floors", floor_ids)

        conn.execute("""
        DELETE FROM buildings
        WHERE id = ?
    """, (building_id,))

        conn.commit()
        conn.close()

        return {
            "status": "deleted",
            "building_id": building_id,
            "deleted": {
                "floors": deleted_floors,
                "rooms": deleted_rooms,
                "floorplans": deleted_floorplans,
                "user_access_records": deleted_user_access
            },
            "devices_preserved_and_unassigned": devices_count,
            "gateways_preserved_and_unassigned": gateways_unassigned
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/floors/{floor_id}")
def delete_floor(floor_id: int):
    conn = db()
    try:

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
    """, (floor_id,)).fetchall()

        room_ids = [row["id"] for row in room_rows]
        floorplan_ids = get_floorplan_ids_for_floors_and_rooms(conn, [floor_id], room_ids)

        devices_count = conn.execute("""
        SELECT COUNT(*)
        FROM devices
        WHERE floor_id = ?
           OR room_id IN (
                SELECT id
                FROM rooms
                WHERE floor_id = ?
           )
    """, (floor_id, floor_id)).fetchone()[0]

        conn.execute("""
        UPDATE devices
        SET is_placed = 0,
            floor_id = NULL,
            room_id = NULL,
            floor = NULL,
            room = NULL,
            x = NULL,
            y = NULL
        WHERE floor_id = ?
           OR room_id IN (
                SELECT id
                FROM rooms
                WHERE floor_id = ?
           )
    """, (floor_id, floor_id))

        gateways_unassigned = safe_unassign_gateways(
            conn,
            floor_ids=[floor_id],
            clear_floor=True
        )

        deleted_user_access = conn.execute("""
        DELETE FROM user_access
        WHERE floor_id = ?
    """, (floor_id,)).rowcount

        deleted_rooms = delete_by_ids(conn, "rooms", room_ids)
        deleted_floorplans = delete_by_ids(conn, "floorplans", floorplan_ids)

        conn.execute("""
        DELETE FROM floors
        WHERE id = ?
    """, (floor_id,))

        conn.commit()
        conn.close()

        return {
            "status": "deleted",
            "floor_id": floor_id,
            "deleted": {
                "rooms": deleted_rooms,
                "floorplans": deleted_floorplans,
                "user_access_records": deleted_user_access
            },
            "devices_preserved_and_unassigned": devices_count,
            "gateways_preserved_and_unassigned": gateways_unassigned
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/sites/{site_id}")
def update_site(site_id: int, data: dict):
    name = data.get("name", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Site name required")

    conn = db()
    try:

        cur = conn.execute("""
        UPDATE sites
        SET name = ?
        WHERE id = ?
    """, (name, site_id))

        conn.commit()
        updated = cur.rowcount
        conn.close()

        if updated == 0:
            raise HTTPException(status_code=404, detail="Site not found")

        return {
            "status": "updated",
            "site_id": site_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/buildings/{building_id}")
def update_building(building_id: int, data: dict):
    name = data.get("name", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Building name required")

    conn = db()
    try:

        cur = conn.execute("""
        UPDATE buildings
        SET name = ?
        WHERE id = ?
    """, (name, building_id))

        conn.commit()
        updated = cur.rowcount
        conn.close()

        if updated == 0:
            raise HTTPException(status_code=404, detail="Building not found")

        return {
            "status": "updated",
            "building_id": building_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/floors/{floor_id}")
def update_floor(floor_id: int, data: dict):
    name = data.get("name", "").strip()
    floor_number = data.get("floor_number")

    if not name:
        raise HTTPException(status_code=400, detail="Floor name required")

    conn = db()
    try:

        cur = conn.execute("""
        UPDATE floors
        SET name = ?,
            floor_number = ?
        WHERE id = ?
    """, (name, floor_number, floor_id))

        conn.commit()
        updated = cur.rowcount
        conn.close()

        if updated == 0:
            raise HTTPException(status_code=404, detail="Floor not found")

        return {
            "status": "updated",
            "floor_id": floor_id,
            "name": name,
            "floor_number": floor_number,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/floors/{floor_id}/rooms")
def create_room_for_floor(floor_id: int, data: dict):
    room_name = data.get("room_name", "").strip()
    polygon_points = data.get("polygon_points", [])

    if not room_name:
        raise HTTPException(status_code=400, detail="room_name required")

    if not isinstance(polygon_points, list) or len(polygon_points) < 3:
        raise HTTPException(status_code=400, detail="polygon_points must contain at least 3 points")

    conn = db()
    try:

        floor = conn.execute("""
        SELECT f.*, b.name AS building_name
        FROM floors f
        JOIN buildings b ON f.building_id = b.id
        WHERE f.id = ?
    """, (floor_id,)).fetchone()

        if not floor:
            conn.close()
            raise HTTPException(status_code=404, detail="Floor not found")

        try:
            x_values = [p["x"] for p in polygon_points]
            y_values = [p["y"] for p in polygon_points]

            center_x = int(sum(x_values) / len(x_values))
            center_y = int(sum(y_values) / len(y_values))

        except Exception:
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="Invalid polygon_points format"
            )

        cur = conn.execute("""
        INSERT INTO rooms (
            floor_id,
            room_name,
            polygon_points,
            x,
            y
        ) VALUES (?, ?, ?, ?, ?)
    """, (
            floor_id,
            room_name,
            json.dumps(polygon_points),
            center_x,
            center_y,
        ))

        conn.commit()
        room_id = cur.lastrowid
        conn.close()

        return {
            "status": "created",
            "room_id": room_id,
            "floor_id": floor_id,
            "room_name": room_name,
            "x": center_x,
            "y": center_y,
            "polygon_points": polygon_points,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/rooms/{room_id}")
def get_room(room_id: int):
    conn = db()
    try:

        room = conn.execute("""
        SELECT *
        FROM rooms
        WHERE id = ?
    """, (room_id,)).fetchone()

        conn.close()

        if not room:
            raise HTTPException(status_code=404, detail="Room not found")

        result = dict(room)

        if result.get("polygon_points"):
            result["polygon_points"] = json.loads(result["polygon_points"])

        return result
    finally:
        conn.close()
@router.post("/rooms/{room_id}/devices")
def assign_device_to_room_post(room_id: int, data: dict):

    device_id = data.get("device_id")

    if not device_id:
        raise HTTPException(
            status_code=400,
            detail="device_id required"
        )

    conn = db()
    try:

        room = conn.execute("""
        SELECT
            r.id as room_id,
            r.floor_id,
            f.building_id
        FROM rooms r
        JOIN floors f
            ON r.floor_id = f.id
        WHERE r.id = ?
    """, (room_id,)).fetchone()

        if not room:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Room not found"
            )

        cur = conn.execute("""
        UPDATE devices
        SET
            room_id = ?
        WHERE device_id = ?
    """, (
            room_id,
            device_id
        ))

        conn.commit()

        if cur.rowcount == 0:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Device not found"
            )

        conn.close()

        return {
            "status": "assigned",
            "device_id": device_id,
            "room_id": room_id
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/hierarchy")
def get_hierarchy():

    conn = db()
    try:

        clients = conn.execute("""
        SELECT *
        FROM clients
        ORDER BY name
    """).fetchall()

        result = []

        for client in clients:

            client_obj = dict(client)
            client_obj["sites"] = []

            sites = conn.execute("""
            SELECT *
            FROM sites
            WHERE client_id = ?
            ORDER BY name
        """, (client["id"],)).fetchall()

            for site in sites:

                site_obj = dict(site)
                site_obj["buildings"] = []

                buildings = conn.execute("""
                SELECT *
                FROM buildings
                WHERE site_id = ?
                ORDER BY name
            """, (site["id"],)).fetchall()

                for building in buildings:

                    building_obj = dict(building)
                    building_obj["floors"] = []

                    floors = conn.execute("""
                    SELECT *
                    FROM floors
                    WHERE building_id = ?
                    ORDER BY floor_number
                """, (building["id"],)).fetchall()

                    for floor in floors:

                        floor_obj = dict(floor)
                        floor_obj["rooms"] = []

                        rooms = conn.execute("""
                        SELECT *
                        FROM rooms
                        WHERE floor_id = ?
                        ORDER BY room_name
                    """, (floor["id"],)).fetchall()

                        for room in rooms:

                            room_obj = dict(room)

                            devices = conn.execute("""
                            SELECT
                                device_id,
                                node_type,
                                label,
                                room_id,
                                floor_id,
                                building_id,
                                site_id,
                                client_id
                            FROM devices
                            WHERE room_id = ?
                            ORDER BY label
                        """, (room["id"],)).fetchall()

                            room_obj["devices"] = [
                                dict(d)
                                for d in devices
                            ]

                            floor_obj["rooms"].append(room_obj)

                        building_obj["floors"].append(floor_obj)

                    site_obj["buildings"].append(building_obj)

                client_obj["sites"].append(site_obj)

            result.append(client_obj)

        conn.close()

        return result
    finally:
        conn.close()
@router.post("/sites/{site_id}/upload-site-map")
async def upload_site_map(
    site_id: int,
    image: UploadFile = File(...)
):
    conn = db()
    try:

        site = conn.execute("""
        SELECT *
        FROM sites
        WHERE id = ?
    """, (site_id,)).fetchone()

        if not site:
            conn.close()
            raise HTTPException(status_code=404, detail="Site not found")

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", site["name"])
        filename, width, height = await save_image_securely(image, UPLOAD_DIR, prefix=f"site_{site_id}_{safe_name}")
        image_path = f"/uploads/{filename}"

        conn.execute("""
        INSERT INTO site_maps (
            site_id,
            image_path,
            image_width,
            image_height
        ) VALUES (?, ?, ?, ?)
    """, (
            site_id,
            image_path,
            width,
            height,
        ))

        conn.execute("""
        UPDATE sites
        SET campus_image_path = ?,
            image_width = ?,
            image_height = ?
        WHERE id = ?
    """, (
            image_path,
            width,
            height,
            site_id,
        ))

        conn.commit()
        conn.close()

        return {
            "status": "uploaded",
            "site_id": site_id,
            "image_path": image_path,
            "image_width": width,
            "image_height": height,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/sites/{site_id}/map")
def get_site_map(site_id: int):

    conn = db()
    try:

        site = conn.execute("""
        SELECT *
        FROM sites
        WHERE id = ?
    """, (site_id,)).fetchone()

        if not site:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Site not found"
            )

        site_dict = dict(site)

        # Fallback to site_maps table if campus_image_path is not populated on sites row
        if not site_dict.get("campus_image_path"):
            latest_map = conn.execute("""
                SELECT image_path, image_width, image_height
                FROM site_maps
                WHERE site_id = ?
                ORDER BY id DESC
                LIMIT 1
            """, (site_id,)).fetchone()
            if latest_map:
                site_dict["campus_image_path"] = latest_map["image_path"]
                site_dict["image_width"] = latest_map["image_width"]
                site_dict["image_height"] = latest_map["image_height"]

        buildings = conn.execute("""
        SELECT *
        FROM buildings
        WHERE site_id = ?
        ORDER BY name
    """, (site_id,)).fetchall()

        conn.close()

        img_path = site_dict.get("campus_image_path")
        result = {
            "site": site_dict,
            "campus_image_path": img_path,
            "image_path": img_path,
            "image_width": site_dict.get("image_width"),
            "image_height": site_dict.get("image_height"),
            "buildings": []
        }

        for b in buildings:

            item = dict(b)

            try:
                item["polygon_points"] = (
                    json.loads(item["polygon_points"])
                    if item["polygon_points"]
                    else []
                )
            except Exception:
                item["polygon_points"] = []

            result["buildings"].append(item)

        return result
    finally:
        conn.close()
@router.put("/buildings/{building_id}/polygon")
def update_building_polygon(
    building_id: int,
    data: dict
):

    polygon_points = data.get("polygon_points", [])

    if len(polygon_points) < 3:
        raise HTTPException(
            status_code=400,
            detail="At least 3 points required"
        )

    try:
        x_values = [p["x"] for p in polygon_points]
        y_values = [p["y"] for p in polygon_points]

        center_x = int(sum(x_values) / len(x_values))
        center_y = int(sum(y_values) / len(y_values))

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid polygon_points format"
        )

    conn = db()
    try:

        cur = conn.execute("""
        UPDATE buildings
        SET
            polygon_points = ?,
            x = ?,
            y = ?
        WHERE id = ?
    """, (
            json.dumps(polygon_points),
            center_x,
            center_y,
            building_id
        ))

        conn.commit()

        if cur.rowcount == 0:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Building not found"
            )

        conn.close()

        return {
            "status": "updated",
            "building_id": building_id,
            "center_x": center_x,
            "center_y": center_y,
            "polygon_points": polygon_points
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/sites/{site_id}/buildings")
def get_site_buildings(site_id: int):

    conn = db()
    try:

        rows = conn.execute("""
        SELECT *
        FROM buildings
        WHERE site_id = ?
        ORDER BY name
    """, (site_id,)).fetchall()

        conn.close()

        result = []

        for row in rows:

            item = dict(row)

            try:
                item["polygon_points"] = (
                    json.loads(item["polygon_points"])
                    if item["polygon_points"]
                    else []
                )
            except Exception:
                item["polygon_points"] = []

            result.append(item)

        return result
    finally:
        conn.close()
@router.get("/buildings/{building_id}/floors")
def get_building_floors(building_id: int):

    conn = db()
    try:

        rows = conn.execute("""
        SELECT *
        FROM floors
        WHERE building_id = ?
        ORDER BY floor_number
    """, (building_id,)).fetchall()

        conn.close()

        return [dict(r) for r in rows]
    finally:
        conn.close()
@router.get("/floors/{floor_id}/details")
def get_floor_details(floor_id: int):

    conn = db()
    try:

        floor = conn.execute("""
        SELECT *
        FROM floors
        WHERE id = ?
    """, (floor_id,)).fetchone()

        if not floor:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Floor not found"
            )

        rooms = conn.execute("""
        SELECT *
        FROM rooms
        WHERE floor_id = ?
        ORDER BY room_name
    """, (floor_id,)).fetchall()

        result = {
            "floor": dict(floor),
            "rooms": []
        }

        for room in rooms:

            room_obj = dict(room)

            try:
                room_obj["polygon_points"] = (
                    json.loads(room_obj["polygon_points"])
                    if room_obj["polygon_points"]
                    else []
                )
            except Exception:
                room_obj["polygon_points"] = []

            devices = conn.execute("""
            SELECT *
            FROM devices
            WHERE room_id = ?
        """, (room["id"],)).fetchall()

            room_obj["devices"] = [
                redact_secrets(dict(d))
                for d in devices
            ]

            result["rooms"].append(room_obj)

        conn.close()

        return result
    finally:
        conn.close()
def _build_floor_live_device(conn, device_row, include_upstream: bool = False):
    """Attach profile and telemetry metadata to a device shown on a floor."""
    # The row is SELECT *, so it carries the LoRaWAN root key. Strip secrets
    # before anything else can copy this dict into a response.
    device = redact_secrets(dict(device_row))
    device_id = device["device_id"]
    profile = load_device_profile_for_live_telemetry(conn, device_id)
    profile_metadata = build_profile_live_metadata(profile)

    if profile_metadata:
        capabilities = profile_metadata["capabilities"]
    else:
        try:
            capabilities = get_device_capabilities_list(
                conn,
                device_id,
                device.get("node_type"),
            )
        except Exception:
            capabilities = [device.get("node_type")]
    capabilities = [capability for capability in capabilities if capability]

    tb_telemetry = {}
    if include_upstream:
        try:
            tb_telemetry = read_tb_latest_telemetry(
                device_id,
                profile=profile,
            ) or {}
        except Exception as exc:
            logger.info("ThingsBoard telemetry failed in floor live: %s", exc)

    try:
        local_telemetry = get_local_latest_telemetry(conn, device_id) or {}
    except Exception as exc:
        logger.info("Local telemetry failed in floor live: %s", exc)
        local_telemetry = {}

    telemetry = merge_profile_live_telemetry_sources(
        tb_telemetry,
        local_telemetry,
    )
    device.update(
        {
            "node_type": profile.get("node_type") if profile else device.get("node_type"),
            "capabilities": capabilities,
            "profile_assigned": bool(profile),
            "profile": profile_metadata,
            "profile_id": profile.get("id") if profile else device.get("profile_id"),
            "profile_code": profile.get("profile_code") if profile else device.get("profile_code"),
            "profile_version": profile.get("profile_version") if profile else device.get("profile_version"),
            "payload_version": profile.get("payload_version") if profile else device.get("payload_version"),
            "icon_type": (
                profile.get("icon_type")
                if profile and profile.get("icon_type")
                else device.get("icon_type")
            ),
            "icon_color": profile.get("icon_color") if profile else None,
            "telemetry": telemetry,
            "telemetry_fields": build_profile_live_field_metadata(profile, surface="all"),
            "display_telemetry": build_profile_display_telemetry(
                profile,
                telemetry,
                surface="floor",
            ),
        }
    )
    device.update(
        build_device_state(
            device,
            stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
            offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
        )
    )
    return device


@router.get("/floors/{floor_id}/live")
def get_floor_live(floor_id: int, include_upstream: bool = False):
    """
    Return one floor with rooms, gateways and profile-driven
    device telemetry for Floor Live View.
    """

    conn = db()

    try:
        # =====================================================
        # 1. LOAD FLOOR
        # =====================================================

        floor = conn.execute(
            """
            SELECT
                f.*,
                b.name AS building_name,
                s.id AS site_id,
                s.name AS site_name,
                c.id AS client_id,
                c.name AS client_name
            FROM floors AS f
            LEFT JOIN buildings AS b
              ON b.id = f.building_id
            LEFT JOIN sites AS s
              ON s.id = b.site_id
            LEFT JOIN clients AS c
              ON c.id = s.client_id
            WHERE f.id = ?
            """,
            (floor_id,),
        ).fetchone()

        if not floor:
            raise HTTPException(
                status_code=404,
                detail="Floor not found",
            )

        floor_dict = dict(
            floor
        )

        floor_name = floor_dict.get(
            "name"
        )

        floor_number = floor_dict.get(
            "floor_number"
        )

        # =====================================================
        # 2. LOAD FLOOR ROOMS
        # =====================================================

        rooms = conn.execute(
            """
            SELECT *
            FROM rooms
            WHERE floor_id = ?
            ORDER BY room_name
            """,
            (floor_id,),
        ).fetchall()

        result = {
            "floor": floor_dict,
            "rooms": [],
            "floor_devices": [],
            "gateways": [],
        }

        # =====================================================
        # 3. BUILD EVERY ROOM
        # =====================================================

        for room in rooms:
            room_obj = dict(
                room
            )

            try:
                room_obj["polygon_points"] = (
                    json.loads(
                        room_obj[
                            "polygon_points"
                        ]
                    )
                    if room_obj.get(
                        "polygon_points"
                    )
                    else []
                )

            except Exception:
                room_obj[
                    "polygon_points"
                ] = []

            devices = conn.execute(
                """
                SELECT DISTINCT *
                FROM devices
                WHERE room_id = ?

                   OR (
                        room = ?

                        AND (
                            floor_id = ?
                            OR floor = ?
                            OR floor = ?
                        )
                   )

                ORDER BY
                    COALESCE(label, device_id),
                    device_id
                """,
                (
                    room["id"],
                    room["room_name"],
                    floor_id,
                    floor_name,
                    floor_number,
                ),
            ).fetchall()

            room_obj["devices"] = []

            # =================================================
            # 4. BUILD EVERY DEVICE
            # =================================================

            for device_row in devices:
                # SELECT * carries the LoRaWAN root key; strip it before the
                # dict is enriched and returned.
                device = redact_secrets(dict(
                    device_row
                ))

                device_id = device[
                    "device_id"
                ]

                # ---------------------------------------------
                # Assigned sensor profile
                # ---------------------------------------------

                profile = (
                    load_device_profile_for_live_telemetry(
                        conn,
                        device_id,
                    )
                )

                profile_metadata = (
                    build_profile_live_metadata(
                        profile
                    )
                )

                # ---------------------------------------------
                # Capabilities
                # ---------------------------------------------

                if profile_metadata:
                    capabilities = (
                        profile_metadata[
                            "capabilities"
                        ]
                    )

                else:
                    try:
                        capabilities = (
                            get_device_capabilities_list(
                                conn,
                                device_id,
                                device.get(
                                    "node_type"
                                ),
                            )
                        )

                    except Exception:
                        capabilities = [
                            device.get(
                                "node_type"
                            )
                        ]

                capabilities = [
                    capability
                    for capability in capabilities
                    if capability
                ]

                # ---------------------------------------------
                # ThingsBoard telemetry
                # ---------------------------------------------

                tb_telemetry = {}
                if include_upstream:
                    try:
                        tb_telemetry = (
                            read_tb_latest_telemetry(
                                device_id,
                                profile=profile,
                            )
                            or {}
                        )

                    except Exception as exc:
                        logger.info(
                            "ThingsBoard telemetry failed "
                            "in floor live: %s",
                            exc,
                        )

                # ---------------------------------------------
                # Local validated telemetry
                # ---------------------------------------------

                try:
                    local_telemetry = (
                        get_local_latest_telemetry(
                            conn,
                            device_id,
                        )
                        or {}
                    )

                except Exception as exc:
                    logger.info(
                        "Local telemetry failed "
                        "in floor live: %s",
                        exc,
                    )

                    local_telemetry = {}

                # ---------------------------------------------
                # Merge both sources
                # ---------------------------------------------

                telemetry = (
                    merge_profile_live_telemetry_sources(
                        tb_telemetry,
                        local_telemetry,
                    )
                )

                # All fields declared by the selected profile.
                telemetry_fields = (
                    build_profile_live_field_metadata(
                        profile,
                        surface="all",
                    )
                )

                # Only fields enabled for Floor Live View.
                floor_display_telemetry = (
                    build_profile_display_telemetry(
                        profile,
                        telemetry,
                        surface="floor",
                    )
                )

                # ---------------------------------------------
                # Build device response
                # ---------------------------------------------

                device["node_type"] = (
                    profile.get(
                        "node_type"
                    )
                    if profile
                    else device.get(
                        "node_type"
                    )
                )

                device["capabilities"] = (
                    capabilities
                )

                device["profile_assigned"] = (
                    bool(profile)
                )

                device["profile"] = (
                    profile_metadata
                )

                device["profile_id"] = (
                    profile.get("id")
                    if profile
                    else device.get(
                        "profile_id"
                    )
                )

                device["profile_code"] = (
                    profile.get(
                        "profile_code"
                    )
                    if profile
                    else device.get(
                        "profile_code"
                    )
                )

                device["profile_version"] = (
                    profile.get(
                        "profile_version"
                    )
                    if profile
                    else device.get(
                        "profile_version"
                    )
                )

                device["payload_version"] = (
                    profile.get(
                        "payload_version"
                    )
                    if profile
                    else device.get(
                        "payload_version"
                    )
                )

                device["icon_type"] = (
                    profile.get(
                        "icon_type"
                    )
                    if (
                        profile
                        and profile.get(
                            "icon_type"
                        )
                    )
                    else device.get(
                        "icon_type"
                    )
                )

                device["icon_color"] = (
                    profile.get(
                        "icon_color"
                    )
                    if profile
                    else None
                )

                device["telemetry"] = (
                    telemetry
                )

                device["telemetry_fields"] = (
                    telemetry_fields
                )

                device[
                    "display_telemetry"
                ] = floor_display_telemetry

                device.update(
                    build_device_state(
                        device,
                        stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
                        offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
                    )
                )

                room_obj["devices"].append(
                    device
                )

            result["rooms"].append(
                room_obj
            )

        floor_device_rows = conn.execute(
            """
            SELECT *
            FROM devices
            WHERE floor_id = ? AND room_id IS NULL
            ORDER BY COALESCE(label, device_id), device_id
            """,
            (floor_id,),
        ).fetchall()
        result["floor_devices"] = [
            _build_floor_live_device(conn, row, include_upstream)
            for row in floor_device_rows
        ]

        # =====================================================
        # 5. LOAD FLOOR GATEWAYS
        # =====================================================

        try:
            gateway_rows = conn.execute(
                """
                SELECT *
                FROM gateways
                WHERE floor_id = ?
                ORDER BY name
                """,
                (floor_id,),
            ).fetchall()

            result["gateways"] = [
                dict(gateway)
                for gateway in gateway_rows
            ]

        except Exception as exc:
            logger.info(
                "Gateway loading failed "
                "in floor live: %s",
                exc,
            )

            result["gateways"] = []

        return result

    finally:
        conn.close()
@router.delete("/buildings/{building_id}/polygon")
def delete_building_polygon(building_id: int):
    conn = db()
    try:

        cur = conn.execute("""
        UPDATE buildings
        SET polygon_points = ?,
            x = NULL,
            y = NULL
        WHERE id = ?
    """, (
            json.dumps([]),
            building_id,
        ))

        conn.commit()
        updated = cur.rowcount
        conn.close()

        if updated == 0:
            raise HTTPException(status_code=404, detail="Building not found")

        return {
            "status": "polygon_deleted",
            "building_id": building_id,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/buildings/{building_id}/overview")
def get_building_overview(building_id: int):

    conn = db()
    try:

        building = conn.execute("""
        SELECT *
        FROM buildings
        WHERE id = ?
    """, (building_id,)).fetchone()

        if not building:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Building not found"
            )

        floors = conn.execute("""
        SELECT *
        FROM floors
        WHERE building_id = ?
        ORDER BY floor_number
    """, (building_id,)).fetchall()

        result = {
            "building": dict(building),
            "floors": [dict(f) for f in floors]
        }

        conn.close()

        return result
    finally:
        conn.close()
@router.delete("/rooms/{room_id}")
def delete_room(room_id: int):
    conn = db()
    try:

        room = conn.execute("""
        SELECT *
        FROM rooms
        WHERE id = ?
    """, (room_id,)).fetchone()

        if not room:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Room not found"
            )

        devices_count = conn.execute("""
        SELECT COUNT(*)
        FROM devices
        WHERE room_id = ?
    """, (room_id,)).fetchone()[0]

        conn.execute("""
        UPDATE devices
        SET room_id = NULL,
            room = NULL,
            x = NULL,
            y = NULL
        WHERE room_id = ?
    """, (room_id,))

        conn.execute("""
        DELETE FROM rooms
        WHERE id = ?
    """, (room_id,))

        conn.commit()
        conn.close()

        return {
            "status": "deleted",
            "room_id": room_id,
            "devices_preserved_and_unassigned": devices_count
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/rooms/{room_id}")
def update_room(room_id: int, data: dict):
    room_name = data.get("room_name", "").strip()
    polygon_points = data.get("polygon_points")

    if not room_name:
        raise HTTPException(status_code=400, detail="room_name required")

    conn = db()
    try:

        room = conn.execute("""
        SELECT *
        FROM rooms
        WHERE id = ?
    """, (room_id,)).fetchone()

        if not room:
            conn.close()
            raise HTTPException(status_code=404, detail="Room not found")

        if polygon_points is not None:
            if not isinstance(polygon_points, list) or len(polygon_points) < 3:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail="polygon_points must contain at least 3 points"
                )

            try:
                x_values = [p["x"] for p in polygon_points]
                y_values = [p["y"] for p in polygon_points]

                center_x = int(sum(x_values) / len(x_values))
                center_y = int(sum(y_values) / len(y_values))

            except Exception:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail="Invalid polygon_points format"
                )

            conn.execute("""
            UPDATE rooms
            SET room_name = ?,
                polygon_points = ?,
                x = ?,
                y = ?
            WHERE id = ?
        """, (
                room_name,
                json.dumps(polygon_points),
                center_x,
                center_y,
                room_id,
            ))

        else:
            conn.execute("""
            UPDATE rooms
            SET room_name = ?
            WHERE id = ?
        """, (
                room_name,
                room_id,
            ))

        conn.commit()
        conn.close()

        return {
            "status": "updated",
            "room_id": room_id,
            "room_name": room_name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/floors/{floor_id}/rooms")
def get_floor_rooms(floor_id: int):

    conn = db()
    try:

        floor = conn.execute("""
        SELECT *
        FROM floors
        WHERE id = ?
    """, (floor_id,)).fetchone()

        if not floor:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Floor not found"
            )

        rooms = conn.execute("""
        SELECT *
        FROM rooms
        WHERE floor_id = ?
        ORDER BY room_name
    """, (floor_id,)).fetchall()

        result = []

        for room in rooms:

            room_obj = dict(room)

            try:
                room_obj["polygon_points"] = (
                    json.loads(room_obj["polygon_points"])
                    if room_obj["polygon_points"]
                    else []
                )
            except Exception:
                room_obj["polygon_points"] = []

            result.append(room_obj)

        conn.close()

        return result
    finally:
        conn.close()
