from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # Preserve discovered inventory rows while repairing any older record that
    # claimed to be placed without having the now-required floor assignment.
    conn.execute(
        "UPDATE devices SET is_placed = 0 WHERE is_placed = 1 AND floor_id IS NULL"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS devices_placed_requires_floor_insert
        BEFORE INSERT ON devices
        WHEN NEW.is_placed = 1 AND NEW.floor_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'placed device requires floor_id');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS devices_placed_requires_floor_update
        BEFORE UPDATE OF is_placed, floor_id ON devices
        WHEN NEW.is_placed = 1 AND NEW.floor_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'placed device requires floor_id');
        END
        """
    )
