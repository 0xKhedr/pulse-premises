from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import settings
from app.main import create_login_session, create_password_hash
from app.schemas.core import Device
from app.services.lorawan_identity import derive_dev_eui_from_mac


def test_device_schema_valid():
    d = Device(
        chip_mac="AA:BB:CC:DD:EE:FF",
        node_type="sensor_node",
        room_id=1,
        building="Main",
        floor="1",
        room="101",
        label="Temp Sensor 1",
        x=10,
        y=20,
        capabilities=["environment"],
        payload_encoder_key="temp_v1",
        uplink_interval_seconds=60,
        firmware_version="1.0.0",
    )
    assert d.chip_mac == "AA:BB:CC:DD:EE:FF"
    assert d.node_type == "sensor_node"
    assert d.room_id == 1
    assert d.capabilities == ["environment"]


def test_device_schema_minimal():
    d = Device(
        chip_mac="AA:BB:CC:DD:EE:FF",
        node_type="sensor_node",
    )
    assert d.chip_mac == "AA:BB:CC:DD:EE:FF"
    assert d.room_id is None
    assert d.capabilities is None


def test_device_schema_missing_required():
    with pytest.raises(ValidationError):
        Device(node_type="sensor_node")  # Missing chip_mac


def test_post_provision_endpoint(client: TestClient, db_conn: sqlite3.Connection):
    pw = create_password_hash("ProvisionAdminPass123!")
    cursor = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('provision_admin@test.local', 'Provision Admin', 'admin', 1, ?, ?)
        """,
        (pw["password_hash"], pw["password_salt"]),
    )
    client.cookies.set("sbms_session", create_login_session(db_conn, cursor.lastrowid))

    db_conn.execute("INSERT OR REPLACE INTO clients (id, name) VALUES (1, 'Test Client')")
    db_conn.execute("INSERT OR REPLACE INTO sites (id, client_id, name) VALUES (1, 1, 'Test Site')")
    db_conn.execute("INSERT OR REPLACE INTO buildings (id, site_id, name) VALUES (1, 1, 'Building A')")
    db_conn.execute("INSERT OR REPLACE INTO floors (id, building_id, name, floor_number) VALUES (1, 1, 'Floor 1', 1)")
    db_conn.execute("INSERT OR REPLACE INTO rooms (id, floor_id, room_name, polygon_points, x, y) VALUES (999, 1, 'Room 999', '[]', 0, 0)")
    db_conn.execute("""
        INSERT OR REPLACE INTO sensor_profiles (
            id, profile_code, profile_name, node_type, profile_version, payload_version,
            payload_encoder_key, uplink_interval_seconds, ttn_formatter_code, enabled, status
        ) VALUES (
            99, 'TEST_V1', 'Temperature Sensor', 'environment', 1, 1,
            'temp_v1', 60, 'js_code', 1, 'active'
        )
    """)
    db_conn.commit()

    payload = {
        "chip_mac": "AA:BB:CC:DD:EE:FF",
        "node_type": "environment",
        "room_id": 999,
        "profile_id": 99,
        "profile_code": "TEST_V1",
        "profile_version": 1,
        "payload_version": 1,
        "payload_encoder_key": "temp_v1",
        "uplink_interval_seconds": 60,
        "building": "Building A",
        "floor": "Floor 1",
        "room": "Room 999"
    }

    with patch("app.main.register_device_in_ttn") as mock_ttn, \
         patch("app.main.sync_tb_attributes_from_profile") as mock_tb, \
         patch("app.main.validate_config"), \
         patch("app.main.LORAWAN_APP_KEY", "00112233445566778899AABBCCDDEEFF"):
        
        mock_tb.return_value = {"status": "ok", "profile_code": "TEST_V1", "tb_device_profile_name": "default"}

        res = client.post("/provision", json=payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] in ("created", "existing_updated")
        assert data["room_id"] == 999
        if data["status"] == "created":
            expected_dev_eui = derive_dev_eui_from_mac(
                "AABBCCDDEEFF", namespace=settings.TTN_APP_ID
            )
            assert data["dev_eui"] == expected_dev_eui
            assert data["app_key"] == "00112233445566778899AABBCCDDEEFF"
            assert mock_ttn.call_args.kwargs["dev_eui"] == expected_dev_eui
            assert mock_ttn.call_args.kwargs["app_key"] == data["app_key"]


def test_provisioning_endpoints_reject_anonymous(client: TestClient):
    options = client.get("/provision-options", follow_redirects=False)
    provision = client.post("/provision", json={}, follow_redirects=False)

    assert options.status_code in (302, 401, 403)
    assert provision.status_code in (302, 401, 403)
