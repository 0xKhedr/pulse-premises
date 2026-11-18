import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE gateway_approval_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL UNIQUE,
            eui TEXT,
            observed_gateway_id TEXT,
            protocol TEXT NOT NULL,
            source_ip TEXT,
            reason TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','registering','registration_failed','accepted','rejected')),
            approval_token TEXT NOT NULL,
            gateway_id TEXT,
            name TEXT,
            frequency_plan_id TEXT,
            require_authenticated_connection INTEGER,
            site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
            decided_by TEXT,
            decided_at TEXT,
            registration_started_at REAL,
            registration_error TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE gateway_attempt_cursors (
            boot_id TEXT PRIMARY KEY,
            sequence INTEGER NOT NULL
        )
    """)
    conn.execute("CREATE INDEX idx_gateway_approval_status ON gateway_approval_candidates(status, last_seen_at)")
