from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import glob
import importlib.util
import os
import sqlite3
from pathlib import Path

from app.config import settings


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def run_migrations() -> None:
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }

        migration_dir = Path(__file__).with_name("migrations")
        for filename in sorted(glob.glob(str(migration_dir / "[0-9]*.py"))):
            version = os.path.basename(filename).removesuffix(".py")
            if version in applied:
                continue

            spec = importlib.util.spec_from_file_location(
                f"sbms_migration_{version}", filename
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load migration {filename}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            up = getattr(module, "up", None)
            if not callable(up):
                raise RuntimeError(f"Migration {version} has no up(conn)")

            try:
                up(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)",
                    (version,),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migrations()
    logger.info("Database migrations completed successfully.")
