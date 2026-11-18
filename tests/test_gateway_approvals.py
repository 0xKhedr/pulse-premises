from __future__ import annotations

import json
import threading

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.db.connection import get_db_connection, run_migrations
from app.main import app, create_login_session
from app.services import gateway_approvals as approvals
from app.services import gateway_attempts as attempts
from app.services.ttn import TTNClient, ttn_client

EUI = "AABBCCDD01234567"
ERROR = f"error:pkg/gatewayserver:gateway_eui_not_registered (gateway EUI `{EUI}` is not registered)"
NOW = "2026-11-17T10:00:00+00:00"


@pytest.fixture
def approval_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_FILE", str(tmp_path / "approvals.db"))
    monkeypatch.setattr(settings, "PREVIEW_MODE", False)
    monkeypatch.setattr(settings, "TTN_GATEWAY_MANAGEMENT_API_KEY", "management-test-key")
    monkeypatch.setattr(settings, "TTN_GATEWAY_ATTEMPT_LOG_DIR", "")
    monkeypatch.setattr(settings, "GATEWAY_APPROVAL_MAX_CANDIDATES", 500)
    monkeypatch.setattr(attempts, "_health", {"last_checked_at": None, "error": None})
    run_migrations()
    conn = get_db_connection()
    yield conn
    conn.close()


def failure_entry(protocol="udp"):
    return {"namespace": "gatewayserver/io/udp" if protocol == "udp" else "web",
            "http.path": "" if protocol == "udp" else "/router-info",
            "gateway_eui": EUI, "error": ERROR, "remote_addr": "192.168.10.55:12345"}


def candidate(conn):
    attempts.record_failure(conn, attempts.parse_failure(failure_entry()), NOW)
    conn.commit()
    return conn.execute("SELECT id FROM gateway_approval_candidates").fetchone()[0]


@pytest.fixture
def registrar(monkeypatch):
    calls = []
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda gateway_id: None)

    def register(item):
        calls.append(item)
        return {"ids": {"gateway_id": item["gateway_id"], "eui": item["eui"]},
                "attributes": {"sbms-approval": item["approval_token"]},
                "frequency_plan_ids": [item["frequency_plan_id"]],
                "require_authenticated_connection": bool(item["require_authenticated_connection"])}

    monkeypatch.setattr(ttn_client, "register_gateway", register)
    return calls


@pytest.mark.parametrize("protocol", ["udp", "basic_station"])
def test_actual_ttn_failure_formats(protocol):
    item = attempts.parse_failure(failure_entry(protocol))
    assert item["eui"] == EUI
    assert item["protocol"] == protocol
    assert item["source_ip"] == "192.168.10.55"


def test_non_admission_failures_and_unidentified_requests_are_ignored():
    assert attempts.parse_failure({**failure_entry(), "error": "database unavailable"}) is None
    assert attempts.parse_failure({**failure_entry(), "namespace": "grpc"}) is None
    assert attempts.parse_failure({**failure_entry(), "gateway_eui": "not-an-eui"}) is None
    item = attempts.parse_failure({"namespace": "gatewayserver/io/semtechws",
                                   "http.path": f"/traffic/eui-{EUI}",
                                   "error": "error:pkg/auth:unauthenticated", "remote_addr": "[2001:db8::1]:443"})
    assert item["eui"] == EUI
    assert item["source_ip"] == "2001:db8::1"


def test_discovery_and_repeated_rejected_attempts_never_register(approval_db, registrar):
    item_id = candidate(approval_db)
    approvals.reject_candidate(approval_db, item_id, "admin@example.com")
    attempts.record_failure(approval_db, attempts.parse_failure(failure_entry()), NOW)
    approval_db.commit()
    item = approvals.candidate_by_id(approval_db, item_id)
    assert item["status"] == "rejected"
    assert item["seen_count"] == 2
    assert registrar == []
    assert approval_db.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 0


def test_journal_restart_rotation_partial_lines_and_credentials(approval_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TTN_GATEWAY_ATTEMPT_LOG_DIR", str(tmp_path))
    entry = {**failure_entry(), "authorization": "secret-never-store"}
    line = lambda n: f"boot-1\t{n}\t1794909600\t{json.dumps(entry)}\n"
    current = tmp_path / "attempts.current"
    current.write_text(line(1) + line(2)[:-1])
    attempts.collect_once()
    assert approval_db.execute("SELECT seen_count FROM gateway_approval_candidates").fetchone()[0] == 1
    current.write_text(line(1) + line(2))
    attempts.collect_once()
    attempts.collect_once()  # Restart/re-read does not increment counters.
    assert approval_db.execute("SELECT seen_count FROM gateway_approval_candidates").fetchone()[0] == 2
    current.rename(tmp_path / "attempts.previous")
    current.write_text(line(3))
    attempts.collect_once()
    row = dict(approval_db.execute("SELECT * FROM gateway_approval_candidates").fetchone())
    assert row["seen_count"] == 3
    assert "secret-never-store" not in json.dumps(row)
    assert "error" not in row["reason"].lower()


def test_inbox_cap_updates_known_identities(approval_db, monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_APPROVAL_MAX_CANDIDATES", 1)
    item_id = candidate(approval_db)
    other = attempts.parse_failure({**failure_entry(), "gateway_eui": "1122334455667788"})
    attempts.record_failure(approval_db, other, NOW)
    attempts.record_failure(approval_db, attempts.parse_failure(failure_entry()), NOW)
    approval_db.commit()
    assert approval_db.execute("SELECT COUNT(*) FROM gateway_approval_candidates").fetchone()[0] == 1
    assert approvals.candidate_by_id(approval_db, item_id)["seen_count"] == 2
    assert "full" in attempts.collector_status()["error"]


def test_accept_registers_once_and_assigns_site(approval_db, registrar):
    client_id = approval_db.execute("INSERT INTO clients(name) VALUES ('Gateway client')").lastrowid
    site_id = approval_db.execute("INSERT INTO sites(name,client_id) VALUES ('Gateway site',?)", (client_id,)).lastrowid
    approval_db.commit()
    item_id = candidate(approval_db)
    result = approvals.accept_candidate(approval_db, item_id, {"site_id": site_id}, "admin@example.com")
    assert result["status"] == "accepted"
    assert "approval_token" not in result
    assert registrar[0]["require_authenticated_connection"] == 0
    gateway = approval_db.execute("SELECT * FROM gateways").fetchone()
    assert gateway["client_id"] == client_id
    assert gateway["site_id"] == site_id
    assert gateway["status"] == "offline"
    approvals.accept_candidate(approval_db, item_id, {}, "admin@example.com")
    assert len(registrar) == 1
    with pytest.raises(HTTPException, match="Only unregistered"):
        approvals.reject_candidate(approval_db, item_id, "admin@example.com")


def test_timeout_is_reconciled_without_second_create(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    def uncertain(item):
        registrar.append(item)
        raise requests.Timeout("network secret value")
    monkeypatch.setattr(ttn_client, "register_gateway", uncertain)
    with pytest.raises(HTTPException) as exc:
        approvals.accept_candidate(approval_db, item_id, {}, "tester")
    assert exc.value.status_code == 502
    assert "secret" not in exc.value.detail
    item = approvals.candidate_by_id(approval_db, item_id)
    assert item["status"] == "registration_failed"
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda gateway_id: {
        "ids": {"gateway_id": gateway_id, "eui": EUI},
        "attributes": {"sbms-approval": item["approval_token"]},
        "frequency_plan_ids": [item["frequency_plan_id"]],
        "require_authenticated_connection": bool(item["require_authenticated_connection"]),
    })
    with pytest.raises(HTTPException, match="original gateway"):
        approvals.accept_candidate(approval_db, item_id, {"gateway_id": "another-gateway"}, "tester")
    result = approvals.accept_candidate(approval_db, item_id, {}, "tester")
    assert result["status"] == "accepted"
    assert len(registrar) == 1


def test_foreign_ttn_registration_is_never_adopted(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: {"ids": {"eui": EUI}})
    with pytest.raises(HTTPException) as exc:
        approvals.accept_candidate(approval_db, item_id, {}, "tester")
    assert exc.value.status_code == 409
    assert registrar == []
    assert approval_db.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 0


def test_definitively_refused_settings_can_be_corrected(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    register = ttn_client.register_gateway
    def refuse(_):
        response = requests.Response()
        response.status_code = 400
        raise requests.HTTPError(response=response)
    monkeypatch.setattr(ttn_client, "register_gateway", refuse)
    with pytest.raises(HTTPException):
        approvals.accept_candidate(approval_db, item_id, {"frequency_plan_id": "INVALID_PLAN"}, "tester")
    assert approvals.candidate_by_id(approval_db, item_id)["gateway_id"] is None
    monkeypatch.setattr(ttn_client, "register_gateway", register)
    result = approvals.accept_candidate(approval_db, item_id, {"frequency_plan_id": "EU_863_870_TTN"}, "tester")
    assert result["status"] == "accepted"


def test_changed_authentication_settings_are_not_silently_reconciled(approval_db):
    item_id = candidate(approval_db)
    item = approvals.candidate_by_id(approval_db, item_id)
    item.update(frequency_plan_id="EU_863_870_TTN", require_authenticated_connection=1)
    with pytest.raises(HTTPException, match="authentication settings changed"):
        approvals.assert_registration_matches(item, {
            "ids": {"eui": EUI}, "attributes": {"sbms-approval": item["approval_token"]},
            "frequency_plan_ids": ["EU_863_870_TTN"], "require_authenticated_connection": False,
        })


@pytest.mark.parametrize("data", [{"gateway_id": "../bad"}, {"site_id": -999},
                                   {"site_id": True}, {"require_authenticated_connection": "false"}])
def test_invalid_registration_settings_never_reach_ttn(approval_db, registrar, data):
    item_id = candidate(approval_db)
    with pytest.raises(HTTPException) as exc:
        approvals.accept_candidate(approval_db, item_id, data, "tester")
    assert exc.value.status_code == 400
    assert registrar == []
    assert approvals.candidate_by_id(approval_db, item_id)["status"] == "pending"


def test_basics_station_requires_authentication(approval_db, registrar):
    item_id = candidate(approval_db)
    approval_db.execute("UPDATE gateway_approval_candidates SET protocol='basic_station'")
    approval_db.commit()
    with pytest.raises(HTTPException):
        approvals.accept_candidate(approval_db, item_id, {"require_authenticated_connection": False}, "tester")
    approvals.accept_candidate(approval_db, item_id, {}, "tester")
    assert registrar[0]["require_authenticated_connection"] == 1


def test_concurrent_accepts_have_one_registration(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    started = threading.Event()
    release = threading.Event()
    results = []
    def register(item):
        registrar.append(item)
        started.set()
        assert release.wait(5)
        return {"ids": {"eui": EUI}, "attributes": {"sbms-approval": item["approval_token"]},
                "frequency_plan_ids": [item["frequency_plan_id"]],
                "require_authenticated_connection": bool(item["require_authenticated_connection"])}
    monkeypatch.setattr(ttn_client, "register_gateway", register)
    def first_accept():
        conn = get_db_connection()
        try:
            results.append(approvals.accept_candidate(conn, item_id, {}, "first"))
        finally:
            conn.close()
    thread = threading.Thread(target=first_accept)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(HTTPException, match="already in progress"):
            approvals.accept_candidate(approval_db, item_id, {}, "second")
    finally:
        release.set()
        thread.join(5)
    assert len(registrar) == 1
    assert results[0]["status"] == "accepted"


def login(conn, client, role="admin"):
    user_id = conn.execute("INSERT INTO users(email,name,role,enabled) VALUES (?,?,?,1)",
                           (f"{role}@gateway.local", role, role)).lastrowid
    conn.commit()
    client.cookies.set("sbms_session", create_login_session(conn, user_id))


def test_routes_auth_origin_audit_and_one_time_key(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    with TestClient(app) as client:
        assert client.get("/gateways/approvals", follow_redirects=False).status_code in (302, 401)
        login(approval_db, client, "client")
        assert client.post(f"/gateways/approvals/{item_id}/accept", json={}).status_code == 403
        login(approval_db, client)
        response = client.get("/gateways/approvals")
        assert response.status_code == 200
        assert "approval_token" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.post(f"/gateways/approvals/{item_id}/reject", headers={"Origin": "https://attacker.local"}, json={}).status_code == 403
        assert client.post(f"/gateways/approvals/{item_id}/accept", json={}).status_code == 200
        accepted = approvals.candidate_by_id(approval_db, item_id)
        monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: {
            "ids": {"eui": EUI}, "attributes": {"sbms-approval": accepted["approval_token"]},
            "frequency_plan_ids": [accepted["frequency_plan_id"]],
            "require_authenticated_connection": bool(accepted["require_authenticated_connection"]),
        })
        monkeypatch.setattr(ttn_client, "create_gateway_link_key", lambda _: {"key": "NNSXS.test-secret", "id": "key-id"})
        key = client.post(f"/gateways/approvals/{item_id}/connection-key", json={})
        assert key.status_code == 200
        assert key.json()["key"] == "NNSXS.test-secret"
        assert key.headers["cache-control"] == "no-store"
        assert "NNSXS" not in client.get("/gateways/approvals").text
        audit = approval_db.execute("SELECT * FROM audit_log WHERE action LIKE 'gateway_%'").fetchall()
        assert {row["action"] for row in audit} >= {"gateway_accepted", "gateway_connection_key_created"}
        assert "NNSXS" not in str([dict(row) for row in audit])
        monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: {"ids": {"eui": "1122334455667788"}})
        def must_not_create(_):
            raise AssertionError("A changed TTN identity must never receive a key")
        monkeypatch.setattr(ttn_client, "create_gateway_link_key", must_not_create)
        assert client.post(f"/gateways/approvals/{item_id}/connection-key", json={}).status_code == 409
        assert "Gateway connection requests" in client.get("/gateway-monitor").text


def test_management_key_required_but_rejection_works(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    monkeypatch.setattr(settings, "TTN_GATEWAY_MANAGEMENT_API_KEY", "")
    with pytest.raises(HTTPException, match="Configure"):
        approvals.accept_candidate(approval_db, item_id, {}, "tester")
    assert approvals.reject_candidate(approval_db, item_id, "tester")["status"] == "rejected"
    assert registrar == []


def test_collector_heartbeat_reports_offline(approval_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TTN_GATEWAY_ATTEMPT_LOG_DIR", str(tmp_path))
    assert not attempts.collector_status()["active"]
    (tmp_path / "heartbeat").touch()
    assert attempts.collector_status()["active"]


def test_gateway_management_requests_use_separate_key(approval_db, monkeypatch):
    monkeypatch.setattr(settings, "TTN_GATEWAY_OWNER_TYPE", "users")
    monkeypatch.setattr(settings, "TTN_GATEWAY_OWNER_ID", "gateway-admin")
    monkeypatch.setattr(settings, "TTN_GATEWAY_SERVER_ADDRESS", "building.local")
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        result = requests.Response()
        result.status_code = 200
        result._content = b'{}'
        return result
    monkeypatch.setattr("app.services.ttn.requests.request", request)
    client = TTNClient()
    item = {"gateway_id": "gateway-test", "eui": EUI, "name": "Test gateway",
            "frequency_plan_id": "EU_863_870_TTN", "require_authenticated_connection": 1,
            "approval_token": "test-marker"}
    client.register_gateway(item)
    client.create_gateway_link_key("gateway-test")
    assert all(call[2]["headers"]["Authorization"] == "Bearer management-test-key" for call in calls)
    assert calls[0][1].endswith('/api/v3/users/gateway-admin/gateways')
    assert calls[0][2]["json"]["gateway"]["gateway_server_address"] == "building.local"
    assert calls[1][2]["json"]["rights"] == ["RIGHT_GATEWAY_LINK"]


def test_delete_gateway_removes_ttn_before_sbms_and_resets_approval(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    accepted = approvals.accept_candidate(approval_db, item_id, {}, "tester")
    gateway = approval_db.execute("SELECT * FROM gateways").fetchone()
    old_token = approvals.candidate_by_id(approval_db, item_id)["approval_token"]
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: {"ids": {"eui": EUI}})
    calls = []

    def delete(gateway_id):
        conn = get_db_connection()
        try:
            assert conn.execute("SELECT gateway_id FROM gateways").fetchone()[0] == gateway_id
        finally:
            conn.close()
        calls.append(gateway_id)

    monkeypatch.setattr(ttn_client, "delete_gateway", delete)
    with TestClient(app) as client:
        login(approval_db, client)
        result = client.delete(f"/gateways/{gateway['id']}")
        assert result.status_code == 200
        assert result.json()["ttn_deleted"] is True
        assert client.post(f"/gateways/approvals/{item_id}/connection-key").status_code == 409
    assert calls == [accepted["gateway_id"]]
    assert approval_db.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 0
    reset = approvals.candidate_by_id(approval_db, item_id)
    assert reset["status"] == "pending"
    assert reset["gateway_id"] is None
    assert reset["approval_token"] != old_token
    assert approval_db.execute("SELECT COUNT(*) FROM audit_log WHERE action='gateway_deleted'").fetchone()[0] == 1
    attempts.record_failure(approval_db, attempts.parse_failure(failure_entry()), NOW)
    approval_db.commit()
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: None)
    assert approvals.accept_candidate(approval_db, item_id, {"gateway_id": "gw-replacement"}, "tester")["status"] == "accepted"


@pytest.mark.parametrize("failure", ["timeout", 401, 403, 500])
def test_delete_gateway_failure_retains_local_record_and_approval(approval_db, registrar, monkeypatch, failure):
    item_id = candidate(approval_db)
    approvals.accept_candidate(approval_db, item_id, {}, "tester")
    gateway = approval_db.execute("SELECT * FROM gateways").fetchone()
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: {"ids": {"eui": EUI}})

    def delete(_):
        if failure == "timeout":
            raise requests.Timeout("private upstream diagnostics")
        response = requests.Response()
        response.status_code = failure
        raise requests.HTTPError("private upstream diagnostics", response=response)

    monkeypatch.setattr(ttn_client, "delete_gateway", delete)
    with TestClient(app) as client:
        login(approval_db, client)
        result = client.delete(f"/gateways/{gateway['id']}")
    assert result.status_code == 502
    assert "private upstream diagnostics" not in result.text
    assert approval_db.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 1
    assert approvals.candidate_by_id(approval_db, item_id)["status"] == "accepted"


def test_delete_manual_gateway_with_already_absent_ttn_registration(approval_db, monkeypatch):
    gateway_id = approval_db.execute(
        "INSERT INTO gateways(gateway_id,name) VALUES ('manual-gateway','Manual gateway')"
    ).lastrowid
    approval_db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        response = requests.Response()
        response.status_code = 404
        response._content = b'{}'
        return response

    monkeypatch.setattr("app.services.ttn.requests.request", request)
    with TestClient(app) as client:
        login(approval_db, client)
        assert client.delete(f"/gateways/{gateway_id}").status_code == 200
    assert calls == ["GET", "DELETE"]
    assert approval_db.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 0


def test_delete_gateway_guards_access_and_changed_identity(approval_db, registrar, monkeypatch):
    item_id = candidate(approval_db)
    approvals.accept_candidate(approval_db, item_id, {}, "tester")
    gateway = approval_db.execute("SELECT * FROM gateways").fetchone()
    monkeypatch.setattr(ttn_client, "get_managed_gateway", lambda _: {"ids": {"eui": "1122334455667788"}})

    def never_delete(_):
        raise AssertionError("Deletion must not reach TTN")

    monkeypatch.setattr(ttn_client, "delete_gateway", never_delete)
    with TestClient(app) as client:
        path = f"/gateways/{gateway['id']}"
        assert client.delete(path, follow_redirects=False).status_code in (302, 401)
        login(approval_db, client, "client")
        assert client.delete(path).status_code == 403
        login(approval_db, client)
        assert client.delete(path, headers={"Origin": "https://attacker.local"}).status_code == 403
        assert client.delete(path).status_code == 409
        monkeypatch.setattr(settings, "TTN_GATEWAY_MANAGEMENT_API_KEY", "")
        assert client.delete(path).status_code == 409
    assert approval_db.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 1


@pytest.mark.parametrize("status", [200, 404, 403])
def test_ttn_gateway_delete_retries_absent_registration_and_uses_management_key(approval_db, monkeypatch, status):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs["headers"]["Authorization"]))
        response = requests.Response()
        response.status_code = status
        response._content = b'{}'
        return response

    monkeypatch.setattr("app.services.ttn.requests.request", request)
    client = TTNClient()
    if status == 403:
        with pytest.raises(requests.HTTPError):
            client.delete_gateway("test/gateway")
    else:
        client.delete_gateway("test/gateway")
    assert calls == [("DELETE", f"{client.base_url}/api/v3/gateways/test%2Fgateway", "Bearer management-test-key")]
