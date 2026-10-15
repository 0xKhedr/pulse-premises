"""Change an existing device's decoding profile without reprovisioning its identity."""

import logging

from fastapi import HTTPException

from app.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def mark_formatter_synced(device_id: str, profile: dict) -> bool:
    """A late upstream response must not mark a newer assignment as applied."""
    conn = get_db_connection()
    try:
        with conn:
            result = conn.execute(
                """UPDATE devices
                   SET configuration_status = 'applied', configuration_error = NULL,
                       configuration_updated_at = CURRENT_TIMESTAMP
                   WHERE device_id = ? AND profile_id = ? AND profile_version = ?
                     AND configuration_checksum = ?""",
                (device_id, profile["id"], profile["profile_version"],
                 profile.get("schema_checksum") or ""),
            )
        return result.rowcount > 0
    finally:
        conn.close()


def assign_device_profile(device_id: str, profile_id: int, actor: str) -> dict:
    from app.main import (
        get_sensor_profile_detail,
        log_audit_event,
        record_device_configuration_history,
        save_device_capabilities,
        set_device_formatter_from_profile,
        sync_tb_attributes_from_profile,
    )

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Device not found")
        device = dict(row)
        profile = get_sensor_profile_detail(conn, profile_id, include_formatter=True)
        if profile is None:
            raise HTTPException(status_code=404, detail="Sensor profile not found")
        if not profile["enabled"] or str(profile["status"]).lower() != "active":
            raise HTTPException(status_code=409, detail="Choose an enabled, active sensor profile")
        if not profile["fields"]:
            raise HTTPException(status_code=409, detail="The sensor profile has no telemetry fields")

        profile.update(
            profile_id=profile["id"],
            firmware_version=device.get("firmware_version") or "1.0.0",
            configuration_status="formatter_pending",
            configuration_checksum=profile.get("schema_checksum") or "",
        )
        with conn:
            conn.execute(
                """UPDATE devices SET
                       profile_id = ?, profile_code = ?, profile_version = ?, payload_version = ?,
                       node_type = ?, configuration_checksum = ?,
                       configuration_status = 'formatter_pending', configuration_error = NULL,
                       configuration_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                   WHERE device_id = ?""",
                (profile["id"], profile["profile_code"], profile["profile_version"],
                 profile["payload_version"], profile["node_type"],
                 profile["configuration_checksum"], device_id),
            )
            save_device_capabilities(conn, device_id, profile["capabilities"])
            updated = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            record_device_configuration_history(
                conn, updated, profile, "profile_changed", source="admin_profile_assignment",
                requested_by=actor,
            )

        log_audit_event(
            conn, action="device_profile_changed", actor=actor, target_type="device",
            target_id=device_id, device_id=device_id,
            details={"previous_profile_code": device.get("profile_code"),
                     "profile_code": profile["profile_code"],
                     "profile_version": profile["profile_version"]},
        )
        updated = dict(updated)
    finally:
        conn.close()

    # The local assignment is durable before contacting optional upstreams.
    # A failure is visible and retryable; it must never look like a full success.
    formatter_synced = False
    try:
        set_device_formatter_from_profile(device_id=device_id, profile=profile)
        formatter_synced = mark_formatter_synced(device_id, profile)
        if formatter_synced:
            updated["configuration_status"] = "applied"
    except Exception:
        logger.warning("TTN formatter sync failed after profile assignment for %s", device_id)

    thingsboard_synced = False
    try:
        sync_tb_attributes_from_profile(device_row=updated, profile=profile)
        thingsboard_synced = True
    except Exception:
        logger.info("ThingsBoard profile attribute sync failed for %s", device_id)

    return {
        "status": "profile_changed", "device_id": device_id,
        "profile_id": profile["id"], "profile_code": profile["profile_code"],
        "profile_name": profile["profile_name"], "profile_version": profile["profile_version"],
        "payload_version": profile["payload_version"], "node_type": profile["node_type"],
        "capabilities": profile["capabilities"], "formatter_synced": formatter_synced,
        "thingsboard_synced": thingsboard_synced,
        "configuration_status": "applied" if formatter_synced else "formatter_pending",
    }
