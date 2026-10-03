import json
import logging
import sqlite3

from fastapi import HTTPException

from app.config import settings
from app.db.connection import get_db_connection
from app.schemas.core import Device

logger = logging.getLogger(__name__)

ALLOWED_NODE_TYPES = {"environment", "energy", "safety", "occupancy", "multi"}


def infer_icon_type(node_type: str) -> str:
    mapping = {
        "environment": "temp",
        "energy": "lightning",
        "safety": "shield",
        "occupancy": "user",
        "multi": "cpu",
    }
    return mapping.get(str(node_type).lower().strip(), "cpu")


def resolve_sensor_profile_for_provision(conn: sqlite3.Connection, device: Device) -> dict:
    if device.profile_id is None:
        raise HTTPException(status_code=400, detail="profile_id is required for profile-driven provisioning")
    
    submitted_profile_code = str(device.profile_code or "").strip().upper()
    if not submitted_profile_code:
        raise HTTPException(status_code=400, detail="profile_code is required for profile-driven provisioning")

    row = conn.execute("SELECT * FROM sensor_profiles WHERE id = ?", (device.profile_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Sensor profile not found: {device.profile_id}")

    profile = dict(row)
    profile["profile_id"] = profile["id"]
    profile["firmware_version"] = profile.get("firmware_version") or "1.0.0"
    profile["configuration_status"] = profile.get("configuration_status") or "applied"
    profile["configuration_checksum"] = profile.get("configuration_checksum") or ""
    stored_profile_code = str(profile.get("profile_code") or "").strip().upper()
    if submitted_profile_code != stored_profile_code:
        raise HTTPException(status_code=409, detail={
            "message": "profile_id and profile_code do not match",
            "submitted_profile_id": device.profile_id,
            "submitted_profile_code": submitted_profile_code,
            "stored_profile_code": stored_profile_code
        })

    if not bool(profile.get("enabled")):
        raise HTTPException(status_code=409, detail=f"Sensor profile is disabled: {stored_profile_code}")

    if str(profile.get("status") or "").strip().lower() != "active":
        raise HTTPException(status_code=409, detail=f"Sensor profile must be active before provisioning: {stored_profile_code}")

    stored_profile_version = int(profile.get("profile_version") or 0)
    stored_payload_version = int(profile.get("payload_version") or 0)

    if device.profile_version != stored_profile_version:
        raise HTTPException(status_code=409, detail={
            "message": "Profile version is outdated or invalid",
            "submitted": device.profile_version,
            "current": stored_profile_version
        })

    if device.payload_version != stored_payload_version:
        raise HTTPException(status_code=409, detail={
            "message": "Payload version does not match the profile",
            "submitted": device.payload_version,
            "current": stored_payload_version
        })

    stored_encoder_key = str(profile.get("payload_encoder_key") or "").strip()
    submitted_encoder_key = str(device.payload_encoder_key or "").strip()
    if submitted_encoder_key != stored_encoder_key:
        raise HTTPException(status_code=409, detail={
            "message": "Payload encoder does not match the profile",
            "submitted": submitted_encoder_key,
            "current": stored_encoder_key
        })

    stored_interval = int(profile.get("uplink_interval_seconds") or 0)
    if device.uplink_interval_seconds != stored_interval:
        raise HTTPException(status_code=409, detail={
            "message": "Uplink interval does not match the selected profile",
            "submitted": device.uplink_interval_seconds,
            "current": stored_interval
        })

    stored_node_type = str(profile.get("node_type") or "").strip().lower()
    submitted_node_type = str(device.node_type or "").strip().lower()
    if submitted_node_type != stored_node_type:
        raise HTTPException(status_code=409, detail={
            "message": "node_type does not match the sensor profile",
            "submitted": submitted_node_type,
            "current": stored_node_type
        })

    capabilities_raw = profile.get("capabilities_json") or profile.get("capabilities") or "[]"
    if isinstance(capabilities_raw, str):
        try:
            profile["capabilities"] = json.loads(capabilities_raw)
        except Exception:
            profile["capabilities"] = []
    else:
        profile["capabilities"] = list(capabilities_raw)

    return profile


def validate_provision_location_and_type(conn: sqlite3.Connection, device: Device) -> str:
    node_type = device.node_type.lower().strip()
    if node_type not in ALLOWED_NODE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid node_type. Allowed values: {sorted(ALLOWED_NODE_TYPES)}",
        )

    if device.room_id is not None:
        room_row = conn.execute("SELECT * FROM rooms WHERE id = ?", (device.room_id,)).fetchone()
    else:
        room_row = conn.execute("""
            SELECT r.* FROM rooms r
            LEFT JOIN floors f ON r.floor_id = f.id
            LEFT JOIN buildings b ON f.building_id = b.id
            WHERE b.name = ? AND f.name = ? AND r.room_name = ?
        """, (device.building, device.floor, device.room)).fetchone()

    if not room_row:
        raise HTTPException(
            status_code=400,
            detail=f"Room not found in database: building={device.building}, floor={device.floor}, room={device.room}",
        )

    return node_type


def get_room_by_id_for_provision(conn: sqlite3.Connection, room_id: int):
    row = conn.execute("""
        SELECT
            r.id AS room_id,
            r.room_name,
            b.name AS building,
            f.name AS floor,
            r.x,
            r.y,
            r.floor_id,
            f.building_id,
            b.site_id,
            s.client_id
        FROM rooms r
        LEFT JOIN floors f ON r.floor_id = f.id
        LEFT JOIN buildings b ON f.building_id = b.id
        LEFT JOIN sites s ON b.site_id = s.id
        WHERE r.id = ?
        LIMIT 1
    """, (room_id,)).fetchone()
    return row


def build_profile_provision_response(status: str, device_row: sqlite3.Row, profile: dict, capabilities: list):
    return {
        "status": status,
        "device_id": device_row["device_id"],
        "dev_eui": device_row["dev_eui"],
        "join_eui": device_row["join_eui"],
        "app_key": device_row["app_key"],
        "profile_id": profile["profile_id"],
        "profile_code": profile["profile_code"],
        "profile_name": profile["profile_name"],
        "profile_version": profile["profile_version"],
        "node_type": profile["node_type"],
        "capabilities": capabilities,
        "payload_version": profile["payload_version"],
        "payload_encoder_key": profile["payload_encoder_key"],
        "f_port": profile.get("f_port", 1),
        "uplink_interval_seconds": profile["uplink_interval_seconds"],
        "firmware_version": profile.get("firmware_version", "1.0.0"),
        "configuration_status": profile.get("configuration_status", "applied"),
        "configuration_checksum": profile.get("configuration_checksum", ""),
        "building": device_row["building"],
        "floor": device_row["floor"],
        "room": device_row["room"],
        "room_id": device_row["room_id"],
        "label": device_row["label"],
        "x": device_row["x"],
        "y": device_row["y"],
        "icon_type": device_row["icon_type"],
    }


def provision(device: Device) -> dict:
    from app.main import (
        JOIN_EUI,
        LORAWAN_APP_KEY,
        get_auto_room_position,
        get_existing_device,
        make_device_id,
        normalize_eui,
        normalize_mac,
        record_device_configuration_history,
        register_device_in_ttn,
        save_device,
        save_device_capabilities,
        save_device_profile_assignment,
        set_device_formatter_from_profile,
        sync_tb_attributes_from_profile,
        update_existing_device_metadata,
        validate_config,
    )
    from app.services.lorawan_identity import derive_dev_eui_from_mac

    try:
        validate_config()
        chip_mac = normalize_mac(device.chip_mac)
        join_eui = normalize_eui(JOIN_EUI, 16)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    conn = get_db_connection()

    try:
        profile = resolve_sensor_profile_for_provision(conn, device)
        node_type = profile["node_type"]
        capabilities = profile["capabilities"]

        device.node_type = node_type
        device.capabilities = capabilities

        if device.room_id is None:
            raise HTTPException(status_code=400, detail="room_id is required for profile-driven provisioning")

        resolved_room = get_room_by_id_for_provision(conn, device.room_id)
        if not resolved_room:
            raise HTTPException(status_code=400, detail=f"Room not found by room_id: {device.room_id}")

        device.building = resolved_room["building"]
        device.floor = resolved_room["floor"]
        device.room = resolved_room["room_name"]
        device.room_id = resolved_room["room_id"]

        validate_provision_location_and_type(conn, device)

        clean_label = str(device.label or "").strip()
        label = clean_label if clean_label else f"{profile['profile_name']} - {device.room}"
        final_icon_type = profile.get("icon_type") or infer_icon_type(node_type)

        existing = get_existing_device(conn, chip_mac)

        if existing:
            shared_dev_eui = derive_dev_eui_from_mac(
                chip_mac, namespace=settings.TTN_APP_ID
            )
            conn.execute(
                """
                UPDATE devices
                SET dev_eui = ?, join_eui = ?, app_key = ?
                WHERE chip_mac = ?
                """,
                (shared_dev_eui, join_eui, LORAWAN_APP_KEY, chip_mac),
            )
            if device.x is not None and device.y is not None:
                final_x, final_y = device.x, device.y
            else:
                final_x, final_y = get_auto_room_position(
                    conn=conn, building=device.building, floor=device.floor, room=device.room, exclude_chip_mac=chip_mac
                )

            update_existing_device_metadata(
                conn=conn, chip_mac=chip_mac, node_type=node_type, building=device.building,
                floor=device.floor, room=device.room, label=label, x=final_x, y=final_y, icon_type=final_icon_type
            )

            conn.execute("""
                UPDATE devices
                SET client_id = ?, site_id = ?, building_id = ?, floor_id = ?, room_id = ?
                WHERE chip_mac = ?
            """, (
                resolved_room["client_id"], resolved_room["site_id"],
                resolved_room["building_id"], resolved_room["floor_id"],
                resolved_room["room_id"], chip_mac
            ))

            save_device_profile_assignment(conn=conn, chip_mac=chip_mac, profile=profile)
            updated = get_existing_device(conn, chip_mac)
            save_device_capabilities(conn, updated["device_id"], capabilities)
            record_device_configuration_history(
                conn=conn, device_row=updated, profile=profile,
                provision_status="existing_updated", source="lilygo_provisioning", requested_by=chip_mac
            )
            conn.commit()

            try:
                set_device_formatter_from_profile(device_id=updated["device_id"], profile=profile)
            except Exception as exc:
                logger.info("Profile formatter update failed: %s", exc)

            try:
                updated = get_existing_device(conn, chip_mac)
                tb_sync_result = sync_tb_attributes_from_profile(device_row=updated, profile=profile)
                logger.info("Profile-driven ThingsBoard sync: %s", tb_sync_result.get("status"))
            except Exception as exc:
                logger.info("Profile-driven ThingsBoard attribute sync failed: %s", exc)

            final_row = get_existing_device(conn, chip_mac)
            return build_profile_provision_response(
                status="existing_updated", device_row=final_row, profile=profile, capabilities=capabilities
            )

        # Create new device
        device_id = make_device_id(chip_mac)
        dev_eui = derive_dev_eui_from_mac(chip_mac, namespace=settings.TTN_APP_ID)
        app_key = LORAWAN_APP_KEY

        if device.x is not None and device.y is not None:
            final_x, final_y = device.x, device.y
        else:
            final_x, final_y = get_auto_room_position(
                conn=conn, building=device.building, floor=device.floor, room=device.room
            )

        register_device_in_ttn(
            device_id=device_id, dev_eui=dev_eui, join_eui=join_eui, app_key=app_key, profile=profile
        )

        save_device(
            conn=conn, chip_mac=chip_mac, device_id=device_id, dev_eui=dev_eui,
            join_eui=join_eui, app_key=app_key, node_type=node_type, building=device.building,
            floor=device.floor, room=device.room, label=label, x=final_x, y=final_y, icon_type=final_icon_type
        )

        conn.execute("""
            UPDATE devices
            SET client_id = ?, site_id = ?, building_id = ?, floor_id = ?, room_id = ?
            WHERE chip_mac = ?
        """, (
            resolved_room["client_id"], resolved_room["site_id"],
            resolved_room["building_id"], resolved_room["floor_id"],
            resolved_room["room_id"], chip_mac
        ))

        save_device_profile_assignment(conn=conn, chip_mac=chip_mac, profile=profile)
        save_device_capabilities(conn, device_id, capabilities)
        saved = get_existing_device(conn, chip_mac)
        record_device_configuration_history(
            conn=conn, device_row=saved, profile=profile,
            provision_status="created", source="lilygo_provisioning", requested_by=chip_mac
        )
        conn.commit()

        try:
            tb_sync_result = sync_tb_attributes_from_profile(device_row=saved, profile=profile)
            logger.info("Profile-driven ThingsBoard sync: %s", tb_sync_result.get("status"))
        except Exception as exc:
            logger.info("Profile-driven ThingsBoard attribute sync failed: %s", exc)

        final_row = get_existing_device(conn, chip_mac)
        return build_profile_provision_response(
            status="created", device_row=final_row, profile=profile, capabilities=capabilities
        )

    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        logger.info("Profile-driven provisioning failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Profile-driven provisioning failed: {exc}")
    finally:
        conn.close()
