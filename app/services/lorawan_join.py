"""
Parse and cryptographically verify LoRaWAN OTAA join requests.

The auto-enrollment listener sees join requests from DevEUIs the network has
never met. Registering every one of them would let anyone within radio range
fill the device registry, so a DevEUI is only trusted once it has proven that
it already holds this deployment's shared AppKey: the join request carries a
MIC that is an AES-128-CMAC over its own body under that key, and only a device
holding the key can produce one that verifies.

Nothing here performs I/O, so the rules stay unit-testable and the listener can
stay a thin shell around them.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.cmac import CMAC

# PHYPayload for a join request (LoRaWAN 1.0.x, section 6.2.4):
#
#   MHDR(1) | JoinEUI(8, LE) | DevEUI(8, LE) | DevNonce(2, LE) | MIC(4)
#
# The three most significant bits of MHDR carry the message type; 0b000 is a
# join request. The remaining bits are RFU/major and are not ours to police.
JOIN_REQUEST_LENGTH = 23
MHDR_MTYPE_MASK = 0xE0
MHDR_MTYPE_JOIN_REQUEST = 0x00

_MIC_LENGTH = 4
_EUI_LENGTH = 8


@dataclass(frozen=True)
class JoinRequest:
    """A parsed join request. EUIs are MSB-first hex, as humans and TTN write them."""

    join_eui: str
    dev_eui: str
    dev_nonce: int
    mic: bytes
    raw: bytes

    @property
    def signed_body(self) -> bytes:
        """The bytes the MIC is computed over: everything except the MIC itself."""
        return self.raw[:-_MIC_LENGTH]


def parse_join_request(payload: bytes) -> JoinRequest | None:
    """
    Decode a PHYPayload as a join request, or return None if it is not one.

    Returns None rather than raising: this is fed from a live event stream
    carrying every uplink on the gateway, so non-join traffic and truncated
    frames are ordinary input, not errors.
    """
    if not isinstance(payload, (bytes, bytearray)):
        return None
    payload = bytes(payload)
    if len(payload) != JOIN_REQUEST_LENGTH:
        return None
    if (payload[0] & MHDR_MTYPE_MASK) != MHDR_MTYPE_JOIN_REQUEST:
        return None

    # Both EUIs travel little-endian on air and are reversed for display.
    join_eui = payload[1:9][::-1].hex().upper()
    dev_eui = payload[9:17][::-1].hex().upper()
    dev_nonce = int.from_bytes(payload[17:19], "little")
    return JoinRequest(
        join_eui=join_eui,
        dev_eui=dev_eui,
        dev_nonce=dev_nonce,
        mic=payload[19:23],
        raw=payload,
    )


def compute_join_request_mic(signed_body: bytes, app_key: bytes) -> bytes:
    """cmac = aes128_cmac(AppKey, MHDR | JoinEUI | DevEUI | DevNonce); MIC = cmac[0:4]."""
    if len(app_key) != 16:
        raise ValueError("AppKey must be exactly 16 bytes")
    mac = CMAC(algorithms.AES(app_key))
    mac.update(signed_body)
    return mac.finalize()[:_MIC_LENGTH]


def verify_join_request_mic(request: JoinRequest, app_key: bytes) -> bool:
    """
    True when this join request was signed with ``app_key``.

    This is the whole security boundary for auto-enrollment, so the comparison
    is constant-time and any malformed key is treated as a failed check rather
    than propagating an exception into the listener loop.
    """
    try:
        expected = compute_join_request_mic(request.signed_body, app_key)
    except Exception:
        return False
    return hmac.compare_digest(expected, request.mic)


def app_key_bytes(app_key_hex: str) -> bytes:
    """Decode the configured AppKey, rejecting anything that is not 16 bytes."""
    cleaned = "".join(str(app_key_hex or "").split()).replace("-", "").replace(":", "")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError("LORAWAN_APP_KEY must be valid hexadecimal") from exc
    if len(raw) != 16:
        raise ValueError("LORAWAN_APP_KEY must decode to exactly 16 bytes")
    return raw
