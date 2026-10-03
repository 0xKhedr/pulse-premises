from __future__ import annotations

import json

from app.main import create_login_session, create_password_hash


def test_client_floor_payload_and_activity_hide_credentials(client, db_conn):
    password = "ClientSafetyPass123!"
    pw = create_password_hash(password)
    user_id = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('client_safety@test.local', 'Client Safety', 'client', 1, ?, ?)
        """,
        (pw["password_hash"], pw["password_salt"]),
    ).lastrowid
    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Client Safety Tenant')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Client Safety Site')",
        (client_id,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Client Safety Building')",
        (site_id,),
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'Client Safety Floor')",
        (building_id,),
    ).lastrowid
    room_id = db_conn.execute(
        """
        INSERT INTO rooms (floor_id, room_name, polygon_points, x, y)
        VALUES (?, 'Client Safety Room', '[]', 0, 0)
        """,
        (floor_id,),
    ).lastrowid
    db_conn.execute(
        """
        INSERT INTO user_access (user_id, floor_id, can_view_devices)
        VALUES (?, ?, 1)
        """,
        (user_id, floor_id),
    )
    db_conn.execute(
        """
        INSERT INTO devices (
            chip_mac, device_id, app_key, client_id, site_id, building_id,
            floor_id, room_id, label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "client-safety-chip",
            "client-safety-device",
            "SUPER-SECRET-LORAWAN-ROOT-KEY",
            client_id,
            site_id,
            building_id,
            floor_id,
            room_id,
            "Client Safety Device",
        ),
    )
    db_conn.execute(
        """
        INSERT INTO devices (
            chip_mac, device_id, app_key, client_id, site_id, building_id,
            floor_id, room_id, label, x, y
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            "client-safety-floor-chip",
            "client-safety-floor-device",
            "SECOND-SUPER-SECRET-ROOT-KEY",
            client_id,
            site_id,
            building_id,
            floor_id,
            "Floor-only Device",
            12,
            34,
        ),
    )
    db_conn.execute(
        """
        INSERT INTO alarm_history (device_id, alarm_type, alarm_message)
        VALUES ('client-safety-floor-device', 'threshold', 'Floor-only alarm')
        """
    )
    db_conn.execute(
        """
        INSERT INTO audit_log (
            action, target_type, target_id, details, floor_id, device_id
        ) VALUES ('move_device', 'device', ?, ?, ?, ?)
        """,
        (
            "client-safety-device",
            json.dumps({"app_key": "OLD-SECRET-IN-AUDIT"}),
            floor_id,
            "client-safety-device",
        ),
    )
    db_conn.commit()
    client.cookies.set("sbms_session", create_login_session(db_conn, user_id))

    floor_response = client.get(
        f"/client-portal/{user_id}/floors/{floor_id}/live"
    )
    assert floor_response.status_code == 200, floor_response.text
    floor_data = floor_response.json()
    assert "password_hash" not in floor_data["user"]
    device = floor_data["rooms"][0]["devices"][0]
    assert device["app_key"] == "***hidden***"
    floor_device = floor_data["floor_devices"][0]
    assert floor_device["device_id"] == "client-safety-floor-device"
    assert floor_device["app_key"] == "***hidden***"
    assert "SUPER-SECRET-LORAWAN-ROOT-KEY" not in floor_response.text
    assert "SECOND-SUPER-SECRET-ROOT-KEY" not in floor_response.text

    alarm_export = client.get(
        f"/client-portal/{user_id}/alarms/export.csv",
        params={"device_id": "client-safety-floor-device"},
    )
    assert alarm_export.status_code == 200, alarm_export.text
    assert "client-safety-floor-device" in alarm_export.text
    assert f",{floor_id}," in alarm_export.text

    activity_response = client.get(f"/client-portal/{user_id}/activity-log")
    assert activity_response.status_code == 200, activity_response.text
    assert activity_response.json()[0]["details"]["app_key"] == "***hidden***"
    assert "OLD-SECRET-IN-AUDIT" not in activity_response.text
