from app.main import (
    auto_provision_discovered_device,
    get_floorplan_ids_for_floors_and_rooms,
    process_ttn_webhook_background,
    profile_alarm_rule_matches,
    save_alarm_history,
)


def test_save_alarm_history_stores_alarm_type(db_conn):
    """
    Verify save_alarm_history correctly persists alarm_type separately from alarm_message.
    """
    dev_id = "test-alarm-type-node"
    db_conn.execute(
        "INSERT OR IGNORE INTO devices (chip_mac, device_id, label, status) VALUES (?, ?, ?, 'online')",
        (dev_id, dev_id, "Alarm Test Node"),
    )
    db_conn.commit()

    save_alarm_history(
        conn=db_conn,
        device_id=dev_id,
        node_type="multi",
        building="B1",
        floor="F1",
        room="R1",
        alarm_message="Low battery level",
        telemetry={"battery_percent": 15},
        alarm_type="system",
    )

    row = db_conn.execute(
        "SELECT alarm_type, alarm_message FROM alarm_history WHERE device_id = ? ORDER BY id DESC LIMIT 1",
        (dev_id,),
    ).fetchone()

    assert row is not None
    assert row["alarm_type"] == "system"
    assert row["alarm_message"] == "Low battery level"


def test_system_alarm_debouncing_and_autoresolve(db_conn):
    """
    Verify that repeated low-battery uplinks do not flood alarm_history with duplicate rows,
    and that recovering battery clears/resolves the active alarm.
    """
    dev_id = "test-debounce-node"
    # Ensure clean state
    db_conn.execute("DELETE FROM alarm_history WHERE device_id = ?", (dev_id,))
    db_conn.execute("DELETE FROM devices WHERE device_id = ?", (dev_id,))
    db_conn.commit()

    envelope_low = {
        "end_device_ids": {"device_id": dev_id, "dev_eui": "1122334455667788"},
        "uplink_message": {
            "decoded_payload": {
                "temperature": 22.0,
                "battery_percent": 15.0,  # <= 20 -> 'low'
            }
        },
    }

    # Discovery packet: telemetry is accepted, but alarms remain suppressed.
    res1 = process_ttn_webhook_background(envelope_low)
    assert res1.get("status") in ("ok_local_test_profile_validated", "ok_profile_validated")
    assert res1["alarms_suppressed"] is True

    client_id = db_conn.execute(
        "INSERT INTO clients (name) VALUES ('Debounce Client')"
    ).lastrowid
    site_id = db_conn.execute(
        "INSERT INTO sites (client_id, name) VALUES (?, 'Debounce Site')",
        (client_id,),
    ).lastrowid
    building_id = db_conn.execute(
        "INSERT INTO buildings (site_id, name) VALUES (?, 'Debounce Building')",
        (site_id,),
    ).lastrowid
    floor_id = db_conn.execute(
        "INSERT INTO floors (building_id, name) VALUES (?, 'Debounce Floor')",
        (building_id,),
    ).lastrowid
    db_conn.execute(
        "UPDATE devices SET floor_id = ?, is_placed = 1 WHERE device_id = ?",
        (floor_id, dev_id),
    )
    db_conn.commit()

    # First operational packet triggers the alarm after floor placement.
    process_ttn_webhook_background(envelope_low)

    active_count1 = db_conn.execute(
        "SELECT COUNT(*) AS c FROM alarm_history WHERE device_id = ? AND alarm_type = 'system' AND resolved = 0",
        (dev_id,),
    ).fetchone()["c"]
    assert active_count1 == 1, "First packet should create exactly 1 active system alarm"

    # Repeated low battery must be debounced (no new history record).
    res2 = process_ttn_webhook_background(envelope_low)
    total_count2 = db_conn.execute(
        "SELECT COUNT(*) AS c FROM alarm_history WHERE device_id = ? AND alarm_type = 'system'",
        (dev_id,),
    ).fetchone()["c"]
    assert total_count2 == 1, "Second packet with same condition must NOT create a duplicate alarm_history row"

    # Packet 3: battery recovers to healthy (85% -> 'ok')
    envelope_ok = {
        "end_device_ids": {"device_id": dev_id, "dev_eui": "1122334455667788"},
        "uplink_message": {
            "decoded_payload": {
                "temperature": 22.0,
                "battery_percent": 85.0,
            }
        },
    }
    process_ttn_webhook_background(envelope_ok)

    # Active system alarms should now be resolved
    active_count3 = db_conn.execute(
        "SELECT COUNT(*) AS c FROM alarm_history WHERE device_id = ? AND alarm_type = 'system' AND resolved = 0",
        (dev_id,),
    ).fetchone()["c"]
    assert active_count3 == 0, "System alarm should be automatically marked resolved when battery recovers"


def test_auto_discovery_sets_chip_mac(db_conn):
    """
    Verify auto-provisioning populates chip_mac so the primary key is never NULL.
    """
    dev_id = "test-chip-mac-discovery"
    eui = "AABBCCDDEEFF0011"
    db_conn.execute("DELETE FROM devices WHERE device_id = ?", (dev_id,))
    db_conn.commit()

    envelope = {
        "end_device_ids": {"device_id": dev_id, "dev_eui": eui},
        "uplink_message": {"decoded_payload": {"temperature": 21.0}},
    }

    discovered = auto_provision_discovered_device(db_conn, dev_id, envelope)
    assert discovered.get("chip_mac") == eui
    assert discovered.get("device_id") == dev_id


def test_floorplan_query_does_not_conflate_floor_ids(db_conn):
    """
    Verify get_floorplan_ids_for_floors_and_rooms only matches floor_id, not floorplan.id.
    """
    # Create client, site, building
    cur = db_conn.execute("INSERT INTO clients (name) VALUES ('Test Client FP')")
    client_id = cur.lastrowid
    cur = db_conn.execute("INSERT INTO sites (client_id, name) VALUES (?, 'Site FP')", (client_id,))
    site_id = cur.lastrowid
    cur = db_conn.execute("INSERT INTO buildings (site_id, name) VALUES (?, 'Bld FP')", (site_id,))
    bld_id = cur.lastrowid

    # Create floor A and floor B
    cur = db_conn.execute("INSERT INTO floors (building_id, name) VALUES (?, 'Floor A')", (bld_id,))
    floor_a_id = cur.lastrowid
    cur = db_conn.execute("INSERT INTO floors (building_id, name) VALUES (?, 'Floor B')", (bld_id,))
    floor_b_id = cur.lastrowid

    # Create floorplan for floor B
    cur = db_conn.execute(
        "INSERT INTO floorplans (floor_id, building, floor, image_path) VALUES (?, 'Bld FP', 'Floor B', '/test.png')",
        (floor_b_id,),
    )
    fp_b_id = cur.lastrowid

    # If we query for floor_ids = [floor_a_id], it must not return fp_b_id even if floor_a_id == fp_b_id
    matched_ids = get_floorplan_ids_for_floors_and_rooms(db_conn, [floor_a_id], [])
    assert matched_ids == [], "Floor A has no floorplans and must not match Floor B's floorplan"


def test_profile_alarm_rule_matches_inverted_thresholds():
    """
    Verify that 'between' and 'outside' operators handle inverted thresholds (min/max normalization).
    """
    # Inverted: threshold_value=30, threshold_value_2=20
    rule_between = {
        "operator": "between",
        "threshold_value": 30,
        "threshold_value_2": 20,
        "field_key": "temperature",
    }
    # 25 is between 20 and 30
    assert profile_alarm_rule_matches(rule_between, 25.0) is True
    # 15 is not between 20 and 30
    assert profile_alarm_rule_matches(rule_between, 15.0) is False

    rule_outside = {
        "operator": "outside",
        "threshold_value": 30,
        "threshold_value_2": 20,
        "field_key": "temperature",
    }
    # 35 is outside [20, 30]
    assert profile_alarm_rule_matches(rule_outside, 35.0) is True
    # 25 is inside [20, 30]
    assert profile_alarm_rule_matches(rule_outside, 25.0) is False


def test_valid_uplink_resolves_offline_history(db_conn):
    dev_id = "test-offline-recovery-node"
    envelope = {
        "end_device_ids": {
            "device_id": dev_id,
            "dev_eui": "8877665544332211",
        },
        "uplink_message": {"decoded_payload": {"temperature": 21.0}},
    }

    process_ttn_webhook_background(envelope)
    db_conn.execute(
        """
        INSERT INTO alarm_history (
            device_id, alarm_type, alarm_message, resolved
        ) VALUES (?, 'SYSTEM_OFFLINE', 'Device has not sent data in 24 hours.', 0)
        """,
        (dev_id,),
    )
    db_conn.commit()

    process_ttn_webhook_background(envelope)

    row = db_conn.execute(
        """
        SELECT resolved, resolved_reason
        FROM alarm_history
        WHERE device_id = ? AND alarm_type = 'SYSTEM_OFFLINE'
        ORDER BY id DESC LIMIT 1
        """,
        (dev_id,),
    ).fetchone()
    assert row["resolved"] == 1
    assert row["resolved_reason"] == "Device telemetry resumed"
