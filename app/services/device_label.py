"""
Human nicknames carried over LoRa.

Fifty freshly flashed boards are indistinguishable in the discovery inbox:
their identity is a DevEUI derived from the chip MAC, which tells an installer
nothing about which box on which floor they just mounted. A nickname closes
that gap without touching identity -- the device is still addressed by its
DevEUI everywhere, and the nickname only ever populates ``devices.label``.

Cayenne LPP has no string type, so the nickname cannot travel inside the sensor
payload: the built-in decoder rejects a whole payload it cannot parse. It
arrives instead as its own uplink on a dedicated port, which the Cayenne
decoder fails on -- harmlessly, because the webhook still fires and carries the
raw ``frm_payload``.

Everything here is pure, so the rules can be tested without a radio.
"""

from __future__ import annotations

import base64
import binascii
import re

# Port reserved for nickname uplinks. Sensor data stays on port 1.
LABEL_F_PORT = 10

MAX_LABEL_LENGTH = 48

# Deliberately narrow. A nickname is rendered straight into the admin UI and
# exported to CSV, so it is restricted to characters that cannot start a
# formula, close a tag, or smuggle control codes through a log line.
_ALLOWED = re.compile(r"^[A-Za-z0-9 _.\-/#]+$")


def parse_label_uplink(frm_payload: str | bytes | None) -> str | None:
    """
    Decode a nickname from an uplink's raw payload.

    Returns None for anything that is not a usable nickname; the caller treats
    that as "no label in this uplink" rather than as an error, because this is
    fed from radio traffic that may be truncated or corrupt.
    """
    if frm_payload is None:
        return None
    if isinstance(frm_payload, str):
        try:
            raw = base64.b64decode(frm_payload, validate=True)
        except (binascii.Error, ValueError):
            return None
    else:
        raw = bytes(frm_payload)

    if not raw or len(raw) > MAX_LABEL_LENGTH:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return sanitize_label(text)


def sanitize_label(text: str) -> str | None:
    """Normalize whitespace and reject anything outside the allowed set."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned or len(cleaned) > MAX_LABEL_LENGTH:
        return None
    if not _ALLOWED.match(cleaned):
        return None
    return cleaned


def is_label_port(f_port: object) -> bool:
    try:
        return int(f_port) == LABEL_F_PORT
    except (TypeError, ValueError):
        return False
