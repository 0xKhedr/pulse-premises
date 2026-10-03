from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Repair devices whose is_placed flag was wrongly cleared by migration 005.

    Migration 005 correctly set is_placed = 1 for rows that had either pixel
    coordinates (x, y) or a full building_id + room_id assignment, but its very
    next UPDATE unconditionally reset is_placed = 0 for every row with no x/y,
    clobbering the building_id + room_id case it had just set. Migration 007's
    later `is_placed = CASE WHEN floor_id IS NULL THEN 0 ELSE is_placed END`
    only ever keeps or downgrades that value, so it never restored it either.

    This reapplies migration 005's original placement rule -- unchanged, not a
    new definition of "placed" -- gated by floor_id IS NOT NULL so it can never
    violate the devices_placed_requires_floor_* invariant triggers added in
    migration 006 (and stays consistent with the canonical commissioned
    definition in app/services/device_state.py, which is is_placed AND
    floor_id). Genuinely unplaced auto-discovered devices (see
    auto_provision_discovered_device in app/main.py) have no floor_id,
    building_id, room_id, or x/y, so they are left untouched.

    Idempotent: once a row is repaired, is_placed = 0 no longer holds for it,
    so re-running this migration is a no-op.
    """
    conn.execute(
        """
        UPDATE devices
        SET is_placed = 1
        WHERE is_placed = 0
          AND floor_id IS NOT NULL
          AND (
                (x IS NOT NULL AND y IS NOT NULL)
                OR (building_id IS NOT NULL AND room_id IS NOT NULL)
              )
        """
    )
