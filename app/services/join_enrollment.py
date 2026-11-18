"""Discover verified radio identities; register only after portal approval.

Discovery windows are ephemeral. Candidates and administrator decisions live
in SQLite. No root key or raw join frame is stored in the approval queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from fastapi import HTTPException

from app.config import settings
from app.services.device_lifecycle import clear_admission_block, remove_device
from app.services.device_state import build_device_state
from app.services.lorawan_identity import (
    device_id_from_dev_eui,
    normalize_hex,
    recover_mac_from_dev_eui,
)
from app.services.lorawan_join import JoinRequest, app_key_bytes, verify_join_request_mic

logger = logging.getLogger(__name__)
REGISTRATION_LEASE_SECONDS = 600
PUBLIC_FIELDS = (
    "dev_eui", "join_eui", "device_id", "status", "first_seen_at", "last_seen_at",
    "seen_count", "gateway_id", "rssi", "snr", "decided_by", "decided_at",
    "registration_error", "online_at",
    "ttn_checked_at",
)


class Outcome(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    ALREADY_REGISTERED = "already_registered"
    WINDOW_CLOSED = "window_closed"
    QUOTA_EXCEEDED = "quota_exceeded"
    JOIN_EUI_MISMATCH = "join_eui_mismatch"
    BAD_MIC = "bad_mic"
    MISCONFIGURED = "misconfigured"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    dev_eui: str
    detail: str = ""


@dataclass
class _Window:
    opened_by: str = ""
    expires_at: float = 0.0
    discovered: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


_lock = threading.Lock()
_window = _Window()


def open_window(actor: str, *, seconds: int | None = None) -> dict:
    duration = int(settings.AUTO_ENROLLMENT_WINDOW_SECONDS if seconds is None else seconds)
    duration = max(1, min(duration, 3600))
    with _lock:
        global _window
        _window = _Window(opened_by=str(actor or "unknown"), expires_at=time.time() + duration)
        return _status_locked(time.time())


def close_window(actor: str = "") -> dict:
    with _lock:
        global _window
        _window = _Window()
        logger.info("Enrollment discovery closed by %s", actor or "unknown")
        return _status_locked(time.time())


def window_status() -> dict:
    with _lock:
        return _status_locked(time.time())


def _status_locked(now: float) -> dict:
    remaining = max(0, int(_window.expires_at - now))
    return {
        "enabled": bool(settings.AUTO_ENROLLMENT_ENABLED),
        "open": remaining > 0,
        "opened_by": _window.opened_by if remaining else "",
        "seconds_remaining": remaining,
        "discovered": list(_window.discovered),
        "rejected": list(_window.rejected),  # Invalid MICs, not administrator rejections.
        "quota": int(settings.AUTO_ENROLLMENT_MAX_PER_WINDOW),
        "quota_used": len(_window.discovered),
    }


def _candidate(conn: sqlite3.Connection, dev_eui: str):
    return conn.execute(
        "SELECT * FROM device_enrollment_candidates WHERE application_id = ? AND dev_eui = ?",
        (settings.TTN_APP_ID, dev_eui),
    ).fetchone()


def _public_candidate(row, device: dict | None = None) -> dict:
    result = {key: row[key] for key in PUBLIC_FIELDS}
    # A crashed worker must not leave an approval permanently stuck. Only an
    # administrator click can retry an expired lease, never radio traffic.
    if row["status"] == "registering" and (row["registration_started_at"] or 0) < time.time() - REGISTRATION_LEASE_SECONDS:
        result["status"] = "registration_failed"
        result["registration_error"] = "Registration was interrupted. Retry registration; check TTN if a conflict is reported."
    if row["management_action"]:
        expired = (row["management_started_at"] or 0) < time.time() - REGISTRATION_LEASE_SECONDS
        result["status"] = "revocation_failed" if row["management_action"] == "revocation_failed" or expired else "revoking"
        if expired and not result["registration_error"]:
            result["registration_error"] = "Revocation was interrupted. Retry revocation to finish removing the TTN registration."
    try:
        result["ttn_check"] = json.loads(row["ttn_check_json"]) if row["ttn_check_json"] else None
    except ValueError:
        result["ttn_check"] = None
    try:
        decided = datetime.fromisoformat(str(row["decided_at"]).replace("Z", "+00:00"))
        if decided.tzinfo is None:
            decided = decided.replace(tzinfo=timezone.utc)
        waiting = max(0, int((datetime.now(timezone.utc) - decided).total_seconds()))
    except (ValueError, TypeError):
        waiting = 0
    result["waiting_seconds"] = waiting if result["status"] == "awaiting_join" else 0
    result["attention_required"] = result["status"] in {"registration_failed", "revocation_failed"} or (
        result["status"] == "awaiting_join" and waiting >= settings.AUTO_ENROLLMENT_JOIN_WAIT_SECONDS)
    result["local_device_id"] = device.get("device_id") if device else None
    result["connection_state"] = build_device_state(
        device or {}, stale_after_seconds=settings.DEVICE_STALE_AFTER_SECONDS,
        offline_after_seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS,
    )["connection_state"]
    return result


def list_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM device_enrollment_candidates WHERE application_id = ?
        ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'registration_failed' THEN 1
                 WHEN 'registering' THEN 2 WHEN 'awaiting_join' THEN 3
                 WHEN 'online' THEN 4 ELSE 5 END, last_seen_at DESC, id DESC""",
        (settings.TTN_APP_ID,),
    ).fetchall()
    devices = [dict(row) for row in conn.execute("""SELECT d.*, t.updated_at AS telemetry_updated_at
        FROM devices d LEFT JOIN device_latest_telemetry t ON t.device_id=d.device_id""")]
    by_id = {device["device_id"]: device for device in devices}
    by_eui = {str(device["dev_eui"]).upper(): device for device in devices if device["dev_eui"]}
    return [_public_candidate(row, by_eui.get(row["dev_eui"].upper()) or by_id.get(row["device_id"])) for row in rows]


def evaluate_join_request(
    conn: sqlite3.Connection, request: JoinRequest, *, gateway_id: str | None = None,
    rssi: float | None = None, snr: float | None = None,
) -> Decision:
    """A verified request can create a pending entry, never a TTN registration."""
    dev_eui = request.dev_eui
    if not settings.AUTO_ENROLLMENT_ENABLED:
        return Decision(Outcome.WINDOW_CLOSED, dev_eui, "discovery is disabled")
    with _lock:
        if _window.expires_at <= time.time():
            return Decision(Outcome.WINDOW_CLOSED, dev_eui, "no discovery window is open")
    try:
        expected = normalize_hex(settings.JOIN_EUI, 16, "JOIN_EUI")
        key = app_key_bytes(settings.LORAWAN_APP_KEY)
    except ValueError as exc:
        return Decision(Outcome.MISCONFIGURED, dev_eui, str(exc))
    if request.join_eui != expected:
        return Decision(Outcome.JOIN_EUI_MISMATCH, dev_eui)
    if not verify_join_request_mic(request, key):
        with _lock:
            if dev_eui not in _window.rejected and len(_window.rejected) < 100:
                _window.rejected.append(dev_eui)
        return Decision(Outcome.BAD_MIC, dev_eui, "MIC did not verify")

    with _lock:
        if _window.expires_at <= time.time():
            return Decision(Outcome.WINDOW_CLOSED, dev_eui)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = _candidate(conn, dev_eui)
            if row:
                # Never change an administrator's decision, even with a fresh MIC.
                conn.execute(
                    """UPDATE device_enrollment_candidates
                    SET last_seen_at = CURRENT_TIMESTAMP, seen_count = seen_count + 1,
                        gateway_id = COALESCE(?, gateway_id), rssi = COALESCE(?, rssi),
                        snr = COALESCE(?, snr), verified_key_hash = ?
                    WHERE id = ?""",
                    (gateway_id, rssi, snr, hashlib.sha256(key).hexdigest(), row["id"]),
                )
                conn.commit()
                outcome = Outcome.PENDING_APPROVAL
                if row["status"] == "rejected":
                    outcome = Outcome.REJECTED
                elif row["status"] in ("registering", "awaiting_join", "online"):
                    outcome = Outcome.ALREADY_REGISTERED
                return Decision(outcome, dev_eui, row["device_id"])
            if conn.execute(
                "SELECT 1 FROM devices WHERE dev_eui = ? COLLATE NOCASE LIMIT 1", (dev_eui,)
            ).fetchone():
                conn.commit()
                return Decision(Outcome.ALREADY_REGISTERED, dev_eui)
            if len(_window.discovered) >= int(settings.AUTO_ENROLLMENT_MAX_PER_WINDOW):
                conn.commit()
                return Decision(Outcome.QUOTA_EXCEEDED, dev_eui)
            mac = recover_mac_from_dev_eui(dev_eui, namespace=settings.TTN_APP_ID)
            device_id = f"node-{mac}" if mac else device_id_from_dev_eui(dev_eui)
            conn.execute(
                """INSERT INTO device_enrollment_candidates
                (application_id, dev_eui, join_eui, device_id, verified_key_hash, gateway_id, rssi, snr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (settings.TTN_APP_ID, dev_eui, request.join_eui, device_id,
                 hashlib.sha256(key).hexdigest(), gateway_id, rssi, snr),
            )
            conn.commit()
            _window.discovered.append(dev_eui)
        except Exception:
            conn.rollback()
            raise
    return Decision(Outcome.PENDING_APPROVAL, dev_eui, device_id)


def _lookup(conn: sqlite3.Connection, dev_eui: str):
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", dev_eui):
        raise HTTPException(status_code=400, detail="DevEUI must be 16 hexadecimal characters")
    row = _candidate(conn, dev_eui.upper())
    if row is None:
        raise HTTPException(status_code=404, detail="Enrollment candidate not found")
    return row


def _audit(conn: sqlite3.Connection, action: str, row, actor: str) -> None:
    from app.main import log_audit_event

    log_audit_event(
        conn, action=action, target_type="device", target_id=row["device_id"],
        actor=actor, details={"dev_eui": row["dev_eui"], "join_eui": row["join_eui"]},
    )


def accept_candidate(conn: sqlite3.Connection, dev_eui: str, actor: str) -> dict:
    """Claim registration atomically, then perform network I/O without a DB lock."""
    if settings.PREVIEW_MODE:
        raise HTTPException(409, "Device enrollment is unavailable in preview mode")
    if not settings.AUTO_ENROLLMENT_ENABLED:
        raise HTTPException(status_code=409, detail="Enrollment is disabled")
    row = _lookup(conn, dev_eui)
    if row["management_action"]:
        raise HTTPException(409, "Finish revocation before accepting this device again")
    if row["status"] in ("awaiting_join", "online"):
        return _public_candidate(row)
    try:
        key = app_key_bytes(settings.LORAWAN_APP_KEY)
        expected = normalize_hex(settings.JOIN_EUI, 16, "JOIN_EUI")
    except ValueError:
        raise HTTPException(status_code=409, detail="Enrollment credentials are not configured correctly")
    if row["join_eui"] != expected or row["verified_key_hash"] != hashlib.sha256(key).hexdigest():
        raise HTTPException(status_code=409, detail="Credentials changed. Scan for a fresh verified join request before accepting.")
    token = uuid.uuid4().hex
    now = time.time()
    try:
        changed = conn.execute(
            """UPDATE device_enrollment_candidates SET status = 'registering',
                decided_by = ?, decided_at = CURRENT_TIMESTAMP, registration_error = NULL,
                registration_token = ?, registration_started_at = ?, online_at=NULL,
                ttn_checked_at=NULL,ttn_check_json=NULL
            WHERE id = ? AND management_action IS NULL AND (status IN ('pending', 'registration_failed')
                OR (status = 'registering' AND registration_started_at < ?))
                AND NOT EXISTS (SELECT 1 FROM device_admission_blocks b WHERE b.application_id=?
                    AND (b.device_id=? OR b.dev_eui=? COLLATE NOCASE) AND b.state IN ('deleting','failed'))""",
            (actor, token, now, row["id"], now - REGISTRATION_LEASE_SECONDS, settings.TTN_APP_ID, row["device_id"], row["dev_eui"]),
        ).rowcount
        if not changed:
            conn.rollback()
            fresh = _candidate(conn, row["dev_eui"])
            if fresh["status"] in ("awaiting_join", "online"):
                return _public_candidate(fresh)
            raise HTTPException(status_code=409, detail="Device was rejected, deletion needs completion, or registration is already in progress")
        _audit(conn, "device_enrollment_accepted", row, actor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    from app.main import register_device_in_ttn

    error = None
    try:
        register_device_in_ttn(
            device_id=row["device_id"], dev_eui=row["dev_eui"], join_eui=row["join_eui"],
            app_key=key.hex().upper(), profile=_enrollment_profile(conn),
        )
    except Exception as exc:
        # Upstream response bodies can include credentials; return a safe message.
        error = "TTN registration failed. Check the server logs and connection, then retry."
        if getattr(exc, "status_code", None) == 409:
            error = "Device identity already exists in TTN. Check all TTN components before retrying; a conflict does not confirm registration."
        logger.warning("Enrollment registration failed for %s (%s)", row["dev_eui"], type(exc).__name__)
    conn.execute(
        """UPDATE device_enrollment_candidates SET status = ?, registration_error = ?
        WHERE id = ? AND status = 'registering' AND registration_token = ?""",
        ("registration_failed" if error else "awaiting_join", error, row["id"], token),
    )
    if not error:
        clear_admission_block(conn, row["device_id"], row["dev_eui"])
    conn.commit()
    return _public_candidate(_candidate(conn, row["dev_eui"]))


def reject_candidate(conn: sqlite3.Connection, dev_eui: str, actor: str) -> dict:
    row = _lookup(conn, dev_eui)
    if row["management_action"]:
        raise HTTPException(409, "Finish the current revocation first")
    if row["status"] == "registration_failed" or (
        row["status"] == "registering" and (row["registration_started_at"] or 0) < time.time() - REGISTRATION_LEASE_SECONDS
    ):
        return revoke_candidate(conn, dev_eui, actor)
    if row["status"] == "rejected":
        return _public_candidate(row)
    try:
        changed = conn.execute(
            """UPDATE device_enrollment_candidates SET status = 'rejected',
                decided_by = ?, decided_at = CURRENT_TIMESTAMP, registration_error = NULL
            WHERE id = ? AND (status IN ('pending', 'registration_failed')
                OR (status = 'registering' AND registration_started_at < ?))""",
            (actor, row["id"], time.time() - REGISTRATION_LEASE_SECONDS),
        ).rowcount
        if not changed:
            raise HTTPException(status_code=409, detail="Device is already approved or registration is in progress")
        _audit(conn, "device_enrollment_rejected", row, actor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _public_candidate(_candidate(conn, row["dev_eui"]))


def reconsider_candidate(conn: sqlite3.Connection, dev_eui: str, actor: str) -> dict:
    row = _lookup(conn, dev_eui)
    changed = conn.execute("""UPDATE device_enrollment_candidates SET status='pending',
        decided_by=?,decided_at=CURRENT_TIMESTAMP,registration_error=NULL,ttn_check_json=NULL,ttn_checked_at=NULL
        WHERE id=? AND status='rejected' AND management_action IS NULL""", (actor, row["id"])).rowcount
    if not changed:
        conn.rollback()
        raise HTTPException(409, "Only rejected devices can be returned to review")
    conn.commit()
    _audit(conn, "device_enrollment_reconsidered", row, actor)
    return _public_candidate(_candidate(conn, row["dev_eui"]))


def revoke_candidate(conn: sqlite3.Connection, dev_eui: str, actor: str) -> dict:
    if settings.PREVIEW_MODE:
        raise HTTPException(409, "Revocation is unavailable in preview mode")
    row = _lookup(conn, dev_eui)
    token = uuid.uuid4().hex
    changed = conn.execute("""UPDATE device_enrollment_candidates SET management_action='revoking',
        management_started_at=?,management_token=?,registration_error=NULL
        WHERE id=? AND (status IN ('awaiting_join','registration_failed')
            OR (status='registering' AND registration_started_at < ?))
        AND (management_action IS NULL OR management_action='revocation_failed' OR management_started_at < ?)""",
        (time.time(), token, row["id"], time.time() - REGISTRATION_LEASE_SECONDS, time.time() - REGISTRATION_LEASE_SECONDS)).rowcount
    if not changed:
        conn.rollback()
        raise HTTPException(409, "Only devices awaiting their first uplink or with failed registration can be revoked")
    conn.commit()
    try:
        remove_device(conn, row["device_id"], row["dev_eui"], actor)
    except Exception:
        conn.execute("""UPDATE device_enrollment_candidates SET management_action='revocation_failed',
            registration_error='TTN revocation did not finish. Retry revocation; admission remains blocked.'
            WHERE id=? AND management_token=?""", (row["id"], token))
        conn.commit()
        raise
    _audit(conn, "device_enrollment_revoked", row, actor)
    return _public_candidate(_candidate(conn, row["dev_eui"]))


def inspect_candidate(conn: sqlite3.Connection, dev_eui: str, actor: str) -> dict:
    from app.services.ttn import ttn_client

    row = _lookup(conn, dev_eui)
    if row["management_action"] or row["status"] == "registering":
        raise HTTPException(409, "Wait for the current network operation to finish")
    local = conn.execute("SELECT ttn_device_id FROM devices WHERE dev_eui=? COLLATE NOCASE", (row["dev_eui"],)).fetchone()
    remote_id = (local["ttn_device_id"] if local else None) or row["device_id"]
    try:
        check = ttn_client.inspect_device_registration(remote_id, row["dev_eui"])
    except Exception as exc:
        check = {"error": "Could not read TTN registration. Check connectivity, application access and device identity."}
        conn.execute("""UPDATE device_enrollment_candidates SET ttn_checked_at=CURRENT_TIMESTAMP,ttn_check_json=?
            WHERE id=?""", (json.dumps(check), row["id"]))
        conn.commit()
        raise HTTPException(502, check["error"]) from exc
    conn.execute("""UPDATE device_enrollment_candidates SET ttn_checked_at=CURRENT_TIMESTAMP,ttn_check_json=?
        WHERE id=?""", (json.dumps(check), row["id"]))
    conn.commit()
    _audit(conn, "device_enrollment_checked", row, actor)
    return _public_candidate(_candidate(conn, row["dev_eui"]))


def mark_online(conn: sqlite3.Connection, device_id: str, dev_eui: str = "") -> None:
    """Call only for an authenticated TTN uplink after resolving its device."""
    conn.execute(
        """UPDATE device_enrollment_candidates SET status = 'online',
            online_at = COALESCE(online_at, CURRENT_TIMESTAMP), registration_error = NULL
        WHERE application_id = ? AND status IN ('registering', 'awaiting_join') AND management_action IS NULL
          AND ((? != '' AND dev_eui = ? COLLATE NOCASE) OR (? = '' AND device_id = ?))""",
        (settings.TTN_APP_ID, dev_eui, dev_eui, dev_eui, device_id),
    )
    conn.commit()


def uplink_is_blocked(conn: sqlite3.Connection, device_id: str, dev_eui: str = "") -> bool:
    """Do not let an externally registered identity bypass a portal decision."""
    if conn.execute("""SELECT 1 FROM device_admission_blocks WHERE application_id=?
        AND (device_id=? OR (? != '' AND dev_eui=? COLLATE NOCASE)) LIMIT 1""",
        (settings.TTN_APP_ID, device_id, dev_eui, dev_eui)).fetchone():
        return True
    return conn.execute(
        """SELECT 1 FROM device_enrollment_candidates
        WHERE application_id = ? AND (status IN ('pending', 'rejected', 'registration_failed') OR management_action IS NOT NULL)
          AND ((? != '' AND dev_eui = ? COLLATE NOCASE) OR (? = '' AND device_id = ?))
        LIMIT 1""",
        (settings.TTN_APP_ID, dev_eui, dev_eui, dev_eui, device_id),
    ).fetchone() is not None


def _enrollment_profile(conn: sqlite3.Connection) -> dict:
    from app.main import get_sensor_profile_detail

    row = conn.execute(
        """SELECT id FROM sensor_profiles WHERE profile_code = 'CAYENNE_LPP_V1'
        AND enabled = 1 AND LOWER(status) = 'active'"""
    ).fetchone()
    if not row:
        row = conn.execute(
            """SELECT id FROM sensor_profiles WHERE enabled = 1 AND LOWER(status) = 'active'
            ORDER BY id LIMIT 1"""
        ).fetchone()
    if not row:
        raise RuntimeError("no active sensor profile is available for enrollment")
    return get_sensor_profile_detail(conn, row["id"])
