"""
Administrator control over over-the-air device enrollment.

Enrollment lets the app register a never-before-seen DevEUI in TTN, so it is
deliberately not a background behaviour that is simply on. An administrator
opens a bounded window, the window closes on its own, and a restart closes it
too. See app/services/join_enrollment.py for the rules applied to each request.
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
    """Current window state, plus what it has admitted and refused so far."""
    _require_admin(request)
    status = join_enrollment.window_status()
    status["listener_running"] = join_listener.is_running()
    return status


@router.post("/admin/enrollment/open")
def open_enrollment(request: Request, data: dict | None = None):
    """
    Open an enrollment window and start watching gateway traffic.

    Only devices that prove possession of the shared AppKey are registered, so
    an open window is not itself an authorisation to join -- but it is still
    bounded in time and count, and every enrollment is audited.
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

    return status


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

    return status
