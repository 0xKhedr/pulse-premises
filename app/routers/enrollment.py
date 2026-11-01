"""
Administrator control over over-the-air device enrollment.

An administrator opens a bounded discovery window, then accepts or rejects
individual verified identities. Discovery never grants network admission.
"""

from __future__ import annotations

import logging

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
    return current_user


@router.get("/admin/enrollment/status")
def enrollment_status(request: Request):
    """Discovery window and durable approval queue, including closed windows."""
    _require_admin(request)
    return _status()


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
