from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Make the legacy devices.device_id column a valid foreign-key target.

    Fresh databases define ``device_id`` as UNIQUE in migration 001. Older
    databases predate that definition, and migration 011's alarm-override
    foreign key consequently makes all later device writes fail with
    ``foreign key mismatch``. Device IDs are already treated as canonical
    identifiers everywhere in the application, so fail clearly if legacy
    duplicates exist and otherwise add the missing unique index.
    """
    duplicate = conn.execute(
        """
        SELECT device_id, COUNT(*) AS count
        FROM devices
        WHERE device_id IS NOT NULL AND trim(device_id) <> ''
        GROUP BY device_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicate:
        raise RuntimeError(
            "Cannot enforce unique devices.device_id; duplicate value found: "
            f"{duplicate['device_id']}"
        )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_devices_device_id
        ON devices(device_id)
        """
    )
