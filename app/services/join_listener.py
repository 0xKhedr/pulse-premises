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
import math
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
        global _thread, _stop
        if _thread is not None and _thread.is_alive() and not _stop.is_set():
            return True
        # A recently stopped stream may still be waiting for its next event.
        # Give the new scan its own stop signal rather than reviving that stream.
        _stop = threading.Event()
        _thread = threading.Thread(
            target=_run, args=(_stop,), name="join-enrollment-listener", daemon=True
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


def _run(stop_event: threading.Event) -> None:
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
        while not stop_event.is_set():
            if not join_enrollment.window_status()["open"]:
                logger.info("Enrollment listener stopping: window closed")
                return
            try:
                _consume(base, headers, identifiers, conn, stop_event)
            except Exception as exc:
                logger.info(
                    "Enrollment event stream dropped (%s); reconnecting",
                    type(exc).__name__,
                )
                stop_event.wait(3)
    finally:
        conn.close()
        logger.info("Enrollment listener stopped")


def _consume(base: str, headers: dict, identifiers: list[dict], conn, stop_event: threading.Event) -> None:
    body = {"identifiers": identifiers, "tail": 0}
    saw_traffic = False
    # Idle streams must eventually wake to observe window closure or stop().
    with requests.post(
        f"{base}/api/v3/events", headers=headers, json=body,
        stream=True, timeout=(10, 15),
    ) as response:
        if response.status_code != 200:
            logger.warning(
                "Enrollment event stream refused: HTTP %s", response.status_code
            )
            stop_event.wait(10)
            return
        for line in response.iter_lines(decode_unicode=True):
            if stop_event.is_set():
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

    metadata = (message.get("rx_metadata") if isinstance(message, dict) else None) or data.get("rx_metadata") or []
    if isinstance(metadata, dict):
        metadata = [metadata]
    radio = next((item for item in metadata if isinstance(item, dict)), {})
    gateway_id = (radio.get("gateway_ids") or {}).get("gateway_id")
    if not gateway_id:
        for identifier in result.get("identifiers") or []:
            gateway_id = (identifier.get("gateway_ids") or {}).get("gateway_id")
            if gateway_id:
                break

    def signal(name):
        try:
            value = float(radio[name])
            return value if math.isfinite(value) else None
        except (KeyError, TypeError, ValueError):
            return None

    decision = join_enrollment.evaluate_join_request(
        conn, request, gateway_id=gateway_id, rssi=signal("rssi"), snr=signal("snr")
    )
    if decision.outcome is join_enrollment.Outcome.PENDING_APPROVAL:
        logger.info(
            "Enrollment: DevEUI %s discovered as %s; awaiting portal approval",
            decision.dev_eui,
            decision.detail,
        )
    elif decision.outcome not in (join_enrollment.Outcome.ALREADY_REGISTERED, join_enrollment.Outcome.REJECTED):
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
