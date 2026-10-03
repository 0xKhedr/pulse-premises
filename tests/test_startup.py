from __future__ import annotations

import sqlite3

from app.db.connection import run_migrations


def test_migrations_run_idempotent():
    # Running migrations again should be completely safe and clean
    run_migrations()
    run_migrations()


def test_database_integrity(db_conn: sqlite3.Connection):
    integrity = db_conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"


def test_database_foreign_keys(db_conn: sqlite3.Connection):
    fk_check = db_conn.execute("PRAGMA foreign_key_check").fetchall()
    assert len(fk_check) == 0, f"Foreign key violations found: {fk_check}"
