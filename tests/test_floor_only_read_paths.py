from app.main import create_login_session, create_password_hash


def test_floor_only_device_appears_in_all_floor_read_paths(
    client,
    db_conn,
    monkeypatch,
):
    upstream_calls = []
    monkeypatch.setattr(
        "app.routers.hierarchy.read_tb_latest_telemetry",
        lambda *args, **kwargs: upstream_calls.append((args, kwargs)) or {},
    )
    monkeypatch.setattr(
        "app.routers.telemetry.read_tb_latest_telemetry",
        lambda *args, **kwargs: {},
    )

    pw = create_password_hash("FloorReadPathsPass123!")
    admin_id = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('floor_paths_admin@test.local', 'Floor Paths Admin', 'admin', 1, ?, ?)
        """,
        (pw["password_hash"], pw["password_salt"]),
    ).lastrowid
    client.cookies.set("sbms_session", create_login_session(db_conn, admin_id))

    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Floor Paths Client')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Floor Paths Site')",
        (client_id,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Floor Paths Building')",
        (site_id,),
    ).lastrowid
    floor_id = db_conn.execute(
        """
        INSERT INTO floors (building_id, name, floor_number, image_width, image_height)
        VALUES (?, 'Floor Paths Level', '7', 1000, 800)
        """,
        (building_id,),
    ).lastrowid
    db_conn.execute(
        """
        INSERT INTO devices (
            chip_mac, device_id, client_id, site_id, building_id, floor_id,
            room_id, label, x, y, is_placed
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 1)
        """,
        (
            "floor-paths-chip",
            "floor-paths-device",
            client_id,
            site_id,
            building_id,
            floor_id,
            "Floor Paths Device",
            100,
            200,
        ),
    )
    db_conn.commit()

    location = client.get("/devices/floor-paths-device/location")
    assert location.status_code == 200, location.text
    assert location.json()["room"] is None
    assert location.json()["floor"]["id"] == floor_id
    assert location.json()["building"]["id"] == building_id
    assert location.json()["site"]["id"] == site_id
    assert location.json()["client"]["id"] == client_id

    floor_map = client.get(
        "/floor-map-data",
        params={"building": "Floor Paths Building", "floor": "Floor Paths Level"},
    )
    assert floor_map.status_code == 200, floor_map.text
    assert [row["device_id"] for row in floor_map.json()["devices"]] == [
        "floor-paths-device"
    ]

    floor_live = client.get(f"/floors/{floor_id}/live")
    assert floor_live.status_code == 200, floor_live.text
    assert upstream_calls == []
    assert [row["device_id"] for row in floor_live.json()["floor_devices"]] == [
        "floor-paths-device"
    ]
    assert floor_live.json()["floor"]["client_name"] == "Floor Paths Client"
    assert floor_live.json()["floor"]["site_name"] == "Floor Paths Site"

    status = client.get("/devices-status")
    assert status.status_code == 200, status.text
    status_device = next(
        row for row in status.json() if row["device_id"] == "floor-paths-device"
    )
    assert status_device["floor_id"] == floor_id
    assert status_device["room_id"] is None
