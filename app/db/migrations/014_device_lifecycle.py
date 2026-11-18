import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE devices ADD COLUMN ttn_device_id TEXT")
    conn.execute("ALTER TABLE devices ADD COLUMN deletion_error TEXT")
    conn.execute("ALTER TABLE device_enrollment_candidates ADD COLUMN management_action TEXT")
    conn.execute("ALTER TABLE device_enrollment_candidates ADD COLUMN management_started_at REAL")
    conn.execute("ALTER TABLE device_enrollment_candidates ADD COLUMN management_token TEXT")
    conn.execute("ALTER TABLE device_enrollment_candidates ADD COLUMN ttn_checked_at TEXT")
    conn.execute("ALTER TABLE device_enrollment_candidates ADD COLUMN ttn_check_json TEXT")
    conn.execute("""
        CREATE TABLE device_admission_blocks (
            application_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            dev_eui TEXT,
            state TEXT NOT NULL DEFAULT 'deleting',
            actor TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(application_id, device_id)
        )
    """)
    conn.execute("CREATE INDEX idx_device_blocks_eui ON device_admission_blocks(application_id, dev_eui)")
