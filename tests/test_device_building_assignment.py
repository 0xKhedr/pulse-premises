from app.main import create_login_session, create_password_hash


def test_device_building_creation_and_assignment(client, db_conn):
    # 0. Seed admin user & session
    pw_data = create_password_hash("Password123!")
    cursor_admin = db_conn.execute("""
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('admin_bldg_test@test.local', 'Admin Building Test', 'admin', 1, ?, ?)
    """, (pw_data["password_hash"], pw_data["password_salt"]))
    admin_id = cursor_admin.lastrowid
    db_conn.commit()

    admin_token = create_login_session(db_conn, admin_id)
    client.cookies.set("sbms_session", admin_token)

    # 1. Setup client, site, building, floor, room
    c_res = client.post("/clients", json={"name": "Building Test Client"})
    assert c_res.status_code == 200, c_res.text
    client_id = c_res.json()["client_id"]

    s_res = client.post("/sites", json={"client_id": client_id, "name": "Building Test Site"})
    assert s_res.status_code == 200, s_res.text
    site_id = s_res.json()["site_id"]

    b1_res = client.post("/buildings", json={"site_id": site_id, "name": "Alpha Tower"})
    assert b1_res.status_code == 200, b1_res.text
    b1_id = b1_res.json()["building_id"]

    b2_res = client.post("/buildings", json={"site_id": site_id, "name": "Beta Tower"})
    assert b2_res.status_code == 200, b2_res.text
    b2_id = b2_res.json()["building_id"]

    f_res = client.post("/floors", json={"building_id": b1_id, "name": "Floor 1", "floor_number": "1"})
    assert f_res.status_code == 200, f_res.text
    f_id = f_res.json()["floor_id"]

    f2_res = client.post("/floors", json={"building_id": b2_id, "name": "Floor 2", "floor_number": "2"})
    assert f2_res.status_code == 200, f2_res.text
    f2_id = f2_res.json()["floor_id"]

    r_res = client.post(f"/floors/{f_id}/rooms", json={
        "room_name": "Lobby 101",
        "polygon_points": [{"x": 10, "y": 10}, {"x": 50, "y": 10}, {"x": 50, "y": 50}]
    })
    assert r_res.status_code == 200, r_res.text
    r_id = r_res.json()["room_id"]

    # 2. Test POST /devices creating a device assigned to Alpha Tower (b1_id)
    dev1_res = client.post("/devices", json={
        "device_id": "BUILDING-NODE-001",
        "label": "Lobby Sensor",
        "node_type": "environment",
        "building_id": b1_id,
        "floor_id": f_id,
        "room_id": r_id
    })
    assert dev1_res.status_code == 200, dev1_res.text
    assert dev1_res.json()["status"] == "created"
    assert dev1_res.json()["device"]["device_id"] == "BUILDING-NODE-001"
    assert dev1_res.json()["device"]["building_id"] == b1_id

    # 3. Test GET /buildings/{building_id}/devices
    bldg_devs_res = client.get(f"/buildings/{b1_id}/devices")
    assert bldg_devs_res.status_code == 200, bldg_devs_res.text
    devices = bldg_devs_res.json()
    assert any(d["device_id"] == "BUILDING-NODE-001" for d in devices)

    # 4. Test PUT /devices/{device_id}/assign-building to reassign device to Beta Tower (b2_id)
    reassign_res = client.put("/devices/BUILDING-NODE-001/assign-building", json={
        "floor_id": f2_id
    })
    assert reassign_res.status_code == 200, reassign_res.text
    assert reassign_res.json()["status"] == "assigned"
    assert reassign_res.json()["device"]["building_id"] == b2_id

    # 5. Verify device moved from b1 to b2
    b1_devs = client.get(f"/buildings/{b1_id}/devices").json()
    assert not any(d["device_id"] == "BUILDING-NODE-001" for d in b1_devs)

    b2_devs = client.get(f"/buildings/{b2_id}/devices").json()
    assert any(d["device_id"] == "BUILDING-NODE-001" for d in b2_devs)

    # 6. Test error validation
    dup_res = client.post("/devices", json={
        "device_id": "BUILDING-NODE-001",
        "building_id": b1_id
    })
    assert dup_res.status_code == 400

    bad_bldg_res = client.post("/devices", json={
        "device_id": "BUILDING-NODE-999",
        "building_id": 999999
    })
    assert bad_bldg_res.status_code == 400


def test_position_move_clears_stale_lower_location_ids(client, db_conn):
    pw_data = create_password_hash("PositionAdminPass123!")
    cursor = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('position_admin@test.local', 'Position Admin', 'admin', 1, ?, ?)
        """,
        (pw_data["password_hash"], pw_data["password_salt"]),
    )
    client.cookies.set("sbms_session", create_login_session(db_conn, cursor.lastrowid))

    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Position Client')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Position Site')",
        (client_id,),
    ).lastrowid
    first_building = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'First Building')",
        (site_id,),
    ).lastrowid
    second_building = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Second Building')",
        (site_id,),
    ).lastrowid
    second_floor = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'New Floor')",
        (second_building,),
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'Old Floor')",
        (first_building,),
    ).lastrowid
    room_id = db_conn.execute(
        """
        INSERT INTO rooms (floor_id, room_name, polygon_points, x, y)
        VALUES (?, 'Old Room', '[]', 0, 0)
        """,
        (floor_id,),
    ).lastrowid
    db_conn.execute(
        """
        INSERT INTO devices (
            chip_mac, device_id, client_id, site_id, building_id, floor_id,
            room_id, building, floor, room, is_placed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'First Building', 'Old Floor', 'Old Room', 1)
        """,
        (
            "position-device-mac",
            "position-device",
            client_id,
            site_id,
            first_building,
            floor_id,
            room_id,
        ),
    )
    db_conn.commit()

    response = client.put(
        "/devices/position-device/position",
        json={"x": 5, "y": 7, "floor_id": second_floor},
    )
    assert response.status_code == 200, response.text

    row = db_conn.execute(
        "SELECT * FROM devices WHERE device_id = 'position-device'"
    ).fetchone()
    assert row["building_id"] == second_building
    assert row["floor_id"] == second_floor
    assert row["room_id"] is None
    assert row["building"] == "Second Building"
    assert row["floor"] == "New Floor"
    assert row["room"] is None


def test_position_rejects_unknown_scope_and_invalid_coordinates(client, db_conn):
    pw_data = create_password_hash("PositionValidationPass123!")
    cursor = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('position_validation@test.local', 'Position Validation', 'admin', 1, ?, ?)
        """,
        (pw_data["password_hash"], pw_data["password_salt"]),
    )
    client.cookies.set("sbms_session", create_login_session(db_conn, cursor.lastrowid))
    db_conn.execute(
        "INSERT INTO devices (chip_mac, device_id) VALUES (?, ?)",
        ("position-validation-mac", "position-validation-device"),
    )
    db_conn.commit()

    unknown = client.put(
        "/devices/position-validation-device/position",
        json={"x": 1, "y": 2, "floor_id": 999999999},
    )
    invalid = client.put(
        "/devices/position-validation-device/position",
        json={"x": "left", "y": 2, "floor_id": 999999999},
    )

    assert unknown.status_code == 404
    assert invalid.status_code == 400


def test_device_cannot_be_placed_at_site_or_building_only(client, db_conn):
    pw_data = create_password_hash("FloorOnlyPlacementPass123!")
    cursor = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('floor_only_admin@test.local', 'Floor Only Admin', 'admin', 1, ?, ?)
        """,
        (pw_data["password_hash"], pw_data["password_salt"]),
    )
    client.cookies.set("sbms_session", create_login_session(db_conn, cursor.lastrowid))
    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Floor Only Client')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Floor Only Site')",
        (client_id,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Floor Only Building')",
        (site_id,),
    ).lastrowid
    db_conn.execute(
        "INSERT INTO devices (chip_mac, device_id) VALUES (?, ?)",
        ("floor-only-existing-mac", "floor-only-existing"),
    )
    db_conn.commit()

    create_at_site = client.post(
        "/devices",
        json={"device_id": "site-only-device", "site_id": site_id},
    )
    create_at_building = client.post(
        "/devices",
        json={"device_id": "building-only-device", "building_id": building_id},
    )
    move_to_site = client.put(
        "/devices/floor-only-existing/position",
        json={"x": 1, "y": 2, "site_id": site_id},
    )
    move_to_building = client.put(
        "/devices/floor-only-existing/position",
        json={"x": 1, "y": 2, "building_id": building_id},
    )

    assert create_at_site.status_code == 400
    assert create_at_building.status_code == 400
    assert move_to_site.status_code == 400
    assert move_to_building.status_code == 400
