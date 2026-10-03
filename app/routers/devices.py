import logging

logger = logging.getLogger(__name__)

import csv
import json
import math
import secrets

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.connection import get_db_connection as db
from app.main import (
    FORMATTERS,
    PROFILE_KEY_PATTERN,
    build_profile_display_telemetry,
    build_profile_live_field_metadata,
    build_profile_live_metadata,
    get_device_capabilities_list,
    get_device_metadata_by_device_id,
    get_device_scope_for_audit,
    get_local_latest_telemetry,
    get_sensor_profile_detail,
    load_device_profile_for_live_telemetry,
    log_audit_event,
    merge_profile_live_telemetry_sources,
    profile_unique_string_list,
    read_tb_latest_telemetry,
    set_device_formatter,
    set_device_formatter_from_profile,
)
from app.services.device_state import build_device_state
from app.services.export_safety import redact_secrets

router = APIRouter()

@router.get("/device-status/{device_id}")
def device_status(device_id: str):
    conn = db()
    try:
        row = get_device_metadata_by_device_id(conn, device_id)
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Device not found in backend database")

        # ThingsBoard is an optional upstream: a device's stored metadata is still
        # useful when it is unreachable, so degrade instead of failing the request
        # (this mirrors how /devices-status already handles it).
        try:
            telemetry = read_tb_latest_telemetry(device_id)
        except Exception as exc:
            logger.info("ThingsBoard telemetry failed in device-status: %s", exc)
            telemetry = {}

        return {
            "device_id": row["device_id"],
            "node_type": row["node_type"],
            "building": row["building"],
            "floor": row["floor"],
            "room": row["room"],
            "label": row["label"],
            "x": row["x"],
            "y": row["y"],
            "icon_type": row["icon_type"],
            "telemetry": telemetry,
        }
    finally:
        conn.close()
@router.get("/devices-status")
def devices_status():
    """
    Return every device with profile-driven telemetry metadata and
    a merged ThingsBoard/local telemetry view.
    """

    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT 
                d.*,
                r.room_name as room,
                f.name as floor,
                b.name as building
            FROM devices d
            LEFT JOIN rooms r ON d.room_id = r.id
            LEFT JOIN floors f ON f.id = COALESCE(d.floor_id, r.floor_id)
            LEFT JOIN buildings b ON f.building_id = b.id
            ORDER BY
                COALESCE(d.label, d.device_id),
                d.device_id
            """
        ).fetchall()

        result = []

        for row in rows:
            device = dict(
                row
            )

            device_id = device[
                "device_id"
            ]

            # =================================================
            # 1. LOAD ASSIGNED SENSOR PROFILE
            # =================================================

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

            # =================================================
            # 2. DETERMINE CAPABILITIES
            # =================================================

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

            # =================================================
            # 3. LOAD THINGSBOARD TELEMETRY
            # =================================================

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
                    "in devices-status: %s",
                    exc,
                )

                tb_telemetry = {}

            # =================================================
            # 4. LOAD LOCAL VALIDATED TELEMETRY
            # =================================================

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
                    "in devices-status: %s",
                    exc,
                )

                local_telemetry = {}

            # =================================================
            # 5. MERGE BOTH TELEMETRY SOURCES
            # =================================================

            telemetry = (
                merge_profile_live_telemetry_sources(
                    tb_telemetry,
                    local_telemetry,
                )
            )

            telemetry_fields = (
                build_profile_live_field_metadata(
                    profile,
                    surface="all",
                )
            )

            display_telemetry = (
                build_profile_display_telemetry(
                    profile,
                    telemetry,
                    surface="dashboard",
                )
            )

            # =================================================
            # 6. BUILD DEVICE RESPONSE
            # =================================================

            state = build_device_state(
                device,
                stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
                offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
            )

            result.append(
                {
                    **state,
                    "device_id": device_id,

                    "node_type": (
                        profile.get(
                            "node_type"
                        )
                        if profile
                        else device.get(
                            "node_type"
                        )
                    ),

                    "capabilities": (
                        capabilities
                    ),

                    "profile_assigned": bool(
                        profile
                    ),

                    "profile": (
                        profile_metadata
                    ),

                    "profile_id": (
                        profile.get("id")
                        if profile
                        else device.get(
                            "profile_id"
                        )
                    ),

                    "profile_code": (
                        profile.get(
                            "profile_code"
                        )
                        if profile
                        else device.get(
                            "profile_code"
                        )
                    ),

                    "profile_version": (
                        profile.get(
                            "profile_version"
                        )
                        if profile
                        else device.get(
                            "profile_version"
                        )
                    ),

                    "payload_version": (
                        profile.get(
                            "payload_version"
                        )
                        if profile
                        else device.get(
                            "payload_version"
                        )
                    ),

                    "building": device.get(
                        "building"
                    ),

                    "floor": device.get(
                        "floor"
                    ),

                    "room": device.get(
                        "room"
                    ),

                    "label": device.get(
                        "label"
                    ),

                    "x": device.get("x"),
                    "y": device.get("y"),

                    "icon_type": (
                        profile.get(
                            "icon_type"
                        )
                        if profile
                        and profile.get(
                            "icon_type"
                        )
                        else device.get(
                            "icon_type"
                        )
                    ),

                    "icon_color": (
                        profile.get(
                            "icon_color"
                        )
                        if profile
                        else None
                    ),

                    "client_id": device.get(
                        "client_id"
                    ),

                    "site_id": device.get(
                        "site_id"
                    ),

                    "building_id": device.get(
                        "building_id"
                    ),

                    "floor_id": device.get(
                        "floor_id"
                    ),

                    "room_id": device.get(
                        "room_id"
                    ),

                    "configuration_status": (
                        device.get(
                            "configuration_status"
                        )
                    ),

                    "configuration_checksum": (
                        device.get(
                            "configuration_checksum"
                        )
                    ),

                    # Raw canonical telemetry remains available
                    # for existing portal code.
                    "telemetry": telemetry,

                    # Every field declared in the profile.
                    "telemetry_fields": (
                        telemetry_fields
                    ),

                    # Dashboard-visible fields combined with
                    # current values.
                    "display_telemetry": (
                        display_telemetry
                    ),
                }
            )

        return result

    finally:
        conn.close()


@router.get("/devices/unplaced")
def get_unplaced_devices():
    """
    Return all discovered or unplaced devices that are not yet positioned on a map.
    Includes latest telemetry and connection status.
    """
    conn = db()
    try:
        rows = conn.execute("""
            SELECT 
                d.*,
                lt.telemetry as latest_telemetry_json,
                lt.alarm_active,
                lt.alarm_message,
                lt.updated_at as telemetry_updated_at
            FROM devices d
            LEFT JOIN device_latest_telemetry lt ON d.device_id = lt.device_id
            WHERE d.x IS NULL OR d.y IS NULL OR d.is_placed = 0
            ORDER BY COALESCE(d.last_seen, d.created_at) DESC
        """).fetchall()

        result = []
        for row in rows:
            # SELECT d.* carries the LoRaWAN root key.
            dev = redact_secrets(dict(row))
            raw_telemetry = dev.pop("latest_telemetry_json", None)
            try:
                dev["telemetry"] = json.loads(raw_telemetry) if raw_telemetry else {}
            except Exception:
                dev["telemetry"] = {}
            dev.update(
                build_device_state(
                    dev,
                    stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
                    offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
                )
            )
            result.append(dev)
        return result
    finally:
        conn.close()


@router.post("/devices")
def create_device(data: dict):
    device_id = str(data.get("device_id") or "").strip()
    building_id = data.get("building_id")
    floor_id = data.get("floor_id")
    room_id = data.get("room_id")
    site_id = data.get("site_id")
    label = str(data.get("label") or "").strip() or device_id
    node_type = str(data.get("node_type") or "environment").strip()
    chip_mac = str(data.get("chip_mac") or "").strip() or None
    dev_eui = str(data.get("dev_eui") or "").strip() or None
    app_key = str(data.get("app_key") or "").strip() or None
    profile_id = data.get("profile_id")

    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    if not room_id and not floor_id:
        raise HTTPException(status_code=400, detail="floor_id or room_id is required")

    conn = db()
    try:
        existing = conn.execute("SELECT device_id FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"Device '{device_id}' already exists")

        client_id = None
        building_name = None
        floor_name = None
        room_name = None

        if room_id:
            room_row = conn.execute("""
                SELECT r.id as room_id, r.room_name,
                       f.id as floor_id, f.name as floor_name,
                       b.id as building_id, b.name as building_name,
                       s.id as site_id, c.id as client_id
                FROM rooms r
                JOIN floors f ON r.floor_id = f.id
                JOIN buildings b ON f.building_id = b.id
                LEFT JOIN sites s ON b.site_id = s.id
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE r.id = ?
            """, (room_id,)).fetchone()
            if not room_row:
                raise HTTPException(status_code=404, detail=f"Room with ID {room_id} not found")
            room_id = room_row["room_id"]
            floor_id = room_row["floor_id"]
            building_id = room_row["building_id"]
            site_id = room_row["site_id"]
            client_id = room_row["client_id"]
            building_name = room_row["building_name"]
            floor_name = room_row["floor_name"]
            room_name = room_row["room_name"]
        elif floor_id:
            floor_row = conn.execute("""
                SELECT f.id as floor_id, f.name as floor_name,
                       b.id as building_id, b.name as building_name,
                       s.id as site_id, c.id as client_id
                FROM floors f
                JOIN buildings b ON f.building_id = b.id
                LEFT JOIN sites s ON b.site_id = s.id
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE f.id = ?
            """, (floor_id,)).fetchone()
            if not floor_row:
                raise HTTPException(status_code=404, detail=f"Floor with ID {floor_id} not found")
            floor_id = floor_row["floor_id"]
            building_id = floor_row["building_id"]
            site_id = floor_row["site_id"]
            client_id = floor_row["client_id"]
            building_name = floor_row["building_name"]
            floor_name = floor_row["floor_name"]

        conn.execute("""
            INSERT INTO devices (
                device_id, chip_mac, dev_eui, app_key, node_type, label,
                building_id, floor_id, room_id, site_id, client_id, profile_id,
                building, floor, room
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            device_id, chip_mac, dev_eui, app_key, node_type, label,
            building_id, floor_id, room_id, site_id, client_id, profile_id,
            building_name, floor_name, room_name,
        ))

        conn.commit()

        log_audit_event(
            conn,
            action="create_device",
            target_type="device",
            target_id=device_id,
            message=f"Device {device_id} created and assigned to building {building_id}",
            client_id=client_id,
            site_id=site_id,
            building_id=building_id,
            floor_id=floor_id,
            room_id=room_id,
        )

        created = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        return {"status": "created", "device": dict(created)}
    finally:
        conn.close()


@router.put("/devices/{device_id}/assign-building")
def assign_device_to_building(device_id: str, data: dict):
    building_id = data.get("building_id")
    floor_id = data.get("floor_id")
    room_id = data.get("room_id")

    if not floor_id and not room_id:
        raise HTTPException(status_code=400, detail="floor_id or room_id is required")

    conn = db()
    try:
        device = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        site_id = None
        client_id = None
        building_name = None
        floor_name = None
        room_name = None

        if room_id:
            room_row = conn.execute("""
                SELECT r.id as room_id, r.room_name,
                       f.id as floor_id, f.name as floor_name,
                       b.id as building_id, b.name as building_name,
                       s.id as site_id, c.id as client_id
                FROM rooms r
                JOIN floors f ON r.floor_id = f.id
                JOIN buildings b ON f.building_id = b.id
                LEFT JOIN sites s ON b.site_id = s.id
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE r.id = ?
            """, (room_id,)).fetchone()
            if not room_row:
                raise HTTPException(status_code=404, detail=f"Room with ID {room_id} not found")
            room_id = room_row["room_id"]
            floor_id = room_row["floor_id"]
            building_id = room_row["building_id"]
            site_id = room_row["site_id"]
            client_id = room_row["client_id"]
            building_name = room_row["building_name"]
            floor_name = room_row["floor_name"]
            room_name = room_row["room_name"]
        elif floor_id:
            floor_row = conn.execute("""
                SELECT f.id as floor_id, f.name as floor_name,
                       b.id as building_id, b.name as building_name,
                       s.id as site_id, c.id as client_id
                FROM floors f
                JOIN buildings b ON f.building_id = b.id
                LEFT JOIN sites s ON b.site_id = s.id
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE f.id = ?
            """, (floor_id,)).fetchone()
            if not floor_row:
                raise HTTPException(status_code=404, detail=f"Floor with ID {floor_id} not found")
            floor_id = floor_row["floor_id"]
            building_id = floor_row["building_id"]
            site_id = floor_row["site_id"]
            client_id = floor_row["client_id"]
            building_name = floor_row["building_name"]
            floor_name = floor_row["floor_name"]
        conn.execute("""
            UPDATE devices
            SET building_id = ?, floor_id = ?, room_id = ?, site_id = ?, client_id = ?,
                building = ?, floor = ?, room = ?
            WHERE device_id = ?
        """, (
            building_id,
            floor_id,
            room_id,
            site_id,
            client_id,
            building_name,
            floor_name,
            room_name,
            device_id,
        ))

        conn.commit()

        updated = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        return {"status": "assigned", "device": dict(updated)}
    finally:
        conn.close()


@router.get("/buildings/{building_id}/devices")
def get_building_devices(building_id: int):
    conn = db()
    try:
        bldg = conn.execute("SELECT * FROM buildings WHERE id = ?", (building_id,)).fetchone()
        if not bldg:
            raise HTTPException(status_code=404, detail=f"Building with ID {building_id} not found")

        rows = conn.execute("""
            SELECT 
                d.*,
                r.room_name as room,
                f.name as floor,
                b.name as building
            FROM devices d
            LEFT JOIN rooms r ON d.room_id = r.id
            LEFT JOIN floors f ON d.floor_id = f.id OR r.floor_id = f.id
            LEFT JOIN buildings b ON d.building_id = b.id OR f.building_id = b.id
            WHERE d.building_id = ? OR f.building_id = ? OR r.floor_id IN (SELECT id FROM floors WHERE building_id = ?)
            GROUP BY d.device_id
            ORDER BY COALESCE(d.label, d.device_id)
        """, (building_id, building_id, building_id)).fetchall()

        # SELECT d.* carries the LoRaWAN root key.
        return [redact_secrets(dict(row)) for row in rows]
    finally:
        conn.close()


@router.get("/sites/{site_id}/devices")
def get_site_devices(site_id: int):
    conn = db()
    try:
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(status_code=404, detail=f"Site with ID {site_id} not found")

        rows = conn.execute("""
            SELECT 
                d.*,
                r.room_name as room,
                f.name as floor,
                b.name as building,
                s.name as site
            FROM devices d
            LEFT JOIN rooms r ON d.room_id = r.id
            LEFT JOIN floors f ON d.floor_id = f.id OR r.floor_id = f.id
            LEFT JOIN buildings b ON d.building_id = b.id OR f.building_id = b.id
            LEFT JOIN sites s ON d.site_id = s.id OR b.site_id = s.id
            WHERE d.site_id = ? OR b.site_id = ? OR s.id = ?
            GROUP BY d.device_id
            ORDER BY COALESCE(d.label, d.device_id)
        """, (site_id, site_id, site_id)).fetchall()

        # SELECT d.* carries the LoRaWAN root key.
        return [redact_secrets(dict(row)) for row in rows]
    finally:
        conn.close()



@router.put("/devices/{device_id}/assign-room")
def assign_device_to_room(device_id: str, data: dict):
    room_id = data.get("room_id")
    x = data.get("x")
    y = data.get("y")

    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")

    conn = db()
    try:

        device = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        if not device:
            conn.close()
            raise HTTPException(status_code=404, detail="Device not found")

        room = conn.execute("""
        SELECT r.*, f.id AS resolved_floor_id, f.name AS floor_name,
               b.id AS resolved_building_id, b.name AS building_name,
               s.id AS resolved_site_id, s.client_id AS resolved_client_id
        FROM rooms r
        JOIN floors f ON f.id = r.floor_id
        JOIN buildings b ON b.id = f.building_id
        LEFT JOIN sites s ON s.id = b.site_id
        WHERE r.id = ?
    """, (room_id,)).fetchone()

        if not room:
            conn.close()
            raise HTTPException(status_code=404, detail="Room not found")

        device_dict = dict(device)
        room_dict = dict(room)

        if x is None:
            x = room_dict.get("center_x") or device_dict.get("x")

        if y is None:
            y = room_dict.get("center_y") or device_dict.get("y")

        conn.execute("""
        UPDATE devices
        SET room_id = ?,
            floor_id = ?,
            building_id = ?,
            site_id = ?,
            client_id = ?,
            building = ?,
            floor = ?,
            room = ?,
            x = ?,
            y = ?
        WHERE device_id = ?
    """, (
            room_id,
            room_dict["resolved_floor_id"],
            room_dict["resolved_building_id"],
            room_dict["resolved_site_id"],
            room_dict["resolved_client_id"],
            room_dict["building_name"],
            room_dict["floor_name"],
            room_dict["room_name"],
            x,
            y,
            device_id
        ))

        conn.commit()

        updated = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        conn.close()

        return {
            "status": "assigned",
            "device": dict(updated)
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/devices/{device_id}")
def delete_device(device_id: str):
    conn = db()
    try:

        cur = conn.execute(
            "DELETE FROM devices WHERE device_id = ?",
            (device_id,)
        )

        conn.commit()
        deleted = cur.rowcount
        conn.close()

        if deleted == 0:
            raise HTTPException(status_code=404, detail="Device not found")

        return {
            "status": "deleted",
            "device_id": device_id
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/devices/{device_id}/latest-telemetry")
def admin_device_latest_telemetry(device_id: str):
    conn = db()
    try:

        device = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        if not device:
            conn.close()
            raise HTTPException(status_code=404, detail="Device not found")

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
@router.get("/devices/{device_id}/location")
def get_device_location(device_id: str):

    conn = db()
    try:

        device = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        if not device:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Device not found"
            )

        room_id = device["room_id"]
        floor_id = device["floor_id"]
    
        room = None
        floor = None
        building = None
        site = None
        client = None

        if room_id:
            room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        
        if floor_id:
            floor = conn.execute("SELECT * FROM floors WHERE id = ?", (floor_id,)).fetchone()
        elif room and room["floor_id"]:
            # Legacy rows may predate the canonical devices.floor_id column.
            floor = conn.execute("SELECT * FROM floors WHERE id = ?", (room["floor_id"],)).fetchone()
        
        if floor and floor["building_id"]:
            building = conn.execute("SELECT * FROM buildings WHERE id = ?", (floor["building_id"],)).fetchone()
        
        if building and building["site_id"]:
            site = conn.execute("SELECT * FROM sites WHERE id = ?", (building["site_id"],)).fetchone()
        
        if site and site["client_id"]:
            client = conn.execute("SELECT * FROM clients WHERE id = ?", (site["client_id"],)).fetchone()

        conn.close()

        return {
            "device": redact_secrets(dict(device)),
            "room": dict(room) if room else None,
            "floor": dict(floor) if floor else None,
            "building": dict(building) if building else None,
            "site": dict(site) if site else None,
            "client": dict(client) if client else None
        }
    finally:
        conn.close()
@router.put("/devices/{device_id}/position")
def update_device_position(
    device_id: str,
    data: dict
):
    x = data.get("x")
    y = data.get("y")
    room_id = data.get("room_id")
    floor_id = data.get("floor_id")

    if room_id is None and floor_id is None:
        raise HTTPException(
            status_code=400,
            detail="A valid floor_id or room_id is required for device placement",
        )

    if x is None or y is None:
        raise HTTPException(
            status_code=400,
            detail="x and y are required"
        )
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not math.isfinite(x)
        or not math.isfinite(y)
    ):
        raise HTTPException(
            status_code=400,
            detail="x and y must be finite numbers",
        )

    conn = db()
    try:

        device = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        if not device:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Device not found"
            )

        old_device = dict(device)

        if room_id is not None:
            room_row = conn.execute("""
                SELECT r.id as room_id, r.room_name,
                       f.id as floor_id, f.name as floor_name,
                       b.id as building_id, b.name as building_name,
                       s.id as site_id, c.id as client_id
                FROM rooms r
                JOIN floors f ON r.floor_id = f.id
                JOIN buildings b ON f.building_id = b.id
                LEFT JOIN sites s ON b.site_id = s.id
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE r.id = ?
                LIMIT 1
            """, (room_id,)).fetchone()

            if not room_row:
                conn.close()
                raise HTTPException(
                    status_code=404,
                    detail="Room not found"
                )

            conn.execute("""
                UPDATE devices
                SET
                    x = ?,
                    y = ?,
                    room_id = ?,
                    floor_id = ?,
                    building_id = ?,
                    site_id = ?,
                    client_id = ?,
                    building = ?,
                    floor = ?,
                    room = ?,
                    is_placed = 1
                WHERE device_id = ?
            """, (
                x,
                y,
                room_row["room_id"],
                room_row["floor_id"],
                room_row["building_id"],
                room_row["site_id"],
                room_row["client_id"],
                room_row["building_name"],
                room_row["floor_name"],
                room_row["room_name"],
                device_id
            ))

        elif floor_id is not None:
            floor_row = conn.execute("""
                SELECT f.id as floor_id, f.name as floor_name,
                       b.id as building_id, b.name as building_name,
                       s.id as site_id, c.id as client_id
                FROM floors f
                JOIN buildings b ON f.building_id = b.id
                LEFT JOIN sites s ON b.site_id = s.id
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE f.id = ?
                LIMIT 1
            """, (floor_id,)).fetchone()

            if not floor_row:
                raise HTTPException(status_code=404, detail="Floor not found")

            conn.execute("""
                UPDATE devices
                SET
                    x = ?,
                    y = ?,
                    room_id = NULL,
                    floor_id = ?,
                    building_id = ?,
                    site_id = ?,
                    client_id = ?,
                    building = ?,
                    floor = ?,
                    room = NULL,
                    is_placed = 1
                WHERE device_id = ?
            """, (
                x,
                y,
                floor_row["floor_id"],
                floor_row["building_id"],
                floor_row["site_id"],
                floor_row["client_id"],
                floor_row["building_name"],
                floor_row["floor_name"],
                device_id
            ))

        conn.commit()

        updated = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        new_device = dict(updated)

        tracked_fields = [
            "x",
            "y",
            "client_id",
            "site_id",
            "building_id",
            "floor_id",
            "room_id",
        ]

        changed_fields = {}

        for field in tracked_fields:
            if old_device.get(field) != new_device.get(field):
                changed_fields[field] = {
                    "old": old_device.get(field),
                    "new": new_device.get(field)
                }

        if changed_fields:
            device_scope = get_device_scope_for_audit(conn, device_id)

            node_label = (
                new_device.get("label")
                or new_device.get("device_id")
                or device_id
            )

            message = (
                f"Node {node_label} was moved from "
                f"({old_device.get('x')}, {old_device.get('y')}) "
                f"to ({new_device.get('x')}, {new_device.get('y')})."
            )

            if old_device.get("room_id") != new_device.get("room_id"):
                message += (
                    f" Room changed from {old_device.get('room_id')} "
                    f"to {new_device.get('room_id')}."
                )

            log_audit_event(
                conn,
                action="move_device",
                target_type="device",
                target_id=device_id,
                message=message,
                details={
                    "changed_fields": changed_fields,
                    "old": old_device,
                    "new": new_device
                },
                client_id=device_scope.get("client_id"),
                site_id=device_scope.get("site_id"),
                building_id=device_scope.get("building_id"),
                floor_id=device_scope.get("floor_id"),
                room_id=device_scope.get("room_id"),
                device_id=device_scope.get("device_id")
            )

        conn.close()

        return {
            "status": "updated" if changed_fields else "no_change",
            "device_id": device_id,
            "x": x,
            "y": y,
            "client_id": new_device.get("client_id"),
            "site_id": new_device.get("site_id"),
            "building_id": new_device.get("building_id"),
            "floor_id": new_device.get("floor_id"),
            "room_id": new_device.get("room_id"),
            "changed_fields": changed_fields
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/devices/{device_id}/capabilities")
def set_device_capabilities(
    device_id: str,
    data: dict,
):
    """
    Save capabilities safely.

    Profile-assigned devices are controlled by their sensor profile.
    Legacy devices without a profile temporarily retain the original
    manual-capability behavior.
    """

    capabilities = data.get("capabilities")

    if (
        not isinstance(capabilities, list)
        or len(capabilities) == 0
    ):
        raise HTTPException(
            status_code=400,
            detail="capabilities must be a non-empty list",
        )

    # ---------------------------------------------------------
    # 1. Normalize submitted capability keys
    # ---------------------------------------------------------

    submitted_capabilities = []

    for value in capabilities:
        capability = str(
            value or ""
        ).strip().lower()

        if not capability:
            raise HTTPException(
                status_code=400,
                detail="Capability values cannot be empty",
            )

        if not PROFILE_KEY_PATTERN.fullmatch(
            capability
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid capability key: {capability}"
                ),
            )

        if capability not in submitted_capabilities:
            submitted_capabilities.append(
                capability
            )

    conn = db()
    try:

        profile = None
        profile_controlled = False
        final_capabilities = []
        final_node_type = ""
        formatter_mode = ""

        try:
            # -----------------------------------------------------
            # 2. Load the complete device record
            # -----------------------------------------------------

            device_row = conn.execute(
                """
            SELECT *
            FROM devices
            WHERE device_id = ?
            LIMIT 1
            """,
                (device_id,),
            ).fetchone()

            if not device_row:
                raise HTTPException(
                    status_code=404,
                    detail="Device not found",
                )

            device = dict(device_row)

            profile_identifier = (
                device.get("profile_id")
                or device.get("profile_code")
            )

            # =====================================================
            # 3. PROFILE-CONTROLLED DEVICE
            # =====================================================

            if profile_identifier:
                profile_controlled = True
                formatter_mode = "sensor_profile"

                profile = get_sensor_profile_detail(
                    conn,
                    profile_identifier,
                    include_formatter=True,
                )

                if not profile:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": (
                                "The assigned sensor profile "
                                "could not be found"
                            ),
                            "device_id": device_id,
                            "profile_id": device.get(
                                "profile_id"
                            ),
                            "profile_code": device.get(
                                "profile_code"
                            ),
                        },
                    )

                device_profile_code = str(
                    device.get("profile_code") or ""
                ).strip().upper()

                current_profile_code = str(
                    profile.get("profile_code") or ""
                ).strip().upper()

                if (
                    device_profile_code
                    != current_profile_code
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": (
                                "Device profile assignment does "
                                "not match the profile record"
                            ),
                            "device_profile_code": (
                                device_profile_code
                            ),
                            "database_profile_code": (
                                current_profile_code
                            ),
                        },
                    )

                device_profile_version = int(
                    device.get("profile_version") or 0
                )

                current_profile_version = int(
                    profile.get("profile_version") or 0
                )

                if (
                    device_profile_version
                    != current_profile_version
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": (
                                "The device is assigned to an "
                                "older profile version and must "
                                "be reprovisioned"
                            ),
                            "device_profile_version": (
                                device_profile_version
                            ),
                            "current_profile_version": (
                                current_profile_version
                            ),
                        },
                    )

                if not bool(profile.get("enabled")):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The assigned sensor profile is "
                            "disabled"
                        ),
                    )

                if (
                    str(profile.get("status") or "")
                    .strip()
                    .lower()
                    != "active"
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The assigned sensor profile is "
                            "not active"
                        ),
                    )

                final_capabilities = (
                    profile_unique_string_list(
                        profile.get("capabilities", [])
                    )
                )

                if not final_capabilities:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The assigned profile has no "
                            "capabilities"
                        ),
                    )

                # Administrators cannot override profile capabilities
                # from the legacy capabilities page.
                if (
                    set(submitted_capabilities)
                    != set(final_capabilities)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": (
                                "Capabilities are controlled by "
                                "the assigned sensor profile"
                            ),
                            "profile_code": (
                                current_profile_code
                            ),
                            "submitted_capabilities": (
                                submitted_capabilities
                            ),
                            "profile_capabilities": (
                                final_capabilities
                            ),
                            "action": (
                                "Edit or assign a different "
                                "sensor profile instead"
                            ),
                        },
                    )

                final_node_type = str(
                    profile.get("node_type") or ""
                ).strip().lower()

                if not final_node_type:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The assigned sensor profile has "
                            "no node type"
                        ),
                    )

            # =====================================================
            # 4. LEGACY DEVICE WITHOUT PROFILE
            # =====================================================

            else:
                formatter_mode = "legacy_node_type"

                legacy_allowed_capabilities = {
                    "environment",
                    "energy",
                    "safety",
                    "occupancy",
                }

                for capability in submitted_capabilities:
                    if (
                        capability
                        not in legacy_allowed_capabilities
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Legacy devices support only: "
                                + ", ".join(
                                    sorted(
                                        legacy_allowed_capabilities
                                    )
                                )
                            ),
                        )

                final_capabilities = (
                    submitted_capabilities
                )

                if len(final_capabilities) > 1:
                    final_node_type = "multi"
                else:
                    final_node_type = (
                        final_capabilities[0]
                    )

            # -----------------------------------------------------
            # 5. Replace stored capability rows
            # -----------------------------------------------------

            conn.execute(
                """
            DELETE FROM device_capabilities
            WHERE device_id = ?
            """,
                (device_id,),
            )

            for capability in final_capabilities:
                conn.execute(
                    """
                INSERT OR IGNORE INTO
                device_capabilities(
                    device_id,
                    capability
                )
                VALUES (?, ?)
                """,
                    (
                        device_id,
                        capability,
                    ),
                )

            # For profile-controlled devices, this also repairs any
            # old node-type drift using the canonical profile value.
            conn.execute(
                """
            UPDATE devices
            SET node_type = ?
            WHERE device_id = ?
            """,
                (
                    final_node_type,
                    device_id,
                ),
            )

            conn.commit()

        except HTTPException:
            conn.rollback()
            raise

        except Exception as exc:
            conn.rollback()

            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to save device capabilities: "
                    f"{exc}"
                ),
            )

        finally:
            conn.close()

        # ---------------------------------------------------------
        # 6. Synchronize the appropriate TTN formatter
        # ---------------------------------------------------------

        formatter_synced = False
        formatter_error = None
        formatter_result = None

        try:
            if profile_controlled:
                formatter_result = (
                    set_device_formatter_from_profile(
                        device_id=device_id,
                        profile=profile,
                    )
                )

                formatter_synced = True

            else:
                # Temporary compatibility for old devices that have
                # not yet been migrated to sensor profiles.
                if final_node_type in FORMATTERS:
                    set_device_formatter(
                        device_id,
                        final_node_type,
                    )

                    formatter_synced = True

                else:
                    formatter_error = (
                        "No legacy formatter found for "
                        f"node_type: {final_node_type}"
                    )

        except Exception as exc:
            formatter_error = str(exc)

        return {
            "status": "saved",
            "device_id": device_id,
            "profile_controlled": profile_controlled,
            "profile_code": (
                profile.get("profile_code")
                if profile_controlled
                else None
            ),
            "profile_version": (
                profile.get("profile_version")
                if profile_controlled
                else None
            ),
            "node_type": final_node_type,
            "capabilities": final_capabilities,
            "formatter_mode": formatter_mode,
            "formatter_synced": formatter_synced,
            "formatter_error": formatter_error,
            "formatter_result": formatter_result,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/devices/{device_id}/capabilities")
def get_device_capabilities(device_id: str):
    conn = db()
    try:

        device = conn.execute("""
        SELECT *
        FROM devices
        WHERE device_id = ?
    """, (device_id,)).fetchone()

        if not device:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Device not found"
            )

        rows = conn.execute("""
        SELECT capability
        FROM device_capabilities
        WHERE device_id = ?
        ORDER BY capability
    """, (device_id,)).fetchall()

        conn.close()

        return {
            "device_id": device_id,
            "node_type": device["node_type"],
            "capabilities": [r["capability"] for r in rows]
        }
    finally:
        conn.close()
@router.post("/devices/{device_id}/sync-formatter")
def sync_device_formatter(device_id: str):
    """
    Synchronize a TTN formatter using the sensor profile currently
    assigned to the device.
    """

    conn = db()
    try:

        try:
            device_row = conn.execute(
                """
            SELECT *
            FROM devices
            WHERE device_id = ?
            LIMIT 1
            """,
                (device_id,),
            ).fetchone()

            if not device_row:
                raise HTTPException(
                    status_code=404,
                    detail="Device not found",
                )

            device = dict(device_row)

            profile_identifier = (
                device.get("profile_id")
                or device.get("profile_code")
            )

            if not profile_identifier:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Device has no assigned sensor profile"
                        ),
                        "device_id": device_id,
                        "action": (
                            "Provision the device using a sensor "
                            "profile before synchronizing its "
                            "formatter"
                        ),
                    },
                )

            profile = get_sensor_profile_detail(
                conn,
                profile_identifier,
                include_formatter=True,
            )

            if not profile:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "The device's assigned sensor profile "
                            "could not be found"
                        ),
                        "device_id": device_id,
                        "profile_id": device.get("profile_id"),
                        "profile_code": device.get(
                            "profile_code"
                        ),
                    },
                )

            stored_device_profile_code = str(
                device.get("profile_code") or ""
            ).strip().upper()

            current_profile_code = str(
                profile.get("profile_code") or ""
            ).strip().upper()

            if (
                stored_device_profile_code
                != current_profile_code
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Device profile assignment does not "
                            "match the selected database profile"
                        ),
                        "device_profile_code": (
                            stored_device_profile_code
                        ),
                        "database_profile_code": (
                            current_profile_code
                        ),
                    },
                )

            device_profile_version = int(
                device.get("profile_version") or 0
            )

            current_profile_version = int(
                profile.get("profile_version") or 0
            )

            if (
                device_profile_version
                != current_profile_version
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "The device is assigned to an older "
                            "profile version and must be "
                            "reprovisioned"
                        ),
                        "device_profile_version": (
                            device_profile_version
                        ),
                        "current_profile_version": (
                            current_profile_version
                        ),
                    },
                )

            if not bool(profile.get("enabled")):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The assigned sensor profile is disabled"
                    ),
                )

            if (
                str(profile.get("status") or "")
                .strip()
                .lower()
                != "active"
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The assigned sensor profile is not active"
                    ),
                )

        finally:
            conn.close()

        result = set_device_formatter_from_profile(
            device_id=device_id,
            profile=profile,
        )

        return {
            **result,
            "node_type": device["node_type"],
            "configuration_status": device[
                "configuration_status"
            ],
            "configuration_checksum": device[
                "configuration_checksum"
            ],
        }
    finally:
        conn.close()
@router.get("/api/devices/{device_id}/history")
def get_device_history(
    device_id: str,
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=5000),
    order: str = Query("asc"),
):
    conn = db()
    try:
        order_direction = "DESC" if order.lower() == "desc" else "ASC"
        query = "SELECT telemetry, timestamp FROM historical_telemetry WHERE device_id = ?"
        params = [device_id]
        
        if from_ts:
            query += " AND timestamp >= ?"
            params.append(from_ts)
        if to_ts:
            query += " AND timestamp <= ?"
            params.append(to_ts)
            
        query += f" ORDER BY timestamp {order_direction} LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            telemetry_data = row["telemetry"]
            if isinstance(telemetry_data, str):
                try:
                    telemetry_data = json.loads(telemetry_data)
                except Exception:
                    pass
            history.append({
                "telemetry": telemetry_data,
                "timestamp": row["timestamp"]
            })
            
        return {"status": "success", "device_id": device_id, "count": len(history), "history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
@router.post("/api/devices/bulk-import")
def bulk_import_devices(file: UploadFile = File(...)):
    import codecs
    
    conn = db()
    try:
        cursor = conn.cursor()
    
        csvReader = csv.DictReader(codecs.iterdecode(file.file, 'utf-8'))
        success_count = 0
        errors = []
    
        for row in csvReader:
            try:
                dev_eui = row.get('dev_eui', '').strip().upper()
                app_key = row.get('app_key', '').strip().upper()
                building_name = row.get('building', '').strip()
                floor_name = row.get('floor', '').strip()
                room_name = row.get('room', '').strip()
                profile_id = row.get('profile_id', '').strip()
                label = row.get('label', '').strip()
            
                if not dev_eui or not profile_id or not building_name or not floor_name or not room_name:
                    errors.append(f"Row missing required fields (dev_eui, profile_id, building, floor, room): {row}")
                    continue
                
                if not app_key:
                    app_key = secrets.token_hex(16).upper()
                
                chip_mac = f"BULK-{dev_eui}"
                device_id = f"node-{dev_eui.lower()}"
            
                # Resolve site_id from row or database default
                raw_site_id = row.get('site_id', '').strip()
                if raw_site_id and raw_site_id.isdigit():
                    site_id = int(raw_site_id)
                else:
                    site_row = cursor.execute("SELECT id FROM sites LIMIT 1").fetchone()
                    site_id = site_row["id"] if site_row else 1

                # Site-scoped building lookup or creation
                b_row = cursor.execute("SELECT id FROM buildings WHERE site_id = ? AND name = ?", (site_id, building_name)).fetchone()
                if not b_row:
                    b_id = cursor.execute("INSERT INTO buildings (site_id, name) VALUES (?, ?)", (site_id, building_name)).lastrowid
                else:
                    b_id = b_row["id"]
                
                f_row = cursor.execute("SELECT id FROM floors WHERE building_id = ? AND name = ?", (b_id, floor_name)).fetchone()
                if not f_row:
                    f_id = cursor.execute("INSERT INTO floors (building_id, name, floor_number) VALUES (?, ?, ?)", (b_id, floor_name, floor_name)).lastrowid
                else:
                    f_id = f_row["id"]
                
                r_row = cursor.execute("SELECT id FROM rooms WHERE floor_id = ? AND room_name = ?", (f_id, room_name)).fetchone()
                if not r_row:
                    room_id = cursor.execute("INSERT INTO rooms (floor_id, room_name, polygon_points, x, y) VALUES (?, ?, '[]', 0, 0)", (f_id, room_name)).lastrowid
                else:
                    room_id = r_row["id"]
                
                # Fetch profile
                profile = cursor.execute("SELECT * FROM sensor_profiles WHERE id = ?", (profile_id,)).fetchone()
                if not profile:
                    errors.append(f"Profile {profile_id} not found for {dev_eui}")
                    continue
                
                # Insert or update device using UPSERT
                cursor.execute("""
                INSERT INTO devices 
                (chip_mac, device_id, dev_eui, app_key, join_eui, label, room_id, profile_id, profile_code, node_type, configuration_status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy')
                ON CONFLICT(chip_mac) DO UPDATE SET
                    device_id = excluded.device_id,
                    dev_eui = excluded.dev_eui,
                    app_key = excluded.app_key,
                    join_eui = excluded.join_eui,
                    label = excluded.label,
                    room_id = excluded.room_id,
                    profile_id = excluded.profile_id,
                    profile_code = excluded.profile_code,
                    node_type = excluded.node_type,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                    chip_mac, device_id, dev_eui, app_key, "0000000000000000",
                    label, room_id, profile_id, profile['profile_code'], profile['node_type']
                ))
            
                cursor.execute("""
                INSERT OR IGNORE INTO device_latest_telemetry (device_id, telemetry)
                VALUES (?, '{}')
            """, (device_id,))
            
                success_count += 1
            
            except Exception as e:
                errors.append(f"Error on {row.get('dev_eui')}: {e!s}")
            
        conn.commit()
        conn.close()
    
        # A caller checking only the status code could not previously tell a
        # clean import from one where every single row failed.
        if success_count == 0 and errors:
            status_code = 400
            status = "failed"
        elif errors:
            status_code = 207          # Multi-Status: imported with errors
            status = "partial"
        else:
            status_code = 200
            status = "success"

        return JSONResponse(status_code=status_code, content={
            "status": status,
            "imported": success_count,
            "errors": errors
        })
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
