from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Let one device deviate from its profile's alarm thresholds.

    Alarm rules live on the sensor profile, so every device sharing a profile
    trips at the same numbers. That is the right default -- it is what makes a
    profile worth having -- but it has no room for the one cupboard that runs
    hot, or the sensor by a doorway whose motion rule cries wolf. The only way
    out was to clone the whole profile per device, which defeats the point.

    An override is a sparse patch over one rule, not a copy of it. Only the
    trip point and whether the rule applies here can be changed; the operator,
    the severity, the field it watches and the message it renders stay with the
    profile, so a device cannot quietly come to mean something different from
    what its profile says. NULL in any column means "inherit this one", which
    is what lets a device raise an upper bound while still following the
    profile for everything else.

    Rows are keyed by rule_code rather than rule id. Rule ids are rewritten
    whenever a profile is edited -- the update path deletes and reinserts the
    whole rule set -- so an id-keyed override would silently detach from its
    rule on the next profile save. The code is the stable name.

    Idempotent: the table and its index are only created when absent.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_alarm_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            rule_code TEXT NOT NULL,

            -- NULL means inherit this aspect from the profile rule. All three
            -- being NULL is a no-op row, which the API declines to write.
            threshold_value REAL,
            threshold_value_2 REAL,
            enabled INTEGER,

            note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT,
            updated_by TEXT,

            UNIQUE (device_id, rule_code),
            FOREIGN KEY (device_id) REFERENCES devices(device_id)
                ON DELETE CASCADE,

            -- enabled is a tri-state, not a boolean: inherit, force on, mute.
            CHECK (enabled IS NULL OR enabled IN (0, 1))
        )
        """
    )

    # Every uplink from a commissioned device looks its overrides up by
    # device_id before evaluating rules, so this is the hot path.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_device_alarm_overrides_device
        ON device_alarm_overrides(device_id)
        """
    )
