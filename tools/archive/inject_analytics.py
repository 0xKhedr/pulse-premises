import os

filepath = 'routers/api.py'

analytics_code = '''
# ---------------------------------------------------------
# ANALYTICS DASHBOARD
# ---------------------------------------------------------
@router.get("/api/analytics")
def get_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Active devices
    cursor.execute("SELECT COUNT(*) as c FROM devices")
    total_devices = cursor.fetchone()['c']
    
    # Offline devices
    cursor.execute("SELECT COUNT(*) as c FROM device_latest_telemetry WHERE alarm_message = 'OFFLINE'")
    offline_devices = cursor.fetchone()['c']
    
    # Gateways
    cursor.execute("SELECT COUNT(*) as c FROM gateways")
    total_gateways = cursor.fetchone()['c']
    
    # Alarms today
    cursor.execute("SELECT COUNT(*) as c FROM alarm_history WHERE timestamp >= date('now')")
    alarms_today = cursor.fetchone()['c']
    
    conn.close()
    
    return {
        "total_devices": total_devices,
        "offline_devices": offline_devices,
        "total_gateways": total_gateways,
        "alarms_today": alarms_today
    }
'''

with open(filepath, 'a', encoding='utf-8') as f:
    f.write(analytics_code)
    
print("Analytics endpoint appended.")
