from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Backfill canonical profile and hierarchy values on older device rows."""
    conn.execute(
        """
        UPDATE devices
        SET floor_id = (
                SELECT rooms.floor_id FROM rooms WHERE rooms.id = devices.room_id
            )
        WHERE floor_id IS NULL
          AND room_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM rooms
              WHERE rooms.id = devices.room_id AND rooms.floor_id IS NOT NULL
          )
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET building_id = (
                SELECT floors.building_id FROM floors WHERE floors.id = devices.floor_id
            )
        WHERE floor_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET site_id = (
                SELECT buildings.site_id FROM buildings WHERE buildings.id = devices.building_id
            )
        WHERE building_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET client_id = (
                SELECT sites.client_id FROM sites WHERE sites.id = devices.site_id
            )
        WHERE site_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET profile_id = (
                SELECT sensor_profiles.id
                FROM sensor_profiles
                WHERE sensor_profiles.profile_code = devices.profile_code
                LIMIT 1
            )
        WHERE profile_id IS NULL AND profile_code IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET profile_code = (
                SELECT sensor_profiles.profile_code
                FROM sensor_profiles
                WHERE sensor_profiles.id = devices.profile_id
            ),
            profile_version = COALESCE(
                profile_version,
                (SELECT sensor_profiles.profile_version
                 FROM sensor_profiles WHERE sensor_profiles.id = devices.profile_id)
            ),
            payload_version = COALESCE(
                payload_version,
                (SELECT sensor_profiles.payload_version
                 FROM sensor_profiles WHERE sensor_profiles.id = devices.profile_id)
            )
        WHERE profile_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET is_placed = CASE WHEN floor_id IS NULL THEN 0 ELSE is_placed END
        """
    )
