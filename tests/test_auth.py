from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.main import create_login_session, create_password_hash


def test_auth_session_revoked_on_disable(client: TestClient, db_conn: sqlite3.Connection):
    pw_data = create_password_hash("Password123!")

    # Seed active user
    cursor = db_conn.execute("""
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('user_dis@test.local', 'Disable Test', 'client', 1, ?, ?)
    """, (pw_data["password_hash"], pw_data["password_salt"]))
    user_id = cursor.lastrowid

    # Seed active admin user
    cursor_admin = db_conn.execute("""
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('admin_dis@test.local', 'Admin Disable Test', 'admin', 1, ?, ?)
    """, (pw_data["password_hash"], pw_data["password_salt"]))
    admin_id = cursor_admin.lastrowid
    db_conn.commit()

    # Create active sessions
    raw_token = create_login_session(db_conn, user_id)
    admin_token = create_login_session(db_conn, admin_id)

    # Verify session is active
    sess = db_conn.execute("SELECT revoked_at FROM auth_sessions WHERE user_id = ?", (user_id,)).fetchone()
    assert sess is not None
    assert sess["revoked_at"] is None

    # Disable user
    client.cookies.set("sbms_session", admin_token)
    res = client.post(f"/users/{user_id}/disable")
    assert res.status_code == 200, res.text

    # Verify session is revoked
    from app.db.connection import get_db_connection
    fresh_conn = get_db_connection()
    sess_after = fresh_conn.execute("SELECT revoked_at FROM auth_sessions WHERE user_id = ?", (user_id,)).fetchone()
    fresh_conn.close()
    assert sess_after["revoked_at"] is not None







def test_auth_session_revoked_on_credentials_update(client: TestClient, db_conn: sqlite3.Connection):
    # Seed active user & admin
    pw_data = create_password_hash("Password123!")
    cursor = db_conn.execute("""
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('client_pw@test.local', 'Client PW Test', 'client', 1, ?, ?)
    """, (pw_data["password_hash"], pw_data["password_salt"]))
    user_id = cursor.lastrowid

    cursor_admin = db_conn.execute("""
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES ('admin_pw@test.local', 'Admin PW Test', 'admin', 1, ?, ?)
    """, (pw_data["password_hash"], pw_data["password_salt"]))
    admin_id = cursor_admin.lastrowid
    db_conn.commit()

    # Create active session for client user
    raw_client_token = create_login_session(db_conn, user_id)
    raw_admin_token = create_login_session(db_conn, admin_id)

    # Admin updates client credentials (new password)
    client.cookies.set("sbms_session", raw_admin_token)
    res = client.put(
        f"/users/{user_id}/credentials",
        json={"password": "NewSecretPassword123!"},
    )
    assert res.status_code == 200, res.text

    # Verify client session is revoked
    sess_after = db_conn.execute("SELECT revoked_at FROM auth_sessions WHERE user_id = ?", (user_id,)).fetchone()
    assert sess_after["revoked_at"] is not None
