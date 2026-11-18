from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.db.connection import get_db_connection, run_migrations
from app.main import app, check_offline_devices, create_login_session, process_ttn_webhook_background
from app.services import join_enrollment
from app.services.device_lifecycle import remove_device
from app.services.device_state import build_device_state
from app.services.ttn import TTNClient, ttn_client

EUI = "1122334455667788"
DEVICE = "lifecycle-node"
KEY = "00112233445566778899AABBCCDDEEFF"


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_FILE", str(tmp_path / "lifecycle.db"))
    monkeypatch.setattr(settings, "PREVIEW_MODE", False)
    monkeypatch.setattr(settings, "TTN_GATEWAY_ATTEMPT_LOG_DIR", "")
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_ENABLED", True)
    monkeypatch.setattr(settings, "TTN_APP_ID", "lifecycle-app")
    monkeypatch.setattr(settings, "JOIN_EUI", "0000000000000000")
    monkeypatch.setattr(settings, "LORAWAN_APP_KEY", KEY)
    monkeypatch.setattr(settings, "DEVICE_STALE_AFTER_SECONDS", 60)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_AFTER_SECONDS", 180)
    monkeypatch.setattr("app.main.schedule_broadcast", lambda *args, **kwargs: None)
    monkeypatch.setattr(ttn_client, "get_application_device", lambda _: {"ids": {"dev_eui": EUI}})
    deleted = []
    monkeypatch.setattr(ttn_client, "delete_device", lambda device_id: deleted.append(device_id))
    monkeypatch.setattr("app.main.register_device_in_ttn", lambda **kwargs: None)
    monkeypatch.setattr(join_enrollment, "_enrollment_profile", lambda _: {"id": 1})
    run_migrations()
    conn = get_db_connection()
    yield conn, deleted
    conn.close()


def add_device(conn, *, last_seen=None, placed=False, device_id=DEVICE, eui=EUI):
    if placed:
        conn.execute("INSERT OR IGNORE INTO clients(id,name) VALUES (1,'Client')")
        conn.execute("INSERT OR IGNORE INTO sites(id,client_id,name) VALUES (1,1,'Site')")
        conn.execute("INSERT OR IGNORE INTO buildings(id,site_id,name) VALUES (1,1,'Building')")
        conn.execute("INSERT OR IGNORE INTO floors(id,building_id,name) VALUES (1,1,'Floor')")
    conn.execute("""INSERT INTO devices(device_id,chip_mac,dev_eui,last_seen,status,is_placed,floor_id)
        VALUES (?,?,?,?, 'online',?,?)""", (device_id, device_id, eui, last_seen, int(placed), 1 if placed else None))
    conn.commit()


def add_candidate(conn, status="awaiting_join", *, eui=EUI, device_id=DEVICE):
    conn.execute("""INSERT INTO device_enrollment_candidates(application_id,dev_eui,join_eui,device_id,
        status,verified_key_hash,decided_at) VALUES (?,?, '0000000000000000',?,?,?,datetime('now','-30 minutes'))""",
        (settings.TTN_APP_ID, eui, device_id, status, hashlib.sha256(bytes.fromhex(KEY)).hexdigest()))
    conn.commit()


def admin_client(conn):
    user_id = conn.execute("INSERT INTO users(email,name,role,enabled) VALUES ('lifecycle@local','Lifecycle','admin',1)").lastrowid
    conn.commit()
    client = TestClient(app)
    client.cookies.set("sbms_session", create_login_session(conn, user_id))
    return client


def test_delete_removes_ttn_before_local_state_and_blocks_delayed_uplink(lifecycle):
    conn, deleted = lifecycle
    add_device(conn)
    add_candidate(conn, "online")
    conn.execute("INSERT INTO device_latest_telemetry(device_id,telemetry) VALUES (?, '{}')", (DEVICE,))
    conn.commit()
    response = admin_client(conn).delete(f"/devices/{DEVICE}")
    assert response.status_code == 200, response.text
    assert response.json()["ttn_deleted"]
    assert deleted == [DEVICE]
    assert not conn.execute("SELECT 1 FROM devices WHERE device_id=?", (DEVICE,)).fetchone()
    assert not conn.execute("SELECT 1 FROM device_latest_telemetry WHERE device_id=?", (DEVICE,)).fetchone()
    assert join_enrollment.list_candidates(conn)[0]["status"] == "rejected"
    result = process_ttn_webhook_background({"end_device_ids": {"device_id": DEVICE, "dev_eui": EUI},
                                           "uplink_message": {"decoded_payload": {"temperature": 22}}})
    assert result["status"] == "rejected_enrollment_not_approved"
    assert not conn.execute("SELECT 1 FROM devices WHERE device_id=?", (DEVICE,)).fetchone()


def test_partial_ttn_delete_keeps_local_device_and_can_resume(lifecycle, monkeypatch):
    conn, deleted = lifecycle
    add_device(conn)
    def timeout(_):
        raise requests.Timeout("sensitive upstream response")
    monkeypatch.setattr(ttn_client, "delete_device", timeout)
    with pytest.raises(HTTPException) as error:
        remove_device(conn, DEVICE, EUI, "admin")
    assert error.value.status_code == 502
    assert "sensitive" not in error.value.detail
    assert conn.execute("SELECT deletion_error FROM devices WHERE device_id=?", (DEVICE,)).fetchone()[0]
    assert join_enrollment.uplink_is_blocked(conn, DEVICE, EUI)
    monkeypatch.setattr(ttn_client, "delete_device", lambda device_id: deleted.append(device_id))
    remove_device(conn, DEVICE, EUI, "admin")
    assert deleted == [DEVICE]


def test_delete_uses_observed_ttn_id_and_refuses_eui_mismatch(lifecycle, monkeypatch):
    conn, deleted = lifecycle
    add_device(conn)
    conn.execute("UPDATE devices SET ttn_device_id='renamed-node'")
    conn.commit()
    monkeypatch.setattr(ttn_client, "get_application_device", lambda _: {"ids": {"dev_eui": "FFFFFFFFFFFFFFFF"}})
    with pytest.raises(HTTPException) as error:
        remove_device(conn, DEVICE, EUI, "admin")
    assert error.value.status_code == 409
    assert deleted == []
    monkeypatch.setattr(ttn_client, "get_application_device", lambda _: {"ids": {"dev_eui": EUI}})
    remove_device(conn, DEVICE, EUI, "admin")
    assert deleted == ["renamed-node"]


def test_ttn_delete_visits_all_components_and_ignores_only_missing(lifecycle, monkeypatch):
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url))
        response = requests.Response()
        response.status_code = 404 if '/as/' in url else 200
        response._content = b'{}'
        return response
    monkeypatch.setattr("app.services.ttn.requests.request", request)
    client = TTNClient()
    client.delete_device("a/b")
    assert len(calls) == 4
    assert all(method == "DELETE" and url.endswith('/devices/a%2Fb') for method, url in calls)
    assert ['/as/', '/ns/', '/js/'] == [next(prefix for prefix in ('/as/', '/ns/', '/js/') if prefix in url) for _, url in calls[:3]]
    def forbidden(*args, **kwargs):
        response = requests.Response()
        response.status_code = 403
        return response
    monkeypatch.setattr("app.services.ttn.requests.request", forbidden)
    with pytest.raises(requests.HTTPError):
        client.delete_device(DEVICE)


def test_offline_policy_updates_unplaced_devices_but_only_alarms_placed(lifecycle):
    conn, _ = lifecycle
    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=180)).isoformat()
    add_device(conn, last_seen=old, placed=True)
    add_device(conn, last_seen=old, device_id="unplaced", eui="2233445566778899")
    check_offline_devices(conn, now)
    check_offline_devices(conn, now)
    assert conn.execute("SELECT COUNT(*) FROM devices WHERE status='offline'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM alarm_history WHERE alarm_type='SYSTEM_OFFLINE'").fetchone()[0] == 1
    telemetry = conn.execute("SELECT updated_at FROM device_latest_telemetry WHERE device_id=?", (DEVICE,)).fetchone()
    assert telemetry[0] is None, 'Offline placeholder must not simulate a fresh uplink'
    message = conn.execute("SELECT alarm_message FROM alarm_history").fetchone()[0]
    assert "180 seconds" in message
    conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (now.isoformat(), DEVICE))
    conn.commit()
    check_offline_devices(conn, now)
    assert conn.execute("SELECT status FROM devices WHERE device_id=?", (DEVICE,)).fetchone()[0] == "online"
    assert conn.execute("SELECT resolved FROM alarm_history").fetchone()[0] == 1


def test_connection_state_uses_freshest_evidence_and_configured_boundaries():
    now = datetime.now(timezone.utc)
    device = {"last_seen": (now - timedelta(days=2)).isoformat(), "telemetry_updated_at": now.isoformat()}
    assert build_device_state(device, now=now)["connection_state"] == "online"
    assert build_device_state({"last_seen": (now - timedelta(seconds=60)).isoformat()}, now=now,
                              stale_after_seconds=60, offline_after_seconds=180)["connection_state"] == "stale"
    assert build_device_state({}, now=now)["connection_state"] == "never_connected"


def test_analytics_and_device_cards_share_the_same_policy(lifecycle):
    conn, _ = lifecycle
    now = datetime.now(timezone.utc)
    add_device(conn, last_seen=(now - timedelta(seconds=100)).isoformat())
    client = admin_client(conn)
    stats = client.get('/api/analytics').json()
    assert stats['stale_devices'] == 1
    assert stats['offline_devices'] == 0
    summary = client.get('/admin/summary').json()
    assert summary['devices']['stale'] == 1
    assert summary['devices']['offline'] == 0


def test_revoke_waiting_device_and_reconsider_requires_explicit_accept(lifecycle):
    conn, deleted = lifecycle
    add_candidate(conn)
    join_enrollment.revoke_candidate(conn, EUI, "admin")
    assert deleted == [DEVICE]
    assert join_enrollment.uplink_is_blocked(conn, DEVICE, EUI)
    assert join_enrollment.list_candidates(conn)[0]["status"] == "rejected"
    join_enrollment.reconsider_candidate(conn, EUI, "admin")
    assert join_enrollment.uplink_is_blocked(conn, DEVICE, EUI)
    assert join_enrollment.accept_candidate(conn, EUI, "admin")["status"] == "awaiting_join"
    assert not join_enrollment.uplink_is_blocked(conn, DEVICE, EUI)


def test_failed_revocation_remains_blocked_and_does_not_race_first_uplink(lifecycle, monkeypatch):
    conn, deleted = lifecycle
    add_candidate(conn)
    def timeout(_):
        join_enrollment.mark_online(conn, DEVICE, EUI)
        raise requests.Timeout()
    monkeypatch.setattr(ttn_client, "delete_device", timeout)
    with pytest.raises(HTTPException):
        join_enrollment.revoke_candidate(conn, EUI, "admin")
    assert join_enrollment.list_candidates(conn)[0]["status"] == "revocation_failed"
    with pytest.raises(HTTPException):
        join_enrollment.accept_candidate(conn, EUI, "admin")
    assert join_enrollment.uplink_is_blocked(conn, DEVICE, EUI)
    monkeypatch.setattr(ttn_client, "delete_device", lambda device_id: deleted.append(device_id))
    assert join_enrollment.revoke_candidate(conn, EUI, "admin")["status"] == "rejected"


def test_ttn_check_reports_partial_registration_without_claiming_online(lifecycle, monkeypatch):
    conn, _ = lifecycle
    add_candidate(conn)
    monkeypatch.setattr(ttn_client, "inspect_device_registration", lambda *args: {
        "components": {"is": "registered", "ns": "registered", "as": "missing", "js": "registered"},
        "complete": False, "session_started_at": "2026-11-17T09:00:00Z"})
    result = join_enrollment.inspect_candidate(conn, EUI, "admin")
    assert result['status'] == 'awaiting_join'
    assert result['ttn_check']['components']['as'] == 'missing'
    assert result['online_at'] is None
    assert result['attention_required']
    assert result['waiting_seconds'] >= 1700


def test_ttn_inspection_never_requests_or_returns_session_keys(lifecycle, monkeypatch):
    calls = []
    def request(method, url, **kwargs):
        calls.append(kwargs['params']['field_mask'])
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'ids': {'dev_eui': EUI}, 'session': {
            'started_at': '2026-11-17T09:00:00Z', 'keys': {'app_s_key': 'must-not-return'}}}).encode()
        return response
    monkeypatch.setattr('app.services.ttn.requests.request', request)
    result = TTNClient().inspect_device_registration(DEVICE, EUI)
    assert result['complete']
    assert all('keys' not in mask for mask in calls)
    assert 'must-not-return' not in json.dumps(result)


def test_bulk_approval_refuses_nonpending_and_bounds_requests(lifecycle):
    conn, _ = lifecycle
    add_candidate(conn, 'pending')
    add_candidate(conn, 'rejected', eui='2233445566778899', device_id='rejected-node')
    client = admin_client(conn)
    response = client.post('/admin/enrollment/bulk', json={'action': 'accept', 'dev_euis': [EUI, '2233445566778899']})
    assert response.status_code == 200, response.text
    results = response.json()['results']
    assert results[0]['success']
    assert not results[1]['success']
    assert client.post('/admin/enrollment/bulk', json={'action': 'accept', 'dev_euis': [EUI]*51}).status_code == 400
    assert client.post('/admin/enrollment/bulk', json={'action': 'revoke', 'dev_euis': [EUI]}).status_code == 400


def test_bulk_stops_after_registration_failure(lifecycle, monkeypatch):
    conn, _ = lifecycle
    add_candidate(conn, 'pending')
    add_candidate(conn, 'pending', eui='2233445566778899', device_id='second-node')
    def fail(**kwargs):
        raise requests.Timeout()
    monkeypatch.setattr('app.main.register_device_in_ttn', fail)
    result = admin_client(conn).post('/admin/enrollment/bulk', json={'action': 'accept', 'dev_euis': [EUI, '2233445566778899']}).json()
    assert not any(row['success'] for row in result['results'])
    assert 'Not processed' in result['results'][1]['detail']
    assert next(row for row in result['candidates'] if row['dev_eui'] == '2233445566778899')['status'] == 'pending'


def test_explicit_provisioning_reenrolls_deleted_identity(lifecycle, monkeypatch):
    from app.schemas.core import Device
    from app.services.lorawan_identity import derive_dev_eui_from_mac
    from app.services.provisioning import provision

    conn, _ = lifecycle
    mac = 'aabbccddeeff'
    eui = derive_dev_eui_from_mac(mac, namespace=settings.TTN_APP_ID)
    device_id = f'node-{mac}'
    add_candidate(conn, 'rejected', eui=eui, device_id=device_id)
    conn.execute("""INSERT INTO device_admission_blocks(application_id,device_id,dev_eui,state,actor)
        VALUES (?,?,?,'deleted','admin')""", (settings.TTN_APP_ID, device_id, eui))
    conn.execute("INSERT INTO clients(id,name) VALUES (7001,'Client')")
    conn.execute("INSERT INTO sites(id,client_id,name) VALUES (7001,7001,'Site')")
    conn.execute("INSERT INTO buildings(id,site_id,name) VALUES (7001,7001,'Building')")
    conn.execute("INSERT INTO floors(id,building_id,name) VALUES (7001,7001,'Floor')")
    conn.execute("INSERT INTO rooms(id,floor_id,room_name,polygon_points,x,y) VALUES (7001,7001,'Room','[]',0,0)")
    conn.commit()
    monkeypatch.setattr('app.main.validate_config', lambda: None)
    monkeypatch.setattr('app.main.sync_tb_attributes_from_profile', lambda **kwargs: {'status':'disabled'})
    profile = dict(conn.execute("SELECT * FROM sensor_profiles WHERE profile_code='CAYENNE_LPP_V1'").fetchone())
    result = provision(Device(chip_mac=mac, room_id=7001, profile_id=profile['id'], **{
        key: profile[key] for key in ('node_type','profile_code','profile_version','payload_version',
                                      'payload_encoder_key','uplink_interval_seconds')}))
    assert result['status'] == 'created'
    assert not join_enrollment.uplink_is_blocked(conn, device_id, eui)
    assert join_enrollment.list_candidates(conn)[0]['status'] == 'awaiting_join'
