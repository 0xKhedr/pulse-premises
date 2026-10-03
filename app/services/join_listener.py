"""
Listens to gateway traffic for join requests while an enrollment window is open.

The listener's lifetime is deliberately tied to the window: opening a window
starts the stream, the window expiring stops it. With enrollment disabled, or
between windows, the app holds no event subscription and makes no outbound
connection at all.

Everything this thread decides is delegated to join_enrollment.evaluate_join_request;
the code here only turns a Things Stack event stream into parsed join requests.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time

import requests

from app.config import settings
from app.services import join_enrollment
from app.services.lorawan_join import parse_join_request

logger = logging.getLogger(__name__)

# The Gateway Server reports an uplink as received before the Network Server
# decides whether it knows the device, which is why enrollment has to read at
# gateway level -- an unknown DevEUI never reaches application scope.
#
# The event is gs.up.receive on current Things Stack builds, but the name has
# moved between versions and self-hosted deployments vary. Rather than depend
# on it, any event carrying a raw_payload that parses as a join request is
# handled; names are only used to skip obviously irrelevant traffic cheaply.
IGNORED_EVENT_PREFIXES = ("gs.status.", "gs.gateway.connection.", "events.stream.")

_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()


def is_running() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def start() -> bool:
    """Start the listener if enrollment is enabled and it is not already up."""
    if not settings.AUTO_ENROLLMENT_ENABLED:
        logger.info("Enrollment listener not started: AUTO_ENROLLMENT_ENABLED is false")
        return False
    with _lock:
        global _thread
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(
            target=_run, name="join-enrollment-listener", daemon=True
        )
        _thread.start()
        logger.info("Enrollment listener started")
        return True


def stop() -> None:
    _stop.set()


def _gateway_identifiers() -> list[dict]:
    """Gateways to watch: the configured list, else every gateway on record."""
    configured = [
        gateway_id.strip()
        for gateway_id in str(settings.AUTO_ENROLLMENT_GATEWAY_IDS or "").split(",")
        if gateway_id.strip()
    ]
    if not configured:
        from app.db.connection import get_db_connection

        conn = get_db_connection()
        try:
            configured = [
                row["gateway_id"]
                for row in conn.execute(
                    "SELECT gateway_id FROM gateways WHERE gateway_id IS NOT NULL"
                )
            ]
        finally:
            conn.close()
    return [{"gateway_ids": {"gateway_id": gateway_id}} for gateway_id in configured]


def _run() -> None:
    """Stream events until the window closes or stop() is called."""
    from app.db.connection import get_db_connection

    base = settings.TTN_BASE_URL.rstrip("/")
    # Gateway traffic is a separate right from application access; see
    # AUTO_ENROLLMENT_GATEWAY_API_KEY in app/config.py.
    api_key = settings.AUTO_ENROLLMENT_GATEWAY_API_KEY or settings.TTN_API_KEY
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }

    identifiers = _gateway_identifiers()
    if not identifiers:
        logger.warning("Enrollment listener stopping: no gateways to watch")
        return

    # The connection is owned by this thread. SQLite objects cannot cross
    # threads, so it must be opened here rather than handed in.
    conn = get_db_connection()
    try:
        while not _stop.is_set():
            if not join_enrollment.window_status()["open"]:
                logger.info("Enrollment listener stopping: window closed")
                return
            try:
                _consume(base, headers, identifiers, conn)
            except Exception as exc:
                logger.info(
                    "Enrollment event stream dropped (%s); reconnecting",
                    type(exc).__name__,
                )
                _stop.wait(3)
    finally:
        conn.close()
        logger.info("Enrollment listener stopped")


def _consume(base: str, headers: dict, identifiers: list[dict], conn) -> None:
    body = {"identifiers": identifiers, "tail": 0}
    saw_traffic = False
    # No read timeout: an event stream is legitimately idle between uplinks.
    with requests.post(
        f"{base}/api/v3/events", headers=headers, json=body,
        stream=True, timeout=(10, None),
    ) as response:
        if response.status_code != 200:
            logger.warning(
                "Enrollment event stream refused: HTTP %s", response.status_code
            )
            _stop.wait(10)
            return
        for line in response.iter_lines(decode_unicode=True):
            if _stop.is_set():
                return
            # Re-check each event rather than on a timer, so the listener shuts
            # down promptly when the window lapses.
            if not join_enrollment.window_status()["open"]:
                return
            if not line:
                continue
            if _handle_line(line, conn):
                saw_traffic = True

    if not saw_traffic:
        # An application-scoped key connects successfully and then receives
        # only status events, so a silent stream is the expected symptom of
        # the wrong key rather than of a quiet radio.
        logger.warning(
            "Enrollment listener saw no gateway traffic events. If devices are "
            "transmitting, AUTO_ENROLLMENT_GATEWAY_API_KEY probably lacks "
            "RIGHT_GATEWAY_READ_TRAFFIC."
        )


def _handle_line(line: str, conn) -> bool:
    """Returns True when the event carried a radio payload, join or not."""
    payload = line[5:].strip() if line.startswith("data:") else line
    try:
        event = json.loads(payload)
    except (ValueError, TypeError):
        return False
    result = event.get("result", event)
    name = str(result.get("name") or "")
    if name.startswith(IGNORED_EVENT_PREFIXES):
        return False
    data = result.get("data") or {}
    # gs.up.receive wraps a GatewayUplinkMessage, so the frame is at
    # data.message.raw_payload. Other event types put it at the top level;
    # both are accepted rather than depending on one shape.
    message = data.get("message")
    raw_payload = None
    if isinstance(message, dict):
        raw_payload = message.get("raw_payload")
    if not raw_payload:
        raw_payload = data.get("raw_payload")
    if not raw_payload:
        return False
    try:
        frame = base64.b64decode(raw_payload)
    except Exception:
        return False

    request = parse_join_request(frame)
    if request is None:
        return True  # a data uplink: traffic is flowing, just not a join

    decision = join_enrollment.evaluate_join_request(conn, request)
    if decision.enrolled:
        logger.info(
            "Enrollment: DevEUI %s registered as %s; its next OTAA attempt "
            "should now succeed",
            decision.dev_eui,
            decision.detail,
        )
    elif decision.outcome is not join_enrollment.Outcome.ALREADY_REGISTERED:
        logger.info(
            "Enrollment: DevEUI %s not enrolled (%s: %s)",
            decision.dev_eui,
            decision.outcome.value,
            decision.detail,
        )
    return True


def wait_until_stopped(timeout: float = 5.0) -> None:
    """Test helper: block until the listener thread exits."""
    deadline = time.time() + timeout
    while is_running() and time.time() < deadline:
        time.sleep(0.05)
