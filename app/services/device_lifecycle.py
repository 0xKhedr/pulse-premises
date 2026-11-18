"""Remove network admission before deleting local data; block delayed uplinks."""
from __future__ import annotations

import requests
from fastapi import HTTPException

from app.config import settings
from app.services.ttn import ttn_client


def clear_admission_block(conn, device_id: str, dev_eui: str) -> None:
    conn.execute("""DELETE FROM device_admission_blocks WHERE application_id=?
        AND (device_id=? OR (dev_eui IS NOT NULL AND dev_eui=? COLLATE NOCASE))""",
        (settings.TTN_APP_ID, device_id, dev_eui))


def remove_device(conn, device_id: str, dev_eui: str, actor: str) -> dict:
    if settings.PREVIEW_MODE:
        raise HTTPException(409, "Device deletion is unavailable in preview mode")
    local = conn.execute("""SELECT * FROM devices WHERE device_id=?
        OR (? != '' AND dev_eui=? COLLATE NOCASE)
        ORDER BY CASE WHEN device_id=? THEN 0 ELSE 1 END LIMIT 1""",
        (device_id, dev_eui, dev_eui, device_id)).fetchone()
    local_id = local["device_id"] if local else device_id
    expected_eui = str((local["dev_eui"] if local else dev_eui) or "").upper()
    remote_id = (local["ttn_device_id"] if local else None) or device_id
    conn.execute("BEGIN IMMEDIATE")
    registration = conn.execute("""SELECT status,management_action FROM device_enrollment_candidates
        WHERE application_id=? AND (device_id=? OR (? != '' AND dev_eui=? COLLATE NOCASE))""",
        (settings.TTN_APP_ID, device_id, expected_eui, expected_eui)).fetchone()
    if registration and registration["status"] == "registering" and registration["management_action"] != "revoking":
        conn.rollback()
        raise HTTPException(409, "Wait for enrollment registration to finish before deleting this device")
    # Commit the block before network I/O: queued webhooks must not recreate a
    # deleted device, even if TTN is partially deleted or a request times out.
    for identity in {local_id, remote_id, device_id}:
        conn.execute("""INSERT INTO device_admission_blocks(application_id,device_id,dev_eui,actor)
            VALUES (?,?,?,?) ON CONFLICT(application_id,device_id) DO UPDATE SET
            dev_eui=excluded.dev_eui,actor=excluded.actor,state='deleting',updated_at=CURRENT_TIMESTAMP""",
            (settings.TTN_APP_ID, identity, expected_eui or None, actor))
    conn.commit()
    try:
        try:
            remote = ttn_client.get_application_device(remote_id)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            remote = None
        if remote and expected_eui and str(remote.get("ids", {}).get("dev_eui") or "").upper() != expected_eui:
            raise HTTPException(409, "TTN device identity differs from SBMS. Check its DevEUI before deleting.")
        # 404 is successful for each component, allowing interrupted deletes to resume.
        ttn_client.delete_device(remote_id)
    except Exception as exc:
        message = (exc.detail if isinstance(exc, HTTPException) else
                   "TTN deletion was not completed. The SBMS record is retained; retry deletion to finish removing all TTN components.")
        conn.execute("UPDATE devices SET deletion_error=? WHERE device_id=?", (message, local_id))
        conn.execute("""UPDATE device_admission_blocks SET state='failed',updated_at=CURRENT_TIMESTAMP
            WHERE application_id=? AND (device_id IN (?,?,?) OR (? != '' AND dev_eui=? COLLATE NOCASE))""",
            (settings.TTN_APP_ID, local_id, remote_id, device_id, expected_eui, expected_eui))
        conn.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, message) from exc
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Keep alarm and configuration history for audit; remove current state
        # that could otherwise be attached to a later re-enrollment.
        for table in ("device_latest_telemetry", "device_capabilities", "profile_alarm_runtime_state"):
            conn.execute(f"DELETE FROM {table} WHERE device_id=?", (local_id,))
        conn.execute("""UPDATE alarm_history SET resolved=1,resolved_by=?,
            resolved_at=CURRENT_TIMESTAMP,resolved_reason='Device deleted'
            WHERE device_id=? AND resolved=0""", (actor, local_id))
        conn.execute("DELETE FROM devices WHERE device_id=?", (local_id,))
        conn.execute("""UPDATE device_enrollment_candidates SET status='rejected',
            decided_by=?,decided_at=CURRENT_TIMESTAMP,registration_error=NULL,
            management_action=NULL,management_token=NULL,management_started_at=NULL,
            ttn_checked_at=NULL,ttn_check_json=NULL
            WHERE application_id=? AND (device_id IN (?,?) OR (? != '' AND dev_eui=? COLLATE NOCASE))""",
            (actor, settings.TTN_APP_ID, local_id, device_id, expected_eui, expected_eui))
        conn.execute("""UPDATE device_admission_blocks SET state='deleted',updated_at=CURRENT_TIMESTAMP
            WHERE application_id=? AND (device_id IN (?,?,?) OR (? != '' AND dev_eui=? COLLATE NOCASE))""",
            (settings.TTN_APP_ID, local_id, remote_id, device_id, expected_eui, expected_eui))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"status": "deleted", "device_id": local_id, "ttn_device_id": remote_id, "ttn_deleted": True}
