import os

filepath = 'routers/api.py'

bulk_import_code = '''
# ---------------------------------------------------------
# BULK CSV IMPORT
# ---------------------------------------------------------
@router.post("/api/devices/bulk-import")
async def bulk_import_devices(file: UploadFile = File(...)):
    import csv
    import codecs
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    csvReader = csv.DictReader(codecs.iterdecode(file.file, 'utf-8'))
    success_count = 0
    errors = []
    
    for row in csvReader:
        try:
            dev_eui = row.get('dev_eui', '').strip().upper()
            app_key = row.get('app_key', '').strip().upper()
            room_id = row.get('room_id', '').strip()
            profile_id = row.get('profile_id', '').strip()
            label = row.get('label', '').strip()
            
            if not dev_eui or not room_id or not profile_id:
                errors.append(f"Row missing required fields: {row}")
                continue
                
            # If app_key is empty, generate a secure random one
            if not app_key:
                app_key = secrets.token_hex(16).upper()
                
            chip_mac = f"BULK-{dev_eui}"
            device_id = f"node-{dev_eui.lower()}"
            
            # Fetch room details
            cursor.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
            room = cursor.fetchone()
            if not room:
                errors.append(f"Room {room_id} not found for {dev_eui}")
                continue
                
            # Fetch profile
            cursor.execute("SELECT * FROM sensor_profiles WHERE id = ?", (profile_id,))
            profile = cursor.fetchone()
            if not profile:
                errors.append(f"Profile {profile_id} not found for {dev_eui}")
                continue
                
            # Insert into database
            cursor.execute("""
                INSERT OR REPLACE INTO devices 
                (chip_mac, device_id, dev_eui, app_key, join_eui, building, floor, room, label, client_id, site_id, building_id, floor_id, room_id, profile_id, profile_code, node_type, configuration_status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy')
            """, (
                chip_mac, device_id, dev_eui, app_key, "0000000000000000",
                room['building'], room['floor'], room['room_name'], label,
                room['client_id'], room['site_id'], room['floor_id'], room['floor_id'], room_id,
                profile_id, profile['profile_code'], profile['module_key']
            ))
            
            cursor.execute("""
                INSERT OR IGNORE INTO device_latest_telemetry (device_id, telemetry)
                VALUES (?, '{}')
            """, (device_id,))
            
            success_count += 1
            
        except Exception as e:
            errors.append(f"Error on {row.get('dev_eui')}: {str(e)}")
            
    conn.commit()
    conn.close()
    
    return JSONResponse(status_code=200, content={
        "status": "success",
        "imported": success_count,
        "errors": errors
    })
'''

with open(filepath, 'a', encoding='utf-8') as f:
    f.write(bulk_import_code)
    
print("Bulk import API appended.")
