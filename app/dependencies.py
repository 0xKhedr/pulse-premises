import sqlite3
from collections.abc import Generator

from app.db.connection import get_db_connection


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    FastAPI dependency that yields a database connection.
    Ensures the connection is closed after the request is complete.
    """
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
