"""Decode portal input to bytes before enqueueing an application downlink."""

from __future__ import annotations

import base64
import binascii
import re

from fastapi import HTTPException

# Keep observer messages small enough for the EU868 node at low data rates.
# TTN still validates the actual radio/session payload limit at scheduling time.
MAX_DOWNLINK_BYTES = 51

# SBMS configuration protocol v1, command 1: uint32 big-endian seconds.
CONFIGURATION_F_PORT = 42


def encode_send_rate(seconds: int) -> bytes:
    if type(seconds) is not int or not 30 <= seconds <= 86400:
        raise ValueError("Send interval must be an integer from 30 to 86400 seconds")
    return bytes((1, 1)) + seconds.to_bytes(4, "big")


def decode_downlink_payload(payload: str, encoding: str) -> bytes:
    try:
        if encoding == "text":
            raw = payload.encode("utf-8")
        elif encoding == "hex":
            compact = "".join(payload.split())
            if not re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", compact):
                raise ValueError("invalid hex")
            raw = bytes.fromhex(compact)
        elif encoding == "base64":
            raw = base64.b64decode("".join(payload.split()), validate=True)
        else:
            raise ValueError("unsupported encoding")
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=400, detail="Payload does not match the selected encoding")
    if not 1 <= len(raw) <= MAX_DOWNLINK_BYTES:
        raise HTTPException(status_code=400, detail=f"Payload must contain 1 to {MAX_DOWNLINK_BYTES} bytes")
    return raw
