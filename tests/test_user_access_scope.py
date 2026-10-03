from app.main import create_login_session, create_password_hash


def test_user_access_scope_is_canonical_and_rejects_mixed_tenants(client, db_conn):
    pw = create_password_hash("AccessScopePass123!")
    admin_id = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('access_scope_admin@test.local', 'Access Scope Admin', 'admin', 1, ?, ?)
        """,
        (pw["password_hash"], pw["password_salt"]),
    ).lastrowid
    target_id = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled)
        VALUES ('access_scope_client@test.local', 'Access Scope Client', 'client', 1)
        """
    ).lastrowid
    client.cookies.set("sbms_session", create_login_session(db_conn, admin_id))

    first_client = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Access Scope Tenant A')"
    ).lastrowid
    second_client = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Access Scope Tenant B')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Access Scope Site')",
        (first_client,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Access Scope Building')",
        (site_id,),
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'Access Scope Floor')",
        (building_id,),
    ).lastrowid
    db_conn.commit()

    mixed = client.post(
        "/user-access",
        json={
            "user_id": target_id,
            "client_id": second_client,
            "floor_id": floor_id,
        },
    )
    assert mixed.status_code == 400

    created = client.post(
        "/user-access",
        json={"user_id": target_id, "floor_id": floor_id},
    )
    assert created.status_code == 200, created.text
    access = created.json()["access"]
    assert access["client_id"] == first_client
    assert access["site_id"] == site_id
    assert access["building_id"] == building_id
    assert access["floor_id"] == floor_id

    invalid_update = client.put(
        f"/user-access/{access['id']}",
        json={"client_id": second_client},
    )
    assert invalid_update.status_code == 400
