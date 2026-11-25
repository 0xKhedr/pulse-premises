import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE devices ADD COLUMN map_icon TEXT")
    conn.execute("ALTER TABLE devices ADD COLUMN label_locked INTEGER NOT NULL DEFAULT 0")
