from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Keep verified discoveries and administrator decisions across restarts."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_enrollment_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL,
            dev_eui TEXT NOT NULL,
            join_eui TEXT NOT NULL,
            device_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            verified_key_hash TEXT NOT NULL,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            seen_count INTEGER NOT NULL DEFAULT 1,
            gateway_id TEXT,
            rssi REAL,
            snr REAL,
            decided_by TEXT,
            decided_at TEXT,
            registration_started_at REAL,
            registration_token TEXT,
            registration_error TEXT,
            online_at TEXT,
            UNIQUE (application_id, dev_eui),
            CHECK (status IN ('pending', 'registering', 'registration_failed',
                              'awaiting_join', 'online', 'rejected'))
        )
        """
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_enrollment_application_status
        ON device_enrollment_candidates(application_id, status)"""
    )
