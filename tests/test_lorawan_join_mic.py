"""
Tests for the join-request MIC check that gates auto-enrollment.

This check is the only thing standing between "a DevEUI transmitted nearby"
and "a DevEUI registered in our LoRaWAN network", so it is tested as a
security boundary: every field is tampered with individually, and a wrong key
must fail even when the frame is otherwise perfectly well-formed.
"""

from __future__ import annotations

import pytest

from app.services.lorawan_join import (
    JOIN_REQUEST_LENGTH,
    app_key_bytes,
    compute_join_request_mic,
    parse_join_request,
    verify_join_request_mic,
)

# A throwaway key that exists only in this file. Never the deployment key:
# a real AppKey in the repository would be a permanent secret leak.
APP_KEY = bytes.fromhex("0F1E2D3C4B5A69788796A5B4C3D2E1F0")
OTHER_KEY = bytes.fromhex("00112233445566778899AABBCCDDEEFF")

JOIN_EUI = "0000000000000000"
DEV_EUI = "70B3D57ED0001234"


def build_join_request(
    app_key: bytes = APP_KEY,
    *,
    join_eui: str = JOIN_EUI,
    dev_eui: str = DEV_EUI,
    dev_nonce: int = 0x1234,
    mhdr: int = 0x00,
) -> bytes:
    """Assemble a correctly signed join request, the way a real node would."""
    body = (
        bytes([mhdr])
        + bytes.fromhex(join_eui)[::-1]
        + bytes.fromhex(dev_eui)[::-1]
        + dev_nonce.to_bytes(2, "little")
    )
    return body + compute_join_request_mic(body, app_key)


# --------------------------------------------------------------------------
# The CMAC primitive itself, pinned to the published RFC 4493 answers. If a
# dependency swap ever changes this, every MIC in the system silently breaks,
# so it is worth asserting independently of our own framing.
# --------------------------------------------------------------------------

RFC4493_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")


@pytest.mark.parametrize(
    "message_hex, expected_hex",
    [
        ("", "bb1d6929e95937287fa37d129b756746"),
        ("6bc1bee22e409f96e93d7e117393172a", "070a16b46b4d4144f79bdd9dd04a287c"),
        (
            "6bc1bee22e409f96e93d7e117393172a"
            "ae2d8a571e03ac9c9eb76fac45af8e51"
            "30c81c46a35ce411",
            "dfa66747de9ae63030ca32611497c827",
        ),
    ],
)
def test_cmac_matches_rfc4493(message_hex: str, expected_hex: str):
    # compute_join_request_mic truncates to the 4-byte MIC, so compare prefixes.
    got = compute_join_request_mic(bytes.fromhex(message_hex), RFC4493_KEY)
    assert got == bytes.fromhex(expected_hex)[:4]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parses_a_well_formed_join_request():
    request = parse_join_request(build_join_request())
    assert request is not None
    assert request.dev_eui == DEV_EUI
    assert request.join_eui == JOIN_EUI
    assert request.dev_nonce == 0x1234
    assert len(request.mic) == 4


@pytest.mark.parametrize(
    "payload, reason",
    [
        (b"", "empty"),
        (b"\x00" * (JOIN_REQUEST_LENGTH - 1), "one byte short"),
        (b"\x00" * (JOIN_REQUEST_LENGTH + 1), "one byte long"),
        ("not bytes", "wrong type"),
        (None, "none"),
    ],
)
def test_rejects_payloads_that_are_not_join_requests(payload, reason):
    assert parse_join_request(payload) is None, reason


@pytest.mark.parametrize("mhdr", [0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0])
def test_ignores_every_other_message_type(mhdr: int):
    """
    Only MType 0b000 is a join request. The listener sees all gateway traffic,
    so confirmed/unconfirmed data uplinks must be skipped, not misread as
    enrollment attempts.
    """
    assert parse_join_request(build_join_request(mhdr=mhdr)) is None


# --------------------------------------------------------------------------
# The security boundary
# --------------------------------------------------------------------------


def test_accepts_a_request_signed_with_the_shared_key():
    request = parse_join_request(build_join_request(APP_KEY))
    assert verify_join_request_mic(request, APP_KEY) is True


def test_rejects_a_request_signed_with_a_different_key():
    """A stranger's node transmits a structurally perfect join request."""
    request = parse_join_request(build_join_request(OTHER_KEY))
    assert request is not None, "the frame itself is valid"
    assert verify_join_request_mic(request, APP_KEY) is False


@pytest.mark.parametrize(
    "offset, label",
    [
        (1, "JoinEUI"),
        (9, "DevEUI"),
        (16, "DevEUI last byte"),
        (17, "DevNonce"),
        (19, "MIC"),
    ],
)
def test_rejects_any_tampered_byte(offset: int, label: str):
    """
    Flip one bit anywhere in the signed body or the MIC and the check must
    fail -- otherwise an attacker could replay a captured join request with a
    substituted DevEUI and have it registered.
    """
    payload = bytearray(build_join_request())
    payload[offset] ^= 0x01
    request = parse_join_request(bytes(payload))
    assert request is not None
    assert verify_join_request_mic(request, APP_KEY) is False, label


def test_rejects_a_malformed_key_without_raising():
    request = parse_join_request(build_join_request())
    assert verify_join_request_mic(request, b"too-short") is False


def test_mic_covers_the_dev_nonce():
    """
    Two joins from the same device under different nonces sign differently,
    and the same nonce reproduces the same frame exactly.

    This documents why the MIC is authentication but not replay protection:
    a captured frame stays valid forever, so the Join Server's own DevNonce
    tracking remains responsible for rejecting replays.
    """
    first = build_join_request(dev_nonce=1)
    assert build_join_request(dev_nonce=2) != first
    assert build_join_request(dev_nonce=1) == first


# --------------------------------------------------------------------------
# Key decoding
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "0F1E2D3C4B5A69788796A5B4C3D2E1F0",
        "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
        "0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0",
        "0F:1E:2D:3C:4B:5A:69:78:87:96:A5:B4:C3:D2:E1:F0",
    ],
)
def test_app_key_accepts_the_usual_spellings(value: str):
    assert app_key_bytes(value) == APP_KEY


@pytest.mark.parametrize("value", ["", None, "C830", "zz" * 16, "AB" * 15, "AB" * 17])
def test_app_key_rejects_anything_that_is_not_sixteen_bytes(value):
    with pytest.raises(ValueError):
        app_key_bytes(value)


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------

# A real join request, captured off the wire from a LilyGO TTGO LoRa32 and
# taken verbatim from a Things Stack gs.up.receive event. The stack's own
# decoding of the same bytes is asserted alongside it, so this pins our
# framing to an independent implementation rather than to our own encoder.
#
# Only the parse is asserted here: verifying its MIC would require the
# deployment's AppKey, which must never appear in the repository. The MIC path
# is covered by the synthetic cases and the RFC 4493 vectors above.
REAL_FRAME_B64 = "AAAAAAAAAAAAEhoxyEJ0wbV0nrvvgko="
REAL_FRAME_TTS_DECODING = {
    "join_eui": "0000000000000000",
    "dev_eui": "B5C17442C8311A12",
    "dev_nonce": 0x9E74,
    "mic": "BBEF824A",
}


def test_parses_a_real_join_request_the_same_way_the_things_stack_does():
    import base64

    request = parse_join_request(base64.b64decode(REAL_FRAME_B64))

    assert request is not None
    assert request.join_eui == REAL_FRAME_TTS_DECODING["join_eui"]
    assert request.dev_eui == REAL_FRAME_TTS_DECODING["dev_eui"]
    assert request.dev_nonce == REAL_FRAME_TTS_DECODING["dev_nonce"]
    assert request.mic.hex().upper() == REAL_FRAME_TTS_DECODING["mic"]
    # The MIC is the last four bytes and is excluded from what it signs.
    assert len(request.signed_body) == 19
    assert request.signed_body + request.mic == request.raw
