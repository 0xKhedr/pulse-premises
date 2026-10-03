from app.main import (
    create_login_session,
    create_password_hash,
    get_sensor_profile_detail,
    process_ttn_webhook_background,
    validate_decoded_payload_against_profile,
)


def test_cayenne_payload_validation(db_conn):
    """
    Test that validate_decoded_payload_against_profile normalizes Cayenne LPP keys
    (e.g., temperature_1 -> temperature, relative_humidity_2 -> humidity).
    """
    profile = get_sensor_profile_detail(db_conn, "CAYENNE_LPP_V1", include_formatter=False)
    assert profile is not None, "CAYENNE_LPP_V1 profile must exist"

    raw_cayenne = {
        "temperature_1": 24.5,
        "relative_humidity_2": 53.0,
        "presence_3": 1,
        "analog_in_4": 3.92,
        "voltage_5": 220.5,
    }

    result = validate_decoded_payload_against_profile(profile, raw_cayenne)
    assert result["valid"] is True
    telemetry = result["telemetry"]

    # Verify standard normalized fields
    assert telemetry["temperature"] == 24.5
    assert telemetry["humidity"] == 53.0
    assert telemetry["motion"] is True
    assert telemetry["voltage"] == 220.5

    # Verify original channel fields are preserved
    assert telemetry["temperature_1"] == 24.5
    assert telemetry["relative_humidity_2"] == 53.0
    assert telemetry["analog_in_4"] == 3.92


def test_zero_touch_auto_discovery_and_placement(client, db_conn):
    """
    Test zero-touch onboarding:
    1. Unknown device uplinks over TTN.
    2. Backend auto-provisions device with is_placed = 0.
    3. GET /devices/unplaced returns the new device.
    4. PUT /devices/{id}/position places it and sets is_placed = 1.
    5. GET /devices/unplaced no longer returns it.
    """
    # 0. Setup admin session
    pw_data = create_password_hash("Password123!")
    cursor_admin = db_conn.execute("""
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('admin_discovery@test.local', 'Admin Discovery', 'admin', 1, ?, ?)
    """, (pw_data["password_hash"], pw_data["password_salt"]))
    admin_id = cursor_admin.lastrowid
    db_conn.commit()

    admin_token = create_login_session(db_conn, admin_id)
    client.cookies.set("sbms_session", admin_token)

    # 1. Setup a test building and floor for placement
    c_res = client.post("/clients", json={"name": "Discovery Test Client"})
    client_id = c_res.json()["client_id"]

    s_res = client.post("/sites", json={"client_id": client_id, "name": "Discovery Site"})
    site_id = s_res.json()["site_id"]

    b_res = client.post("/buildings", json={"site_id": site_id, "name": "Tower Alpha"})
    building_id = b_res.json()["building_id"]

    f_res = client.post("/floors", json={"building_id": building_id, "name": "Floor 2", "floor_number": "2"})
    floor_id = f_res.json()["floor_id"]

    # 2. Simulate TTN uplink from an unknown LoRa device (1 km away)
    new_device_id = "LILYGO-AUTO-TEST-88"
    new_dev_eui = "0004A30B001C0588"

    uplink_envelope = {
        "end_device_ids": {
            "device_id": new_device_id,
            "dev_eui": new_dev_eui,
            "application_ids": {
                "application_id": "smart-building-environment"
            }
        },
        "uplink_message": {
            "decoded_payload": {
                "temperature_1": 22.8,
                "relative_humidity_2": 48.5,
                "analog_in_3": 3.84
            }
        }
    }

    # Process webhook
    webhook_res = process_ttn_webhook_background(uplink_envelope)
    assert webhook_res["status"] in ("ok_profile_validated", "ok_local_test_profile_validated", "ok_profile_validated_thingsboard_failed")

    # 3. Verify device was automatically created in database
    dev_row = db_conn.execute("SELECT * FROM devices WHERE device_id = ?", (new_device_id,)).fetchone()
    assert dev_row is not None
    dev = dict(dev_row)
    assert dev["device_id"] == new_device_id
    assert dev["dev_eui"] == new_dev_eui
    assert dev["status"] == "online"
    assert dev["is_placed"] == 0
    assert dev["x"] is None
    assert dev["y"] is None

    # 4. Check GET /devices/unplaced
    unplaced_res = client.get("/devices/unplaced")
    assert unplaced_res.status_code == 200
    unplaced_list = unplaced_res.json()
    assert any(d["device_id"] == new_device_id for d in unplaced_list)

    discovered_item = next(d for d in unplaced_list if d["device_id"] == new_device_id)
    assert discovered_item["is_placed"] == 0
    assert discovered_item["telemetry"]["temperature"] == 22.8
    assert discovered_item["telemetry"]["humidity"] == 48.5

    # 5. Place the device on the map via PUT /devices/{id}/position
    place_res = client.put(f"/devices/{new_device_id}/position", json={
        "x": 350,
        "y": 420,
        "floor_id": floor_id
    })
    assert place_res.status_code == 200, place_res.text

    # 6. Verify device is now marked as placed and assigned to floor
    updated_row = db_conn.execute("SELECT * FROM devices WHERE device_id = ?", (new_device_id,)).fetchone()
    assert updated_row["is_placed"] == 1
    assert updated_row["x"] == 350
    assert updated_row["y"] == 420
    assert updated_row["floor_id"] == floor_id

    # 7. Verify device is no longer in the unplaced inbox
    unplaced_after = client.get("/devices/unplaced").json()
    assert not any(d["device_id"] == new_device_id for d in unplaced_after)
