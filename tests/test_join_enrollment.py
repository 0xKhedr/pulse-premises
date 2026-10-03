"""
Tests for over-the-air enrollment decisions.

Enrollment writes to the LoRaWAN network registry on the strength of a radio
frame, so the tests are written from the attacker's side as much as the
operator's: the case that matters most is a structurally perfect join request
from a device that does not hold the shared key.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.services import join_enrollment
from app.services.join_enrollment import Outcome, evaluate_join_request
from app.services.lorawan_join import compute_join_request_mic, parse_join_request

APP_KEY_HEX = "0F1E2D3C4B5A69788796A5B4C3D2E1F0"
APP_KEY = bytes.fromhex(APP_KEY_HEX)
ATTACKER_KEY = bytes.fromhex("FFEEDDCCBBAA99887766554433221100")

JOIN_EUI = "0000000000000000"
# Derived from MAC aa11bb22cc33 under the 'sbms-test' namespace, so the
# node-<mac> naming path is exercised rather than the eui-<...> fallback.
KNOWN_MAC = "aa11bb22cc33"


def make_request(dev_eui: str, key: bytes = APP_KEY, *, dev_nonce: int = 7):
    body = (
        b"\x00"
        + bytes.fromhex(JOIN_EUI)[::-1]
        + bytes.fromhex(dev_eui)[::-1]
        + dev_nonce.to_bytes(2, "little")
    )
    return parse_join_request(body + compute_join_request_mic(body, key))


@pytest.fixture
def enrollment_env(monkeypatch):
    """Enrollment enabled, a fresh closed window, and a stubbed TTN registrar."""
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_MAX_PER_WINDOW", 3)
    monkeypatch.setattr(settings, "LORAWAN_APP_KEY", APP_KEY_HEX)
    monkeypatch.setattr(settings, "JOIN_EUI", JOIN_EUI)
    join_enrollment.close_window("test-setup")

    registered: list[dict] = []

    def fake_register(*, device_id, dev_eui, join_eui, app_key, profile):
        registered.append({"device_id": device_id, "dev_eui": dev_eui})
        return {"status": "registered"}

    # Patch where the names are looked up, not where they are defined: the
    # registrar is imported lazily inside _register.
    monkeypatch.setattr("app.main.register_device_in_ttn", fake_register)
    monkeypatch.setattr(
        "app.services.join_enrollment._enrollment_profile", lambda conn: {"id": 1}
    )
    yield registered
    join_enrollment.close_window("test-teardown")


# ---------------------------------------------------------------------------
# The security boundary
# ---------------------------------------------------------------------------


def test_a_device_holding_the_shared_key_is_enrolled(
    db_conn: sqlite3.Connection, enrollment_env
):
    join_enrollment.open_window("tester")
    dev_eui = _eui_for(KNOWN_MAC)

    decision = evaluate_join_request(db_conn, make_request(dev_eui))

    assert decision.outcome is Outcome.REGISTERED
    assert decision.enrolled is True
    assert enrollment_env == [{"device_id": f"node-{KNOWN_MAC}", "dev_eui": dev_eui}]


def test_a_device_without_the_shared_key_is_refused(
    db_conn: sqlite3.Connection, enrollment_env
):
    """
    The whole point of the design. This frame is well-formed, carries our
    JoinEUI, arrives inside an open window and is for an unknown DevEUI -- it
    fails on the MIC alone.
    """
    join_enrollment.open_window("tester")
    dev_eui = _eui_for("dead00beef11")

    decision = evaluate_join_request(db_conn, make_request(dev_eui, ATTACKER_KEY))

    assert decision.outcome is Outcome.BAD_MIC
    assert enrollment_env == [], "nothing may reach TTN"
    assert dev_eui in join_enrollment.window_status()["rejected"]


def test_a_replayed_frame_with_a_swapped_dev_eui_is_refused(
    db_conn: sqlite3.Connection, enrollment_env
):
    """Capturing a valid join and substituting a DevEUI must not enroll it."""
    join_enrollment.open_window("tester")
    genuine = make_request(_eui_for(KNOWN_MAC))

    forged = bytearray(genuine.raw)
    forged[9] ^= 0xFF  # a different DevEUI, original MIC retained
    decision = evaluate_join_request(db_conn, parse_join_request(bytes(forged)))

    assert decision.outcome is Outcome.BAD_MIC
    assert enrollment_env == []


# ---------------------------------------------------------------------------
# Window and quota
# ---------------------------------------------------------------------------


def test_nothing_enrolls_while_the_window_is_closed(
    db_conn: sqlite3.Connection, enrollment_env
):
    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))
    assert decision.outcome is Outcome.WINDOW_CLOSED
    assert enrollment_env == []


def test_nothing_enrolls_when_the_feature_is_disabled(
    db_conn: sqlite3.Connection, enrollment_env, monkeypatch
):
    """An open window must not survive the master switch being turned off."""
    join_enrollment.open_window("tester")
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_ENABLED", False)

    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))
    assert decision.outcome is Outcome.WINDOW_CLOSED
    assert enrollment_env == []


def test_an_expired_window_stops_enrolling(
    db_conn: sqlite3.Connection, enrollment_env
):
    join_enrollment.open_window("tester", seconds=1)
    join_enrollment._window.expires_at = 0.0  # expire it without sleeping

    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))
    assert decision.outcome is Outcome.WINDOW_CLOSED


def test_the_quota_bounds_one_window(db_conn: sqlite3.Connection, enrollment_env):
    """A single open window cannot be used to flood the TTN registry."""
    join_enrollment.open_window("tester")
    macs = ["aa0000000001", "aa0000000002", "aa0000000003", "aa0000000004"]

    outcomes = [
        evaluate_join_request(db_conn, make_request(_eui_for(mac))).outcome
        for mac in macs
    ]

    assert outcomes[:3] == [Outcome.REGISTERED] * 3
    assert outcomes[3] is Outcome.QUOTA_EXCEEDED
    assert len(enrollment_env) == 3


# ---------------------------------------------------------------------------
# Everything else
# ---------------------------------------------------------------------------


def test_another_networks_join_eui_is_ignored(
    db_conn: sqlite3.Connection, enrollment_env, monkeypatch
):
    monkeypatch.setattr(settings, "JOIN_EUI", "70B3D57ED0000001")
    join_enrollment.open_window("tester")

    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))
    assert decision.outcome is Outcome.JOIN_EUI_MISMATCH
    assert enrollment_env == []


def test_a_device_already_on_record_is_left_alone(
    db_conn: sqlite3.Connection, enrollment_env
):
    dev_eui = _eui_for("bb2233445566")
    db_conn.execute(
        "INSERT OR REPLACE INTO devices (chip_mac, device_id, dev_eui, node_type)"
        " VALUES ('BB:22:33:44:55:66', 'node-bb2233445566', ?, 'multi')",
        (dev_eui,),
    )
    db_conn.commit()
    join_enrollment.open_window("tester")

    decision = evaluate_join_request(db_conn, make_request(dev_eui))
    assert decision.outcome is Outcome.ALREADY_REGISTERED
    assert enrollment_env == [], "a known device must not be re-registered"


def test_a_broken_app_key_fails_closed(
    db_conn: sqlite3.Connection, enrollment_env, monkeypatch
):
    join_enrollment.open_window("tester")
    monkeypatch.setattr(settings, "LORAWAN_APP_KEY", "not-a-key")

    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))
    assert decision.outcome is Outcome.MISCONFIGURED
    assert enrollment_env == []


def test_a_failing_registrar_is_reported_not_raised(
    db_conn: sqlite3.Connection, enrollment_env, monkeypatch
):
    """TTN being unreachable must not take down the listener thread."""
    def boom(**kwargs):
        raise ConnectionError("TTN unreachable")

    monkeypatch.setattr("app.main.register_device_in_ttn", boom)
    join_enrollment.open_window("tester")

    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))
    assert decision.outcome is Outcome.REGISTRATION_FAILED
    assert "unreachable" in decision.detail
    # A failed attempt must not consume window quota.
    assert join_enrollment.window_status()["quota_used"] == 0


def test_window_status_reports_what_the_operator_needs(enrollment_env):
    assert join_enrollment.window_status()["open"] is False

    join_enrollment.open_window("darwish", seconds=120)
    status = join_enrollment.window_status()
    assert status["open"] is True
    assert status["opened_by"] == "darwish"
    assert 0 < status["seconds_remaining"] <= 120

    join_enrollment.close_window("darwish")
    assert join_enrollment.window_status()["open"] is False


def _eui_for(mac: str) -> str:
    from app.services.lorawan_identity import derive_dev_eui_from_mac

    return derive_dev_eui_from_mac(mac, namespace=settings.TTN_APP_ID)


# ---------------------------------------------------------------------------
# Registration atomicity
# ---------------------------------------------------------------------------


def test_a_device_already_in_ttn_is_treated_as_enrolled_not_failed(
    db_conn: sqlite3.Connection, enrollment_env, monkeypatch
):
    """
    A DevEUI present in TTN but missing from our devices table can already
    join; the webhook picks it up on the next uplink. Reporting that as a
    failure would make the listener retry forever against a 409.
    """
    from fastapi import HTTPException

    def already_there(**kwargs):
        raise HTTPException(status_code=409, detail={"stage": "device_identity"})

    monkeypatch.setattr("app.main.register_device_in_ttn", already_there)
    join_enrollment.open_window("tester")

    decision = evaluate_join_request(db_conn, make_request(_eui_for(KNOWN_MAC)))

    assert decision.outcome is Outcome.ALREADY_REGISTERED
    assert join_enrollment.window_status()["quota_used"] == 0


def test_a_failed_registration_rolls_back_the_partial_device():
    """
    TTN registration spans four components with no transaction across them.
    A failure after the identity exists used to leave a device that could
    neither join nor be re-registered, because the retry hit 409 id_taken.
    """
    from fastapi import HTTPException

    import app.main as main_module

    calls = []

    def fake_identity(device_id, dev_eui, join_eui):
        calls.append(("create", device_id))

    def fake_formatter(*, device_id, profile):
        raise HTTPException(status_code=400, detail="formatter exploded")

    class FakeClient:
        def delete_device(self, device_id):
            calls.append(("delete", device_id))

    original = (
        main_module.create_ttn_device_identity,
        main_module.set_ttn_join_server,
        main_module.set_ttn_network_server,
        main_module.set_ttn_application_server,
        main_module.set_device_formatter_from_profile,
        main_module.ttn_client,
    )
    try:
        main_module.create_ttn_device_identity = fake_identity
        main_module.set_ttn_join_server = lambda *a, **k: None
        main_module.set_ttn_network_server = lambda *a, **k: None
        main_module.set_ttn_application_server = lambda *a, **k: None
        main_module.set_device_formatter_from_profile = fake_formatter
        main_module.ttn_client = FakeClient()

        with pytest.raises(HTTPException):
            main_module.register_device_in_ttn(
                device_id="node-rollback",
                dev_eui="70B3D57ED0009999",
                join_eui="0000000000000000",
                app_key="0F1E2D3C4B5A69788796A5B4C3D2E1F0",
                profile={"profile_code": "X", "profile_version": 1},
            )
    finally:
        (
            main_module.create_ttn_device_identity,
            main_module.set_ttn_join_server,
            main_module.set_ttn_network_server,
            main_module.set_ttn_application_server,
            main_module.set_device_formatter_from_profile,
            main_module.ttn_client,
        ) = original

    assert calls == [("create", "node-rollback"), ("delete", "node-rollback")], (
        "the half-created device must be removed so a retry can succeed"
    )
