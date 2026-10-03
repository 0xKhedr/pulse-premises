from __future__ import annotations

import json

from app.main import create_login_session, create_password_hash


def _make_admin(db_conn, email="audit_redaction_admin@test.local"):
    password = create_password_hash("AuditRedactionPass123!")
    admin_id = db_conn.execute(
        """
        INSERT INTO users (email, name, role, enabled, password_hash, password_salt)
        VALUES (?, 'Audit Redaction Admin', 'admin', 1, ?, ?)
        """,
        (email, password["password_hash"], password["password_salt"]),
    ).lastrowid
    db_conn.commit()
    return admin_id


def test_audit_log_json_endpoint_redacts_secrets_from_legacy_rows(client, db_conn):
    # Simulate a legacy / out-of-band row that bypassed log_audit_event's
    # write-time redaction (e.g. a direct INSERT, as could happen from an
    # older code path or a manual DB fix). The read path must not trust
    # that every row already had its secrets stripped.
    admin_id = _make_admin(db_conn)
    client.cookies.set("sbms_session", create_login_session(db_conn, admin_id))

    db_conn.execute(
        """
        INSERT INTO audit_log (action, target_type, target_id, details)
        VALUES ('move_device', 'device', 'audit-redaction-device', ?)
        """,
        (
            json.dumps(
                {
                    "app_key": "RAW-AUDIT-JSON-SECRET",
                    "root_key": "RAW-AUDIT-JSON-ROOT-KEY",
                }
            ),
        ),
    )
    db_conn.commit()

    response = client.get(
        "/audit-log", params={"target_id": "audit-redaction-device"}
    )
    assert response.status_code == 200, response.text

    rows = response.json()
    assert len(rows) == 1
    details = rows[0]["details"]
    assert details["app_key"] == "***hidden***"
    assert details["root_key"] == "***hidden***"

    assert "RAW-AUDIT-JSON-SECRET" not in response.text
    assert "RAW-AUDIT-JSON-ROOT-KEY" not in response.text


def test_audit_log_csv_export_redacts_secrets_from_legacy_rows(client, db_conn):
    admin_id = _make_admin(db_conn, email="audit_redaction_admin_csv@test.local")
    client.cookies.set("sbms_session", create_login_session(db_conn, admin_id))

    db_conn.execute(
        """
        INSERT INTO audit_log (action, target_type, target_id, details)
        VALUES ('move_device', 'device', 'audit-redaction-csv-device', ?)
        """,
        (
            json.dumps(
                {
                    "app_key": "RAW-AUDIT-CSV-SECRET",
                    "root_key": "RAW-AUDIT-CSV-ROOT-KEY",
                }
            ),
        ),
    )
    db_conn.commit()

    response = client.get(
        "/audit-log/export.csv",
        params={"target_id": "audit-redaction-csv-device"},
    )
    assert response.status_code == 200, response.text

    assert "RAW-AUDIT-CSV-SECRET" not in response.text
    assert "RAW-AUDIT-CSV-ROOT-KEY" not in response.text
    assert "***hidden***" in response.text
