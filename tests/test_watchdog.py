from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app.main import check_offline_devices


def test_watchdog_detects_offline_device(db_conn: sqlite3.Connection):
    # Seed device and latest telemetry with timestamp > 24 hours ago
    db_conn.execute("INSERT OR REPLACE INTO clients (id, name) VALUES (1, 'Test Client')")
    db_conn.execute("INSERT OR REPLACE INTO sites (id, client_id, name) VALUES (1, 1, 'Test Site')")
    db_conn.execute("INSERT OR REPLACE INTO buildings (id, site_id, name) VALUES (1, 1, 'Building A')")
    db_conn.execute("INSERT OR REPLACE INTO floors (id, building_id, name, floor_number) VALUES (1, 1, 'Floor 1', 1)")
    db_conn.execute("INSERT OR REPLACE INTO rooms (id, floor_id, room_name, polygon_points, x, y) VALUES (101, 1, 'Room 101', '[]', 0, 0)")

    db_conn.execute("""
        INSERT OR REPLACE INTO devices (chip_mac, device_id, node_type, client_id, site_id, building_id, floor_id, room_id, building, floor, room, is_placed)
        VALUES ('11:22:33:44:55:66', 'dev_stale_1', 'environment', 1, 1, 1, 1, 101, 'Building A', 'Floor 1', 'Room 101', 1)
    """)

    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    db_conn.execute("""
        INSERT OR REPLACE INTO device_latest_telemetry (device_id, updated_at, alarm_active, alarm_message)
        VALUES ('dev_stale_1', ?, 0, 'NORMAL')
    """, (old_time,))
    db_conn.commit()

    # Run check_offline_devices
    check_offline_devices(db_conn)

    # Verify device status updated to OFFLINE and alarm_history record created
    telem = db_conn.execute("SELECT alarm_active, alarm_message FROM device_latest_telemetry WHERE device_id = 'dev_stale_1'").fetchone()
    assert telem["alarm_active"] == 1
    assert telem["alarm_message"] == "OFFLINE"

    history = db_conn.execute("SELECT * FROM alarm_history WHERE device_id = 'dev_stale_1'").fetchone()
    assert history is not None
    assert history["alarm_type"] == "SYSTEM_OFFLINE"
    assert "24 hours" in history["alarm_message"]
