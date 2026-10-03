from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Mark sensor profile fields that the system registered by itself.

    A Cayenne payload is self-describing once decoded: the uplink arrives as
    named values, and the webhook already stores every one of them regardless
    of whether a profile declares it. What a declared field adds is
    presentation and rules -- a human label, a unit, display order, floor and
    dashboard visibility, and something for sensor_profile_rules.field_key to
    point at.

    Field learning fills that in from the first uplink that mentions a field,
    so a new kind of device is usable without anyone writing a profile by hand.
    Learned fields need to be distinguishable from authored ones: an
    administrator has to be able to see what the system guessed, correct a
    label or a unit, and have that correction stick rather than be re-guessed.
    Counting them is also what bounds the feature, since the cap on how many a
    profile may learn is what stops malformed uplinks growing a profile without
    limit.

    Idempotent: the column is only added when it is absent, so re-running this
    migration does nothing and existing fields keep auto_learned = 0, which is
    correct -- everything predating this migration was authored.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sensor_profile_fields)")}
    if "auto_learned" not in columns:
        conn.execute(
            "ALTER TABLE sensor_profile_fields "
            "ADD COLUMN auto_learned INTEGER NOT NULL DEFAULT 0"
        )

    # Learning looks up "does this profile already declare this key?" on every
    # uplink that carries an unfamiliar field, so the lookup is worth an index.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_fields_profile_key
        ON sensor_profile_fields(profile_id, field_key)
        """
    )
