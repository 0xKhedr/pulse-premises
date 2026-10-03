"""
The HTTP surface for per-device alarm thresholds.

The merge logic is covered in test_alarm_overrides.py. What is tested here is
the wiring: that a threshold set through the API is the one an uplink would be
judged by, that reverting really does return the device to its profile, and
that the endpoints refuse what they should.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import (
    build_device_alarm_profile,
    create_login_session,
    create_password_hash,
    get_sensor_profile_detail,
)

ADMIN = {"user_id": 9801, "email": "override_admin@test.local"}
PASSWORD = "OverrideApiPass123!"

DEVICE_ID = "override_api_dev"
PROFILE_CODE = "OVERRIDE_API_V1"


@pytest.fixture
def seeded(db_conn: sqlite3.Connection):
    """An admin, a profile with one numeric and one boolean rule, a device."""
    pw = create_password_hash(PASSWORD)
    db_conn.execute(
        """
        INSERT OR REPLACE INTO users
            (id, email, name, role, enabled, password_hash, password_salt)
        VALUES (?, ?, 'Override Admin', 'admin', 1, ?, ?)
        """,
        (ADMIN["user_id"], ADMIN["email"], pw["password_hash"],
         pw["password_salt"]),
    )

    db_conn.execute(
        "DELETE FROM sensor_profiles WHERE profile_code = ?", (PROFILE_CODE,)
    )
    cursor = db_conn.execute(
        """
        INSERT INTO sensor_profiles (
            profile_code, profile_name, profile_version, node_type,
            payload_encoder_key, payload_version, f_port,
            uplink_interval_seconds, ttn_formatter_code, ttn_formatter_type,
            tb_device_profile_name, icon_type, icon_color, status, enabled,
            is_system, schema_checksum, created_by, updated_by
        ) VALUES (
            ?, 'Override API Test', 1, 'multi',
            'cayenne_lpp_v1', 1, 1, 60, '', 'none',
            'default', 'multi', '#000000', 'active', 1, 0,
            'override_api_checksum', 'test', 'test'
        )
        """,
        (PROFILE_CODE,),
    )
    profile_id = cursor.lastrowid

    db_conn.execute(
        """
        INSERT INTO sensor_profile_fields (
            profile_id, field_key, label, unit, data_type, payload_order,
            required, nullable, display_order, visible_floor, visible_dashboard
        ) VALUES (?, 'temperature', 'Temperature', 'C', 'number', 1,
                  0, 1, 1, 1, 1)
        """,
        (profile_id,),
    )
    db_conn.execute(
        """
        INSERT INTO sensor_profile_rules (
            profile_id, rule_code, field_key, operator, threshold_value,
            severity, alarm_type, message_template, debounce_seconds,
            cooldown_seconds, auto_resolve, enabled
        ) VALUES (?, 'API_HIGH_TEMP', 'temperature', '>', 30.0,
                  'warning', 'sensor_alarm', 'too hot', 0, 0, 1, 1)
        """,
        (profile_id,),
    )
    db_conn.execute(
        """
        INSERT INTO sensor_profile_rules (
            profile_id, rule_code, field_key, operator, expected_boolean,
            severity, alarm_type, message_template, debounce_seconds,
            cooldown_seconds, auto_resolve, enabled
        ) VALUES (?, 'API_MOTION', 'motion', '==', 1,
                  'warning', 'sensor_alarm', 'movement', 0, 0, 1, 1)
        """,
        (profile_id,),
    )

    db_conn.execute(
        """
        INSERT OR REPLACE INTO devices
            (device_id, chip_mac, label, node_type, profile_id, profile_code,
             profile_version, payload_version)
        VALUES (?, 'AA:BB:CC:98:01:01', 'Override Test Node', 'multi',
                ?, ?, 1, 1)
        """,
        (DEVICE_ID, profile_id, PROFILE_CODE),
    )
    db_conn.execute(
        "DELETE FROM device_alarm_overrides WHERE device_id = ?", (DEVICE_ID,)
    )
    db_conn.commit()

    yield {"profile_id": profile_id}

    db_conn.execute(
        "DELETE FROM device_alarm_overrides WHERE device_id = ?", (DEVICE_ID,)
    )
    db_conn.execute("DELETE FROM devices WHERE device_id = ?", (DEVICE_ID,))
    db_conn.execute(
        "DELETE FROM sensor_profile_rules WHERE profile_id = ?", (profile_id,)
    )
    db_conn.execute(
        "DELETE FROM sensor_profile_fields WHERE profile_id = ?", (profile_id,)
    )
    db_conn.execute(
        "DELETE FROM sensor_profiles WHERE id = ?", (profile_id,)
    )
    db_conn.commit()


@pytest.fixture
def admin_client(client: TestClient, seeded, db_conn: sqlite3.Connection):
    client.cookies.set(
        "sbms_session", create_login_session(db_conn, ADMIN["user_id"])
    )
    return client


def _rule(payload: dict, code: str) -> dict:
    matches = [r for r in payload["rules"] if r["rule_code"] == code]
    assert matches, f"{code} missing from {[r['rule_code'] for r in payload['rules']]}"
    return matches[0]


def test_anonymous_callers_are_refused(client: TestClient, seeded):
    client.cookies.clear()
    response = client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP",
        json={"threshold_value": 10},
        follow_redirects=False,
    )
    assert response.status_code in (302, 401, 403)


def test_a_device_starts_fully_inherited(admin_client: TestClient):
    body = admin_client.get(f"/alarm-overrides/{DEVICE_ID}").json()

    assert body["profile_code"] == PROFILE_CODE
    assert body["stale_overrides"] == []

    rule = _rule(body, "API_HIGH_TEMP")
    assert rule["overridden"] is False
    assert rule["override"] is None
    assert rule["profile"]["threshold_value"] == 30.0
    assert rule["effective"]["threshold_value"] == 30.0
    # Labels come from the profile's field declaration, not the raw key.
    assert rule["field_label"] == "Temperature"
    assert rule["unit"] == "C"


def test_setting_a_threshold_changes_only_that_device(
    admin_client: TestClient, db_conn: sqlite3.Connection, seeded
):
    response = admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP",
        json={"threshold_value": 24, "note": "server cupboard"},
    )
    assert response.status_code == 200

    rule = response.json()["rule"]
    assert rule["effective"]["threshold_value"] == 24.0
    assert rule["profile"]["threshold_value"] == 30.0
    assert rule["overridden"] is True

    # The profile itself is untouched, so every other device still trips at 30.
    profile = get_sensor_profile_detail(db_conn, seeded["profile_id"])
    profile_rule = next(
        r for r in profile["rules"] if r["rule_code"] == "API_HIGH_TEMP"
    )
    assert profile_rule["threshold_value"] == 30.0

    # And the uplink path would judge this device by the new number.
    merged = build_device_alarm_profile(db_conn, DEVICE_ID, profile)
    merged_rule = next(
        r for r in merged["rules"] if r["rule_code"] == "API_HIGH_TEMP"
    )
    assert merged_rule["threshold_value"] == 24.0


def test_muting_is_reported_as_false_not_inherited(admin_client: TestClient):
    response = admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_MOTION", json={"enabled": False}
    )
    assert response.status_code == 200

    rule = response.json()["rule"]
    assert rule["override"]["enabled"] is False
    assert rule["effective"]["enabled"] is False
    assert rule["profile"]["enabled"] is True


def test_a_threshold_on_a_boolean_rule_is_rejected(admin_client: TestClient):
    response = admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_MOTION", json={"threshold_value": 5}
    )
    assert response.status_code == 400
    assert "numeric threshold" in str(response.json()["detail"])


def test_an_unknown_rule_code_is_rejected(admin_client: TestClient):
    response = admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/NOT_A_RULE", json={"threshold_value": 5}
    )
    assert response.status_code == 404


def test_an_unknown_device_is_rejected(admin_client: TestClient):
    response = admin_client.put(
        "/alarm-overrides/no_such_device/API_HIGH_TEMP",
        json={"threshold_value": 5},
    )
    assert response.status_code == 404


def test_reverting_returns_the_device_to_its_profile(
    admin_client: TestClient, db_conn: sqlite3.Connection, seeded
):
    admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP",
        json={"threshold_value": 24},
    )

    response = admin_client.delete(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP"
    )
    assert response.status_code == 200
    assert response.json()["rule"]["overridden"] is False
    assert response.json()["rule"]["effective"]["threshold_value"] == 30.0

    profile = get_sensor_profile_detail(db_conn, seeded["profile_id"])
    assert build_device_alarm_profile(db_conn, DEVICE_ID, profile) is profile

    # Reverting twice is a mistake worth reporting, not a silent no-op.
    assert admin_client.delete(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP"
    ).status_code == 404


def test_the_picker_counts_what_each_device_deviates_by(
    admin_client: TestClient,
):
    admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP",
        json={"threshold_value": 24},
    )

    devices = admin_client.get("/alarm-overrides").json()["devices"]
    row = next(d for d in devices if d["device_id"] == DEVICE_ID)

    assert row["override_count"] == 1
    assert row["rule_count"] == 2
    assert row["profile_code"] == PROFILE_CODE


def test_every_change_is_audited(
    admin_client: TestClient, db_conn: sqlite3.Connection
):
    admin_client.put(
        f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP",
        json={"threshold_value": 24},
    )
    admin_client.delete(f"/alarm-overrides/{DEVICE_ID}/API_HIGH_TEMP")

    actions = [
        row[0]
        for row in db_conn.execute(
            """
            SELECT action FROM audit_log
            WHERE device_id = ?
              AND action LIKE 'device_alarm_override%'
            ORDER BY id
            """,
            (DEVICE_ID,),
        )
    ]
    assert "device_alarm_override_set" in actions
    assert "device_alarm_override_cleared" in actions
