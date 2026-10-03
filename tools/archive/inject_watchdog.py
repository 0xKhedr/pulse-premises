import os

filepath = 'maintestfinal2.py'

watchdog_code = '''
# ---------------------------------------------------------
# OFFLINE WATCHDOG
# ---------------------------------------------------------
async def offline_watchdog():
    while True:
        try:
            conn = sqlite3.connect(DB, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT device_id, updated_at, alarm_active, alarm_message FROM device_latest_telemetry")
            rows = cursor.fetchall()
            now = datetime.now(timezone.utc)
            
            for row in rows:
                device_id = row['device_id']
                updated_at_str = row['updated_at']
                
                try:
                    # SQLite CURRENT_TIMESTAMP is 'YYYY-MM-DD HH:MM:SS'
                    # If it has milliseconds, we need to handle that, but typically it doesn't.
                    if '.' in updated_at_str:
                        updated_at_str = updated_at_str.split('.')[0]
                    updated_at = datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception as e:
                    continue
                
                if (now - updated_at) > timedelta(hours=24):
                    if row['alarm_message'] != 'OFFLINE':
                        cursor.execute("UPDATE device_latest_telemetry SET alarm_active=1, alarm_message='OFFLINE' WHERE device_id=?", (device_id,))
                        cursor.execute("INSERT INTO alarm_history (device_id, alarm_type, message) VALUES (?, ?, ?)",
                                     (device_id, "SYSTEM_OFFLINE", "Device has not sent data in 24 hours."))
                        
                        alert_msg = {
                            "type": "ALARM",
                            "device_id": device_id,
                            "message": "Device Offline (No data in 24h)",
                            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        asyncio.create_task(manager.broadcast(json.dumps(alert_msg)))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Watchdog error: {e}")
            
        await asyncio.sleep(300)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(offline_watchdog())
'''

with open(filepath, 'a', encoding='utf-8') as f:
    f.write(watchdog_code)
    
print("Watchdog appended.")
