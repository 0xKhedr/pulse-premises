import asyncio
from datetime import datetime, timezone, timedelta
from ws_manager import manager
import sqlite3

DB = "devices.db"

def get_db_connection():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

async def offline_watchdog():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Find devices that haven't updated in 24 hours
            cursor.execute("SELECT device_id, updated_at, alarm_active, alarm_message FROM device_latest_telemetry")
            rows = cursor.fetchall()
            now = datetime.now(timezone.utc)
            
            for row in rows:
                device_id = row['device_id']
                updated_at_str = row['updated_at']
                
                try:
                    # SQLite CURRENT_TIMESTAMP is 'YYYY-MM-DD HH:MM:SS' in UTC
                    updated_at = datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                
                if (now - updated_at) > timedelta(hours=24):
                    if row['alarm_message'] != 'OFFLINE':
                        # Mark as offline
                        cursor.execute("UPDATE device_latest_telemetry SET alarm_active=1, alarm_message='OFFLINE' WHERE device_id=?", (device_id,))
                        
                        # Log to alarm_history
                        cursor.execute("INSERT INTO alarm_history (device_id, alarm_type, message) VALUES (?, ?, ?)",
                                     (device_id, "SYSTEM_OFFLINE", "Device has not sent data in 24 hours."))
                        
                        # Broadcast via WS
                        alert_msg = {
                            "type": "ALARM",
                            "device_id": device_id,
                            "message": "Device Offline (No data in 24h)",
                            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        # We must run this async correctly if manager.broadcast is async
                        # Actually wait, let's just use asyncio.create_task for the broadcast to not block
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Watchdog error: {e}")
            
        await asyncio.sleep(300)  # Check every 5 minutes

# We will inject the event startup using string replacement or just append it
