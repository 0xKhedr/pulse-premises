"""
Verified discovery, persistent portal decisions, and network admission.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time

import pytest
from fastapi import HTTPException

from app.config import settings
from app.db.connection import get_db_connection
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
def enrollment_env(monkeypatch, db_conn):
    """Enrollment enabled, a fresh closed window, and a stubbed TTN registrar."""
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_MAX_PER_WINDOW", 3)
    monkeypatch.setattr(settings, "LORAWAN_APP_KEY", APP_KEY_HEX)
    monkeypatch.setattr(settings, "JOIN_EUI", JOIN_EUI)
    join_enrollment.close_window("test-setup")
    db_conn.execute("DELETE FROM device_enrollment_candidates")
    db_conn.commit()

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
    db_conn.execute("DELETE FROM device_enrollment_candidates")
    db_conn.commit()


# ---------------------------------------------------------------------------
# The security boundary
# ---------------------------------------------------------------------------


def test_verified_discovery_waits_for_explicit_approval_even_after_window_closes(
    db_conn: sqlite3.Connection, enrollment_env
):
    join_enrollment.open_window("tester")
    dev_eui = _eui_for(KNOWN_MAC)

    decision = evaluate_join_request(db_conn, make_request(dev_eui))

    assert decision.outcome is Outcome.PENDING_APPROVAL
    assert enrollment_env == []
    assert join_enrollment.list_candidates(db_conn)[0]["status"] == "pending"
    join_enrollment.close_window("tester")
    result = join_enrollment.accept_candidate(db_conn, dev_eui, "approver")
    assert result["status"] == "awaiting_join"
    assert result["decided_by"] == "approver"
    assert enrollment_env == [{"device_id": f"node-{KNOWN_MAC}", "dev_eui": dev_eui}]
    # Double-clicks must not register twice, and approval creates no building row.
    join_enrollment.accept_candidate(db_conn, dev_eui, "approver")
    assert len(enrollment_env) == 1
    assert not db_conn.execute("SELECT 1 FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone()


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

    assert outcomes[:3] == [Outcome.PENDING_APPROVAL] * 3
    assert outcomes[3] is Outcome.QUOTA_EXCEEDED
    assert len(join_enrollment.list_candidates(db_conn)) == 3
    assert enrollment_env == []
    # A full scan must still update an existing entry without using more quota.
    evaluate_join_request(db_conn, make_request(_eui_for(macs[0]), dev_nonce=8))
    assert join_enrollment.window_status()["quota_used"] == 3
    assert next(row for row in join_enrollment.list_candidates(db_conn)
                if row["dev_eui"] == _eui_for(macs[0]))["seen_count"] == 2


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
    """A failed approval remains visible and is retried only by an administrator."""
    def boom(**kwargs):
        raise ConnectionError("TTN unreachable")

    monkeypatch.setattr("app.main.register_device_in_ttn", boom)
    join_enrollment.open_window("tester")

    dev_eui = _eui_for(KNOWN_MAC)
    evaluate_join_request(db_conn, make_request(dev_eui))
    result = join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    assert result["status"] == "registration_failed"
    assert result["registration_error"]
    # Radio retries never retry registration, even after an earlier approval.
    evaluate_join_request(db_conn, make_request(dev_eui, dev_nonce=8))
    assert join_enrollment.list_candidates(db_conn)[0]["status"] == "registration_failed"
    monkeypatch.setattr("app.main.register_device_in_ttn", lambda **kwargs: None)
    assert join_enrollment.accept_candidate(db_conn, dev_eui, "tester")["status"] == "awaiting_join"


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


def test_a_ttn_conflict_does_not_claim_success_for_a_possibly_partial_device(
    db_conn: sqlite3.Connection, enrollment_env, monkeypatch
):
    """Identity conflicts can also represent an incomplete TTN registration."""
    def already_there(**kwargs):
        raise HTTPException(status_code=409, detail={"stage": "device_identity"})

    monkeypatch.setattr("app.main.register_device_in_ttn", already_there)
    join_enrollment.open_window("tester")

    dev_eui = _eui_for(KNOWN_MAC)
    evaluate_join_request(db_conn, make_request(dev_eui))
    result = join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    assert result["status"] == "registration_failed"
    assert "conflict" in result["registration_error"]


def test_rejection_survives_new_windows_connections_and_fresh_join_requests(db_conn, enrollment_env):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    join_enrollment.reject_candidate(db_conn, dev_eui, "rejector")
    join_enrollment.close_window("tester")
    join_enrollment.open_window("another-admin")
    fresh = get_db_connection()
    try:
        assert evaluate_join_request(fresh, make_request(dev_eui, dev_nonce=99)).outcome is Outcome.REJECTED
        rows = join_enrollment.list_candidates(fresh)
        assert len(rows) == 1
        assert rows[0]["status"] == "rejected"
        assert rows[0]["decided_by"] == "rejector"
        with pytest.raises(HTTPException) as error:
            join_enrollment.accept_candidate(fresh, dev_eui, "another-admin")
        assert error.value.status_code == 409
        assert enrollment_env == []
    finally:
        fresh.close()


def test_rotation_requires_fresh_proof_and_does_not_expose_keys(db_conn, enrollment_env, monkeypatch):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    monkeypatch.setattr(settings, "LORAWAN_APP_KEY", ATTACKER_KEY.hex())
    with pytest.raises(HTTPException) as error:
        join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    assert error.value.status_code == 409
    assert enrollment_env == []
    evaluate_join_request(db_conn, make_request(dev_eui, ATTACKER_KEY, dev_nonce=8))
    result = join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    assert result["status"] == "awaiting_join"
    output = json.dumps(join_enrollment.list_candidates(db_conn))
    assert APP_KEY_HEX not in output
    assert ATTACKER_KEY.hex() not in output
    assert "verified_key_hash" not in output


def test_concurrent_approval_and_rejection_cannot_overtake_registration(db_conn, enrollment_env, monkeypatch):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    calls = []

    def register(**kwargs):
        calls.append(kwargs["dev_eui"])
        other = get_db_connection()
        try:
            assert join_enrollment.list_candidates(other)[0]["status"] == "registering"
            for action in (join_enrollment.accept_candidate, join_enrollment.reject_candidate):
                with pytest.raises(HTTPException) as error:
                    action(other, dev_eui, "second-admin")
                assert error.value.status_code == 409
        finally:
            other.close()

    monkeypatch.setattr("app.main.register_device_in_ttn", register)
    assert join_enrollment.accept_candidate(db_conn, dev_eui, "first-admin")["status"] == "awaiting_join"
    assert calls == [dev_eui]


def test_interrupted_registration_requires_an_explicit_retry(db_conn, enrollment_env):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    db_conn.execute(
        "UPDATE device_enrollment_candidates SET status = 'registering', registration_started_at = ?",
        (time.time() - join_enrollment.REGISTRATION_LEASE_SECONDS - 1,),
    )
    db_conn.commit()
    assert join_enrollment.list_candidates(db_conn)[0]["status"] == "registration_failed"
    evaluate_join_request(db_conn, make_request(dev_eui, dev_nonce=8))
    assert enrollment_env == []
    assert join_enrollment.accept_candidate(db_conn, dev_eui, "tester")["status"] == "awaiting_join"


def test_first_uplink_during_registration_is_not_overwritten(db_conn, enrollment_env, monkeypatch):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))

    def register(**kwargs):
        other = get_db_connection()
        try:
            join_enrollment.mark_online(other, kwargs["device_id"], kwargs["dev_eui"])
        finally:
            other.close()

    monkeypatch.setattr("app.main.register_device_in_ttn", register)
    result = join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    assert result["status"] == "online"
    assert result["online_at"]


def test_listener_captures_radio_metadata_without_registering(db_conn, enrollment_env):
    from app.services.join_listener import _handle_line

    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    event = {"result": {"name": "gs.up.receive", "data": {"message": {
        "raw_payload": base64.b64encode(make_request(dev_eui).raw).decode(),
        "rx_metadata": [{"gateway_ids": {"gateway_id": "lobby-gateway"}, "rssi": -82, "snr": 7.5}],
    }}}}
    assert _handle_line(json.dumps(event), db_conn)
    row = join_enrollment.list_candidates(db_conn)[0]
    assert row["gateway_id"] == "lobby-gateway"
    assert row["rssi"] == -82
    assert row["snr"] == 7.5
    assert row["status"] == "pending"
    assert enrollment_env == []


def test_reopening_discovery_does_not_reuse_a_stopped_stream(enrollment_env, monkeypatch):
    from app.services import join_listener

    class StreamThread:
        def __init__(self, **kwargs):
            self.stop_event = kwargs["args"][0]

        def is_alive(self):
            return True

        def start(self):
            pass

    monkeypatch.setattr(join_listener.threading, "Thread", StreamThread)
    monkeypatch.setattr(join_listener, "_thread", None)
    monkeypatch.setattr(join_listener, "_stop", threading.Event())
    assert join_listener.start()
    first = join_listener._thread
    join_listener.stop()
    assert first.stop_event.is_set()
    assert join_listener.start()
    second = join_listener._thread
    assert first is not second
    assert not second.stop_event.is_set()
    join_listener.stop()


def test_pending_queue_is_scoped_to_the_application(db_conn, enrollment_env, monkeypatch):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    monkeypatch.setattr(settings, "TTN_APP_ID", "another-application")
    assert join_enrollment.list_candidates(db_conn) == []
    with pytest.raises(HTTPException) as error:
        join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    assert error.value.status_code == 404
    assert enrollment_env == []


@pytest.fixture
def enrollment_admin(client, db_conn, enrollment_env):
    from app.main import create_login_session, create_password_hash

    password = create_password_hash("EnrollmentApprovalTest123!")
    db_conn.execute(
        """INSERT OR REPLACE INTO users
        (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (9824, 'enrollment-admin@test.local', 'Enrollment Admin', 'admin', 1, ?, ?)""",
        (password["password_hash"], password["password_salt"]),
    )
    db_conn.commit()
    client.cookies.set("sbms_session", create_login_session(db_conn, 9824))
    return client


def test_portal_approval_then_environment_nickname_uplink(enrollment_admin, db_conn, enrollment_env, monkeypatch):
    client = enrollment_admin
    dev_eui = _eui_for("aa119922cc33")
    device_id = "node-aa119922cc33"
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "approval-test-secret")
    monkeypatch.setattr("app.main.LOCAL_TEST_MODE", True)
    monkeypatch.setattr("app.services.join_listener.start", lambda: True)
    response = client.post("/admin/enrollment/open", json={"seconds": 300})
    assert response.status_code == 200
    evaluate_join_request(db_conn, make_request(dev_eui))
    response = client.get("/admin/enrollment/status")
    assert response.json()["candidates"][0]["status"] == "pending"
    assert enrollment_env == []

    envelope = {
        "end_device_ids": {"device_id": device_id, "dev_eui": dev_eui,
                           "application_ids": {"application_id": settings.TTN_APP_ID}},
        "uplink_message": {"f_port": 10, "frm_payload": base64.b64encode(b"Environment lobby").decode()},
    }
    # Even a trusted webhook must not turn a pending candidate into a device.
    client.post("/ttn-webhook", json=envelope, headers={"X-Webhook-Secret": "approval-test-secret"})
    assert not db_conn.execute("SELECT 1 FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone()
    candidate = join_enrollment.list_candidates(db_conn)[0]
    assert candidate["status"] == "pending"
    assert candidate["online_at"] is None
    client.post("/admin/enrollment/close")
    response = client.post(f"/admin/enrollment/candidates/{dev_eui}/accept")
    assert response.status_code == 200, response.text
    assert response.json()["candidates"][0]["status"] == "awaiting_join"
    assert len(enrollment_env) == 1
    response = client.post("/ttn-webhook", json=envelope, headers={"X-Webhook-Secret": "approval-test-secret"})
    assert response.status_code == 200
    candidate = client.get("/admin/enrollment/status").json()["candidates"][0]
    assert candidate["status"] == "online"
    device = db_conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone()
    assert device["label"] == "Environment lobby"
    assert device["is_placed"] == 0
    db_conn.execute("DELETE FROM devices WHERE dev_eui = ?", (dev_eui,))
    db_conn.commit()


@pytest.mark.parametrize("profile_failure", ["outdated", "disabled"])
@pytest.mark.parametrize("port", [1, 10])
def test_approved_uplink_marks_online_even_when_profile_context_fails(
    client, db_conn, enrollment_env, monkeypatch, profile_failure, port
):
    from app.main import auto_provision_discovered_device, load_device_profile_context_for_webhook

    dev_eui = _eui_for("aa119933cc44")
    device_id = "node-aa119933cc44"
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "profile-failure-test-secret")
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    join_enrollment.close_window("tester")
    envelope = {
        # TTN may rename its human-readable ID. Enrollment follows DevEUI,
        # with the same case-insensitive matching as device resolution.
        "end_device_ids": {"device_id": "renamed-environment-node", "dev_eui": dev_eui.lower(),
                           "application_ids": {"application_id": settings.TTN_APP_ID}},
        "uplink_message": {"f_port": port, "frm_payload": base64.b64encode(b"Environment lobby").decode()},
    }
    device = auto_provision_discovered_device(
        db_conn, device_id,
        {"end_device_ids": {"device_id": device_id, "dev_eui": dev_eui}},
    )
    profile_id = device["profile_id"]
    original_enabled = db_conn.execute(
        "SELECT enabled FROM sensor_profiles WHERE id = ?", (profile_id,),
    ).fetchone()["enabled"]
    try:
        if profile_failure == "outdated":
            db_conn.execute("UPDATE devices SET profile_version = profile_version + 1 WHERE device_id = ?", (device_id,))
        else:
            db_conn.execute("UPDATE sensor_profiles SET enabled = 0 WHERE id = ?", (profile_id,))
        db_conn.commit()
        # Exercise the real profile checks, not a stubbed failure.
        with pytest.raises(HTTPException) as error:
            load_device_profile_context_for_webhook(db_conn, "renamed-environment-node", envelope)
        assert error.value.status_code == 409
        assert join_enrollment.list_candidates(db_conn)[0]["status"] == "awaiting_join"

        response = client.post(
            "/ttn-webhook", json=envelope,
            headers={"X-Webhook-Secret": "profile-failure-test-secret"},
        )
        assert response.status_code == 200
        candidate = join_enrollment.list_candidates(db_conn)[0]
        assert candidate["status"] == "online"
        assert candidate["online_at"]
        assert candidate["device_id"] == device_id
        # Profile rejection still prevents nickname/telemetry processing.
        stored = db_conn.execute("SELECT label FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        assert stored["label"] == device["label"]
    finally:
        db_conn.execute("UPDATE sensor_profiles SET enabled = ? WHERE id = ?", (original_enabled, profile_id))
        db_conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        db_conn.commit()


@pytest.mark.parametrize("message", [None, {}])
def test_join_accept_or_empty_uplink_does_not_mark_approved_candidate_online(
    client, db_conn, enrollment_env, monkeypatch, message
):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "non-uplink-test-secret")
    dev_eui = _eui_for("aa119944cc55")
    device_id = "node-aa119944cc55"
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    join_enrollment.accept_candidate(db_conn, dev_eui, "tester")
    join_enrollment.close_window("tester")

    def reject_profile_context(*args, **kwargs):
        raise HTTPException(status_code=409, detail="No telemetry profile available")

    monkeypatch.setattr("app.main.load_device_profile_context_for_webhook", reject_profile_context)
    envelope = {
        "end_device_ids": {"device_id": device_id, "dev_eui": dev_eui},
        "join_accept": {"session_key_id": "test-session"},
    }
    if message is not None:
        envelope["uplink_message"] = message
    response = client.post(
        "/ttn-webhook", json=envelope,
        headers={"X-Webhook-Secret": "non-uplink-test-secret"},
    )
    assert response.status_code == 200
    candidate = join_enrollment.list_candidates(db_conn)[0]
    assert candidate["status"] == "awaiting_join"
    assert candidate["online_at"] is None


def test_portal_rejection_is_persistent_and_audited(enrollment_admin, db_conn, enrollment_env):
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    response = enrollment_admin.post(f"/admin/enrollment/candidates/{dev_eui}/reject")
    assert response.status_code == 200, response.text
    assert response.json()["candidates"][0]["status"] == "rejected"
    assert enrollment_admin.post(f"/admin/enrollment/candidates/{dev_eui}/accept").status_code == 409
    assert enrollment_env == []
    audit = db_conn.execute(
        "SELECT actor FROM audit_log WHERE action = 'device_enrollment_rejected' AND target_id = ? ORDER BY id DESC LIMIT 1",
        (f"node-{KNOWN_MAC}",),
    ).fetchone()
    assert audit["actor"] == "enrollment-admin@test.local"


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_portal_rejects_unknown_and_malformed_candidates(enrollment_admin, action):
    assert enrollment_admin.post(f"/admin/enrollment/candidates/0011223344556677/{action}").status_code == 404
    assert enrollment_admin.post(f"/admin/enrollment/candidates/not-an-eui/{action}").status_code == 400


@pytest.mark.parametrize("role", [None, "client"])
@pytest.mark.parametrize("action", ["accept", "reject"])
def test_approval_endpoints_require_an_admin(client, db_conn, enrollment_env, role, action):
    from app.main import create_login_session, create_password_hash

    if role:
        password = create_password_hash("EnrollmentClientTest123!")
        db_conn.execute(
            """INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
            VALUES (9825, 'enrollment-client@test.local', 'Client', 'client', 1, ?, ?)""",
            (password["password_hash"], password["password_salt"]),
        )
        db_conn.commit()
        client.cookies.set("sbms_session", create_login_session(db_conn, 9825))
    dev_eui = _eui_for(KNOWN_MAC)
    join_enrollment.open_window("tester")
    evaluate_join_request(db_conn, make_request(dev_eui))
    response = client.post(f"/admin/enrollment/candidates/{dev_eui}/{action}", follow_redirects=False)
    assert response.status_code in (302, 401, 403)
    assert join_enrollment.list_candidates(db_conn)[0]["status"] == "pending"
    assert enrollment_env == []


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
