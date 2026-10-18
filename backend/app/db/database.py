import sqlite3
from typing import Optional
from backend.app.core.config import settings
from backend.app.core.logging import logger

def get_db_connection() -> sqlite3.Connection:
    # Remove 'sqlite:///' prefix for sqlite3 module
    db_path = settings.database_url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def execute_query(query: str, params: tuple = (), fetch: bool = False, fetch_one: bool = False) -> Optional[list]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch:
                if fetch_one:
                    return cursor.fetchone()
                return cursor.fetchall()
            conn.commit()
    except Exception as e:
        logger.error(f"Database error executing query: {query}. Error: {e}")
        raise e
    return None
