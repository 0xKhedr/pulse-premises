import sqlite3

import pytest

from app.config import settings
from app.db.connection import run_migrations
from app.services.preview import gateway_stats, seed_preview


def test_preview_seed_is_complete_and_idempotent(tmp_path, monkeypatch):
    database = tmp_path / 'preview.db'
    monkeypatch.setattr(settings, 'DB_FILE', str(database))
    run_migrations()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        seed_preview(conn)
        seed_preview(conn)
        for table in ('clients', 'sites', 'buildings', 'floors', 'rooms', 'devices',
                      'gateways', 'sensor_profiles', 'sensor_catalog', 'firmware_modules',
                      'users', 'user_access', 'site_maps', 'floorplans', 'alarm_history',
                      'audit_log', 'alarm_recipients', 'device_latest_telemetry'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 2, table
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT COUNT(*) FROM devices WHERE is_placed=1 AND floor_id IS NOT NULL').fetchone()[0] == 2
        scopes = conn.execute('SELECT DISTINCT client_id FROM user_access').fetchall()
        assert len(scopes) == 2


def test_preview_seed_refuses_existing_workspace(tmp_path, monkeypatch):
    database = tmp_path / 'existing.db'
    monkeypatch.setattr(settings, 'DB_FILE', str(database))
    run_migrations()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO clients(name) VALUES ('Existing customer')")
        with pytest.raises(RuntimeError, match='new database'):
            seed_preview(conn)
        assert conn.execute("SELECT name FROM clients WHERE name='Existing customer'").fetchone()
        assert conn.execute('SELECT COUNT(*) FROM devices').fetchone()[0] == 0


def test_preview_gateway_states():
    assert gateway_stats('demo-gateway-1')['uplink_count'] > 0
    assert gateway_stats('demo-gateway-2') == {}
