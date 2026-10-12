"""Sample portfolio in a dedicated database, with local network readings."""
import asyncio
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.db.connection import get_db_connection


def gateway_stats(gateway_id):
    if gateway_id != "demo-gateway-1":
        return {}
    now = datetime.now(timezone.utc).isoformat()
    return {
        "connected_at": now, "last_status_received_at": now,
        "last_uplink_received_at": now, "last_downlink_received_at": now,
        "protocol": "udp", "uplink_count": 1248, "downlink_count": 32,
        "gateway_remote_address": {"ip": "192.0.2.10"},
    }


def verify_preview_database():
    with closing(get_db_connection()) as conn:
        marker = conn.execute("SELECT value FROM preview_metadata WHERE key='dataset'").fetchone()
        if not marker or marker[0] != "smart-building-preview-v1":
            raise RuntimeError("Preview mode requires the dedicated preview database")


async def preview_heartbeat():
    while True:
        with closing(get_db_connection()) as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE devices SET last_seen=? WHERE device_id='demo-device-1'", (now,))
            conn.execute("UPDATE device_latest_telemetry SET updated_at=? WHERE device_id='demo-device-1'", (now,))
            conn.execute("UPDATE gateways SET last_seen=? WHERE gateway_id='demo-gateway-1'", (now,))
            conn.commit()
        await asyncio.sleep(30)


def seed_preview(conn):
    """Seed an empty, migrated database exactly once; never reset existing data."""
    from app.main import create_password_hash, create_sensor_profile_record

    conn.execute("CREATE TABLE IF NOT EXISTS preview_metadata (key TEXT PRIMARY KEY, value TEXT)")
    if conn.execute("SELECT 1 FROM preview_metadata WHERE key='dataset'").fetchone():
        return
    if conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] or conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        raise RuntimeError("The preview seed requires a new database")
    if any(row[0] != 'Legacy Client' for row in conn.execute('SELECT name FROM clients')):
        raise RuntimeError("The preview seed requires a new database")
    # The generic discovery profile is not used by this two-profile portfolio.
    generic = conn.execute("SELECT id FROM sensor_profiles WHERE profile_code='CAYENNE_LPP_V1'").fetchone()
    if generic:
        for table in ('sensor_profile_fields', 'sensor_profile_rules', 'sensor_profile_sensors', 'sensor_profile_versions', 'profile_firmware_compatibility'):
            conn.execute(f'DELETE FROM {table} WHERE profile_id=?', (generic[0],))
        conn.execute('DELETE FROM sensor_profiles WHERE id=?', (generic[0],))
    now = datetime.now(timezone.utc)
    iso = now.isoformat()
    # The compatibility migration creates one empty legacy organization.
    client = conn.execute("SELECT id FROM clients WHERE name='Legacy Client'").fetchone()
    if client:
        conn.execute("UPDATE clients SET name='Northstar Properties' WHERE id=?", (client[0],))
    else:
        conn.execute("INSERT INTO clients(name) VALUES ('Northstar Properties')")
    conn.execute("INSERT INTO clients(name) VALUES ('Greenline Campus')")
    clients = [r[0] for r in conn.execute("SELECT id FROM clients ORDER BY id")]
    for i, (code, model, protocol) in enumerate([('DEMO_ENV', 'Climate sensor', 'i2c'), ('DEMO_CAM', 'Meter reader camera', 'digital')], 1):
        conn.execute("INSERT INTO sensor_catalog(sensor_code, manufacturer, model, use_case, protocol, description) VALUES (?, 'Preview hardware', ?, 'environment', ?, 'Sample sensor for dashboard preview')", (code, model, protocol))
        conn.execute("INSERT INTO firmware_modules(module_key, display_name, driver_class, protocol, library_name, source_file) VALUES (?, ?, ?, ?, 'Preview library', ?)", (f'demo_driver_{i}', model, f'DemoDriver{i}', protocol, f'demo_driver_{i}.cpp'))
    environment = create_sensor_profile_record(conn, {
        'profile_code': 'DEMO_CLIMATE', 'profile_name': 'Indoor climate',
        'node_type': 'environment', 'description': 'Temperature and humidity monitoring',
        'capabilities': ['environment'], 'payload_encoder_key': 'demo_climate',
        'ttn_formatter_code': 'function decodeUplink(input) { return {data: {temperature: input.bytes[0], humidity: input.bytes[1]}}; }',
        'tb_device_profile_name': 'default', 'status': 'active', 'icon_type': 'environment',
        'sensors': [{'sensor_code':'DEMO_ENV', 'module_key':'demo_driver_1'}],
        'fields': [{'field_key':'temperature', 'label':'Temperature', 'unit':'°C', 'data_type':'number'}, {'field_key':'humidity', 'label':'Humidity', 'unit':'%', 'data_type':'number'}],
        'rules': [{'rule_code':'DEMO_HIGH_TEMP', 'field_key':'temperature', 'operator':'>', 'threshold_value':28, 'severity':'warning', 'alarm_type':'temperature', 'message_template':'Temperature above 28°C'}],
    }, actor='preview')
    camera = dict(conn.execute("SELECT * FROM sensor_profiles WHERE profile_code='ESPCAM_METER_V1'").fetchone())
    sensor_id = conn.execute("SELECT id FROM sensor_catalog WHERE sensor_code='DEMO_CAM'").fetchone()[0]
    module_id = conn.execute("SELECT id FROM firmware_modules WHERE module_key='demo_driver_2'").fetchone()[0]
    conn.execute("INSERT INTO sensor_profile_sensors(profile_id, sensor_id, firmware_module_id, role) VALUES (?, ?, ?, 'primary')", (camera['id'], sensor_id, module_id))
    for i, (client_id, site_name, building, room, profile) in enumerate(zip(clients, ['Riverside Estate', 'Innovation Park'], ['North Tower', 'Innovation Center'], ['Meeting room', 'Utility room'], [environment, camera], strict=True), 1):
        site = conn.execute("SELECT id FROM sites WHERE client_id=? AND name='Legacy Site'", (client_id,)).fetchone()
        if site:
            site_id = site[0]
            conn.execute("UPDATE sites SET name=?, campus_image_path='/static/preview-campus.svg', image_width=1000, image_height=650 WHERE id=?", (site_name, site_id))
        else:
            site_id = conn.execute("INSERT INTO sites(client_id, name, campus_image_path, image_width, image_height) VALUES (?, ?, '/static/preview-campus.svg', 1000, 650)", (client_id, site_name)).lastrowid
        conn.execute("INSERT INTO site_maps(site_id, image_path, image_width, image_height) VALUES (?, '/static/preview-campus.svg', 1000, 650)", (site_id,))
        polygon = json.dumps([{'x':250,'y':180},{'x':750,'y':180},{'x':750,'y':470},{'x':250,'y':470}])
        building_id = conn.execute("INSERT INTO buildings(site_id, name, polygon_points, x, y) VALUES (?, ?, ?, 500, 325)", (site_id, building, polygon)).lastrowid
        floor_id = conn.execute("INSERT INTO floors(building_id, name, floor_number, image_path, image_width, image_height) VALUES (?, 'Ground floor', '0', '/static/preview-floor.svg', 1000, 650)", (building_id,)).lastrowid
        room_id = conn.execute("INSERT INTO rooms(floor_id, room_name, polygon_points, x, y) VALUES (?, ?, ?, 350, 300)", (floor_id, room, json.dumps([{'x':100,'y':100},{'x':600,'y':100},{'x':600,'y':500},{'x':100,'y':500}]))).lastrowid
        conn.execute("INSERT INTO floorplans(building, floor, floor_id, image_path, image_width, image_height) VALUES (?, 'Ground floor', ?, '/static/preview-floor.svg', 1000, 650)", (building, floor_id))
        last_seen = iso if i == 1 else (now - timedelta(days=2)).isoformat()
        conn.execute("INSERT INTO gateways(gateway_id, name, client_id, site_id, building_id, floor_id, status, last_seen, x, y, label, location_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 700, 300, ?, 'Sample gateway')", (f'demo-gateway-{i}', f'{site_name} gateway', client_id, site_id, building_id, floor_id, 'online' if i == 1 else 'offline', last_seen, f'Gateway {i}'))
        device = f'demo-device-{i}'
        conn.execute("INSERT INTO devices(chip_mac, device_id, dev_eui, node_type, room_id, label, status, x, y, last_seen, profile_id, profile_code, profile_version, payload_version, configuration_status, client_id, site_id, building_id, floor_id, building, floor, room, is_placed) VALUES (?, ?, ?, ?, ?, ?, ?, 350, 300, ?, ?, ?, 1, 1, 'configured', ?, ?, ?, ?, ?, 'Ground floor', ?, 1)", (f'DE:00:00:00:00:0{i}', device, f'DE0000000000000{i}', profile['node_type'], room_id, profile['profile_name'], 'online' if i == 1 else 'offline', last_seen, profile['id'], profile['profile_code'], client_id, site_id, building_id, floor_id, building, room))
        conn.execute("INSERT INTO device_capabilities(device_id, capability) VALUES (?, 'environment')", (device,))
        telemetry = {'temperature':30.4, 'humidity':48, 'battery_voltage':3.7, 'rssi':-72, 'alarm_active':True, 'alarm_message':'Temperature above 28°C'} if i == 1 else {'cam_reading':'004827', 'cam_ok':True, 'cam_running':True, 'alarm_active':False, 'alarm_message':'OK'}
        conn.execute("INSERT INTO device_latest_telemetry(device_id, telemetry, alarm_active, alarm_message, updated_at) VALUES (?, ?, ?, ?, ?)", (device, json.dumps(telemetry), int(i == 1), telemetry['alarm_message'], last_seen))
        for hour in range(24):
            reading = dict(telemetry)
            if i == 1:
                reading['temperature'] = round(22 + hour * .35, 1)
            conn.execute("INSERT INTO historical_telemetry(device_id, telemetry, timestamp) VALUES (?, ?, ?)", (device, json.dumps(reading), (now - timedelta(hours=23-hour, days=0 if i == 1 else 2)).isoformat()))
        conn.execute("INSERT INTO alarm_history(device_id, node_type, building, floor, room, alarm_type, alarm_message, telemetry, triggered_at, resolved, resolved_at, severity) VALUES (?, ?, ?, 'Ground floor', ?, ?, ?, ?, ?, ?, ?, ?)", (device, profile['node_type'], building, room, 'temperature' if i == 1 else 'battery', telemetry['alarm_message'] if i == 1 else 'Battery restored', json.dumps(telemetry), iso, int(i == 2), iso if i == 2 else None, 'warning' if i == 1 else 'info'))
        password = create_password_hash('Preview123!')
        user_id = conn.execute("INSERT INTO users(name, email, role, enabled, password_salt, password_hash, password_iterations) VALUES (?, ?, 'client', 1, ?, ?, ?)", (f'{site_name} manager', f'client{i}@preview.local', password['password_salt'], password['password_hash'], password['password_iterations'])).lastrowid
        conn.execute("INSERT INTO user_access(user_id, client_id, access_level, can_view_devices, can_view_gateways, can_view_alarms, can_view_telemetry) VALUES (?, ?, 'viewer', 1, 1, 1, 1)", (user_id, client_id))
        conn.execute("INSERT INTO alarm_recipients(email) VALUES (?)", (f'facilities{i}@example.test',))
        conn.execute("INSERT INTO audit_log(actor, action, target_type, target_id, details, message, client_id, site_id, building_id, floor_id) VALUES ('preview', 'create_gateway', 'gateway', ?, '{}', ?, ?, ?, ?, ?)", (f'demo-gateway-{i}', f'{site_name} gateway commissioned for preview', client_id, site_id, building_id, floor_id))
    conn.execute("INSERT INTO alarm_email_template(id, subject_template, body_template) VALUES (1, 'Building alert: {device_id}', 'An alarm was detected: {alarm_message}')")
    conn.execute("INSERT INTO preview_metadata VALUES ('dataset', 'smart-building-preview-v1')")
    conn.commit()
