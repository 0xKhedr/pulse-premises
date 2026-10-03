import sqlite3
conn = sqlite3.connect('devices.db')
c = conn.cursor()
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='device_latest_telemetry';")
print("--- device_latest_telemetry ---")
print(c.fetchone()[0])
