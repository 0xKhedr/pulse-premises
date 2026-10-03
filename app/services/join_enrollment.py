"""
Over-the-air enrollment for devices the LoRaWAN network has never met.

A node whose DevEUI is unknown to the Join Server cannot complete OTAA, so it
never produces an uplink, so the webhook never sees it and zero-touch discovery
never fires. Registration was previously a manual step outside the app.

This module closes that gap without weakening the network. It watches gateway
traffic for join requests and registers a DevEUI only when *all* of these hold:

  1. An administrator has explicitly opened an enrollment window.
  2. The request's JoinEUI matches this deployment's.
  3. The DevEUI is not already registered.
  4. The window's registration quota is not exhausted.
  5. The request's MIC verifies under the shared AppKey.

Check 5 is the security boundary. A join request's MIC is an AES-CMAC over its
own body keyed with the AppKey, so only a device already holding this
deployment's key can produce one that verifies. Without it, anyone within radio
range could fill the device registry with DevEUIs of their choosing; with it,
an attacker who does not have the key cannot enroll anything, and one who does
have the key could already join anyway.

The decision logic here performs no I/O beyond the database and is separated
from the event listener so it can be tested directly.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.services.lorawan_identity import (
    normalize_hex,
    recover_mac_from_dev_eui,
)
from app.services.lorawan_join import (
    JoinRequest,
    app_key_bytes,
    verify_join_request_mic,
)

logger = logging.getLogger(__name__)


class Outcome(str, Enum):
    """Why a join request was or was not enrolled. Recorded in the audit log."""

    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"
    WINDOW_CLOSED = "window_closed"
    QUOTA_EXCEEDED = "quota_exceeded"
    JOIN_EUI_MISMATCH = "join_eui_mismatch"
    BAD_MIC = "bad_mic"
    MISCONFIGURED = "misconfigured"
    REGISTRATION_FAILED = "registration_failed"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    dev_eui: str
    detail: str = ""

    @property
    def enrolled(self) -> bool:
        return self.outcome is Outcome.REGISTERED


@dataclass
class _Window:
    """State of the current enrollment window. Guarded by ``_lock``."""

    opened_by: str = ""
    opened_at: float = 0.0
    expires_at: float = 0.0
    registered: list[str] = field(default_factory=list)
    # DevEUIs that presented a bad MIC while the window was open. Surfaced to
    # the operator: a burst here means something nearby is transmitting with
    # the wrong key, which is worth seeing rather than silently dropping.
    rejected: list[str] = field(default_factory=list)


_lock = threading.Lock()
_window = _Window()


# ---------------------------------------------------------------------------
# Window control
# ---------------------------------------------------------------------------


def open_window(actor: str, *, seconds: int | None = None) -> dict:
    """Open an enrollment window. Re-opening replaces any window in progress."""
    duration = int(seconds or settings.AUTO_ENROLLMENT_WINDOW_SECONDS)
    duration = max(1, min(duration, 3600))
    now = time.time()
    with _lock:
        global _window
        _window = _Window(
            opened_by=str(actor or "unknown"),
            opened_at=now,
            expires_at=now + duration,
        )
        logger.info(
            "Enrollment window opened by %s for %s seconds", _window.opened_by, duration
        )
        return _status_locked(now)


def close_window(actor: str = "") -> dict:
    """Close the window immediately."""
    with _lock:
        global _window
        was_open = _window.expires_at > time.time()
        _window = _Window()
        if was_open:
            logger.info("Enrollment window closed by %s", actor or "unknown")
        return _status_locked(time.time())


def window_status() -> dict:
    with _lock:
        return _status_locked(time.time())


def _status_locked(now: float) -> dict:
    remaining = max(0, int(_window.expires_at - now))
    is_open = remaining > 0
    return {
        "enabled": bool(settings.AUTO_ENROLLMENT_ENABLED),
        "open": is_open,
        "opened_by": _window.opened_by if is_open else "",
        "seconds_remaining": remaining,
        "registered": list(_window.registered),
        "rejected": list(_window.rejected),
        "quota": int(settings.AUTO_ENROLLMENT_MAX_PER_WINDOW),
        "quota_used": len(_window.registered),
    }


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def evaluate_join_request(
    conn: sqlite3.Connection, request: JoinRequest
) -> Decision:
    """
    Decide whether this join request should be enrolled, and enroll it if so.

    Checks are ordered cheapest-first, but every rejection is equally silent on
    air: the device simply does not receive a JoinAccept, exactly as before.
    """
    dev_eui = request.dev_eui

    if not settings.AUTO_ENROLLMENT_ENABLED:
        return Decision(Outcome.WINDOW_CLOSED, dev_eui, "auto-enrollment is disabled")

    now = time.time()
    with _lock:
        if _window.expires_at <= now:
            return Decision(Outcome.WINDOW_CLOSED, dev_eui, "no window is open")
        if len(_window.registered) >= int(settings.AUTO_ENROLLMENT_MAX_PER_WINDOW):
            return Decision(
                Outcome.QUOTA_EXCEEDED,
                dev_eui,
                f"window quota of {settings.AUTO_ENROLLMENT_MAX_PER_WINDOW} reached",
            )

    # A join request for a different JoinEUI belongs to somebody else's network.
    try:
        expected_join_eui = normalize_hex(settings.JOIN_EUI, 16, "JOIN_EUI")
    except ValueError as exc:
        return Decision(Outcome.MISCONFIGURED, dev_eui, str(exc))
    if request.join_eui != expected_join_eui:
        return Decision(
            Outcome.JOIN_EUI_MISMATCH,
            dev_eui,
            f"JoinEUI {request.join_eui} is not {expected_join_eui}",
        )

    if _dev_eui_is_known(conn, dev_eui):
        return Decision(Outcome.ALREADY_REGISTERED, dev_eui)

    # --- the security boundary --------------------------------------------
    try:
        key = app_key_bytes(settings.LORAWAN_APP_KEY)
    except ValueError as exc:
        return Decision(Outcome.MISCONFIGURED, dev_eui, str(exc))

    if not verify_join_request_mic(request, key):
        with _lock:
            if dev_eui not in _window.rejected:
                _window.rejected.append(dev_eui)
        logger.warning(
            "Enrollment refused for DevEUI %s: join request MIC did not verify "
            "against the shared AppKey",
            dev_eui,
        )
        return Decision(Outcome.BAD_MIC, dev_eui, "MIC did not verify")

    return _register(conn, request)


def _dev_eui_is_known(conn: sqlite3.Connection, dev_eui: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM devices WHERE dev_eui = ? COLLATE NOCASE LIMIT 1",
        (dev_eui,),
    ).fetchone()
    return row is not None


def _register(conn: sqlite3.Connection, request: JoinRequest) -> Decision:
    """Register the proven device in TTN so its next join attempt succeeds."""
    # Imported lazily: app.main imports this module's siblings, and the TTN
    # helpers live there. Matches how provisioning.py reaches them.
    from app.main import log_audit_event, register_device_in_ttn
    from app.services.lorawan_identity import device_id_from_dev_eui

    dev_eui = request.dev_eui

    # Devices minted by our own firmware carry a recoverable MAC, so they keep
    # the node-<mac> name every other device uses. Anything else falls back to
    # an EUI-derived id.
    mac = recover_mac_from_dev_eui(dev_eui, namespace=settings.TTN_APP_ID)
    device_id = f"node-{mac}" if mac else device_id_from_dev_eui(dev_eui)

    try:
        profile = _enrollment_profile(conn)
        register_device_in_ttn(
            device_id=device_id,
            dev_eui=dev_eui,
            join_eui=request.join_eui,
            app_key=settings.LORAWAN_APP_KEY,
            profile=profile,
        )
    except Exception as exc:
        # A device already present in TTN but absent from our devices table is
        # not a failure: it can join perfectly well, and the webhook will pick
        # it up on the next uplink. Retrying registration would only 409 again.
        if getattr(exc, "status_code", None) == 409:
            logger.info(
                "DevEUI %s is already registered in TTN; leaving it alone",
                dev_eui,
            )
            return Decision(Outcome.ALREADY_REGISTERED, dev_eui, device_id)
        logger.error("Enrollment registration failed for %s: %s", dev_eui, exc)
        return Decision(Outcome.REGISTRATION_FAILED, dev_eui, str(exc))

    with _lock:
        if dev_eui not in _window.registered:
            _window.registered.append(dev_eui)
        opened_by = _window.opened_by

    try:
        log_audit_event(
            conn,
            action="device_auto_enrolled",
            target_type="device",
            target_id=device_id,
            actor=f"enrollment:{opened_by}",
            details={
                "dev_eui": dev_eui,
                "join_eui": request.join_eui,
                "dev_nonce": request.dev_nonce,
                "mic_verified": True,
            },
        )
    except Exception as exc:  # auditing must never block enrollment
        logger.info("Enrollment audit write failed for %s: %s", dev_eui, exc)

    logger.info(
        "Enrolled DevEUI %s as %s after a verified join request", dev_eui, device_id
    )
    return Decision(Outcome.REGISTERED, dev_eui, device_id)


def _enrollment_profile(conn: sqlite3.Connection) -> dict:
    """The sensor profile newly enrolled devices are registered against."""
    from app.main import get_sensor_profile_detail

    row = conn.execute(
        """
        SELECT id FROM sensor_profiles
        WHERE profile_code = 'CAYENNE_LPP_V1' AND enabled = 1
          AND LOWER(status) = 'active'
        """
    ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT id FROM sensor_profiles
            WHERE enabled = 1 AND LOWER(status) = 'active'
            ORDER BY id LIMIT 1
            """
        ).fetchone()
    if not row:
        raise RuntimeError("no active sensor profile is available for enrollment")
    return get_sensor_profile_detail(conn, row["id"])
