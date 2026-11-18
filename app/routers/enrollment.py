"""
Administrator control over over-the-air device enrollment.

An administrator opens a bounded discovery window, then accepts or rejects
individual verified identities. Discovery never grants network admission.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.db.connection import get_db_connection as db
from app.main import get_current_user_from_request, log_audit_event
from app.services import join_enrollment, join_listener

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_admin(request: Request) -> dict:
    current_user = get_current_user_from_request(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if (current_user.get("role") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    origin = request.headers.get("origin")
    if request.method != "GET" and origin:
        parsed = urlsplit(origin)
        if (parsed.scheme, parsed.netloc) != (request.url.scheme, request.url.netloc):
            raise HTTPException(403, "Enrollment actions require the same browser origin")
    return current_user


@router.get("/admin/enrollment/status")
def enrollment_status(request: Request):
    """Discovery window and durable approval queue, including closed windows."""
    _require_admin(request)
    return _status()


def _candidate_action(request: Request, dev_eui: str, action):
    user = _require_admin(request)
    actor = user.get("email") or str(user.get("id") or "admin")
    conn = db()
    try:
        action(conn, dev_eui, actor)
    finally:
        conn.close()
    return _status()


@router.post("/admin/enrollment/candidates/{dev_eui}/reconsider")
def reconsider_enrollment(request: Request, dev_eui: str):
    return _candidate_action(request, dev_eui, join_enrollment.reconsider_candidate)


@router.post("/admin/enrollment/candidates/{dev_eui}/revoke")
def revoke_enrollment(request: Request, dev_eui: str):
    return _candidate_action(request, dev_eui, join_enrollment.revoke_candidate)


@router.post("/admin/enrollment/candidates/{dev_eui}/check")
def check_enrollment(request: Request, dev_eui: str):
    return _candidate_action(request, dev_eui, join_enrollment.inspect_candidate)


@router.post("/admin/enrollment/bulk")
def bulk_enrollment(request: Request, data: dict):
    user = _require_admin(request)
    action = data.get("action")
    handlers = {"accept": join_enrollment.accept_candidate, "reject": join_enrollment.reject_candidate}
    if action not in handlers:
        raise HTTPException(400, "Bulk action must be accept or reject")
    euis = data.get("dev_euis")
    if (not isinstance(euis, list) or not 1 <= len(euis) <= 50
            or any(not isinstance(eui, str) for eui in euis)):
        raise HTTPException(400, "Select between 1 and 50 DevEUIs")
    actor = user.get("email") or str(user.get("id") or "admin")
    results = []
    registration_failed = False
    conn = db()
    try:
        for eui in dict.fromkeys(euis):
            if registration_failed:
                results.append({"dev_eui": eui, "success": False, "detail": "Not processed: registration stopped after an earlier failure; this request remains pending."})
                continue
            try:
                row = join_enrollment._lookup(conn, eui)
                if row["status"] != "pending" or row["management_action"]:
                    raise HTTPException(409, "Bulk decisions apply only to pending devices; review this device individually")
                result = handlers[action](conn, eui, actor)
                results.append({"dev_eui": eui, "success": result["status"] != "registration_failed",
                                "status": result["status"], "detail": result["registration_error"]})
                registration_failed = action == "accept" and result["status"] == "registration_failed"
            except HTTPException as exc:
                conn.rollback()
                results.append({"dev_eui": eui, "success": False, "detail": str(exc.detail)})
    finally:
        conn.close()
    return {**_status(), "results": results}


def _status():
    status = join_enrollment.window_status()
    status["listener_running"] = join_listener.is_running()
    conn = db()
    try:
        status["candidates"] = join_enrollment.list_candidates(conn)
    finally:
        conn.close()
    return status


@router.post("/admin/enrollment/open")
def open_enrollment(request: Request, data: dict | None = None):
    """
    Open an enrollment window and start watching gateway traffic.

    Verified devices are queued for individual approval. Opening discovery
    does not register them or authorise a join.
    """
    current_user = _require_admin(request)

    if not settings.AUTO_ENROLLMENT_ENABLED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Over-the-air enrollment is disabled. Set "
                "AUTO_ENROLLMENT_ENABLED=true in .env and restart to use it."
            ),
        )

    requested = (data or {}).get("seconds")
    try:
        seconds = int(requested) if requested is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="seconds must be a whole number")

    actor = current_user.get("email") or str(current_user.get("id") or "admin")
    status = join_enrollment.open_window(actor, seconds=seconds)
    status["listener_running"] = join_listener.start()

    conn = db()
    try:
        log_audit_event(
            conn,
            action="enrollment_window_opened",
            target_type="system",
            target_id="enrollment",
            actor=actor,
            details={"seconds": status["seconds_remaining"]},
        )
    except Exception as exc:
        logger.info("Enrollment audit write failed: %s", exc)
    finally:
        conn.close()

    return _status()


@router.post("/admin/enrollment/close")
def close_enrollment(request: Request):
    """Close the window immediately and stop the listener."""
    current_user = _require_admin(request)
    actor = current_user.get("email") or str(current_user.get("id") or "admin")

    status = join_enrollment.close_window(actor)
    join_listener.stop()
    status["listener_running"] = join_listener.is_running()

    conn = db()
    try:
        log_audit_event(
            conn,
            action="enrollment_window_closed",
            target_type="system",
            target_id="enrollment",
            actor=actor,
            details={},
        )
    except Exception as exc:
        logger.info("Enrollment audit write failed: %s", exc)
    finally:
        conn.close()

    return _status()


@router.post("/admin/enrollment/candidates/{dev_eui}/accept")
def accept_enrollment(request: Request, dev_eui: str):
    current_user = _require_admin(request)
    actor = current_user.get("email") or str(current_user.get("id") or "admin")
    conn = db()
    try:
        join_enrollment.accept_candidate(conn, dev_eui, actor)
    finally:
        conn.close()
    return _status()


@router.post("/admin/enrollment/candidates/{dev_eui}/reject")
def reject_enrollment(request: Request, dev_eui: str):
    current_user = _require_admin(request)
    actor = current_user.get("email") or str(current_user.get("id") or "admin")
    conn = db()
    try:
        join_enrollment.reject_candidate(conn, dev_eui, actor)
    finally:
        conn.close()
    return _status()
