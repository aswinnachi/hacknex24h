import os
from backend.app.db.database import get_db_connection
from backend.app.core.logging import logger
from backend.app.core.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    video_path TEXT,
    fps REAL,
    duration REAL,
    resolution TEXT,
    width INTEGER,
    height INTEGER,
    status TEXT DEFAULT 'READY',
    analysis_summary TEXT,
    source_type TEXT DEFAULT 'uploaded',
    dataset_id TEXT,
    is_demo INTEGER DEFAULT 0,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    dataset_id TEXT,
    tracking_id TEXT,
    concept TEXT NOT NULL,
    start_timestamp REAL NOT NULL,
    end_timestamp REAL NOT NULL,
    confidence REAL,
    bounding_boxes TEXT,
    representative_frame TEXT,
    clip_path TEXT,
    embedding_ref TEXT,
    attributes TEXT,
    FOREIGN KEY(camera_id) REFERENCES cameras(id)
);

CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    key_name TEXT NOT NULL,
    camera_id TEXT,
    region TEXT,
    metadata TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS queries (
    id TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    timestamp REAL NOT NULL,
    latency REAL
);
"""

def init_db():
    db_path = settings.database_url.replace("sqlite:///", "")
    db_dir = os.path.dirname(db_path)
    if not os.path.exists(db_dir) and db_dir:
        os.makedirs(db_dir, exist_ok=True)
    
    logger.info(f"Initializing database at {db_path}")
    with get_db_connection() as conn:
        conn.executescript(SCHEMA)
        # Safe migration for existing databases
        columns_to_add = [
            ("events", "attributes", "TEXT"),
            ("events", "dataset_id", "TEXT"),
            ("cameras", "analysis_summary", "TEXT"),
            ("cameras", "source_type", "TEXT DEFAULT 'uploaded'"),
            ("cameras", "dataset_id", "TEXT"),
            ("cameras", "is_demo", "INTEGER DEFAULT 0"),
            ("cameras", "created_at", "REAL"),
            ("cameras", "status", "TEXT DEFAULT 'READY'"),
            ("cameras", "width", "INTEGER"),
            ("cameras", "height", "INTEGER")
        ]
        for table, col, col_type in columns_to_add:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except Exception:
                pass
        conn.commit()
    logger.info("Database initialized successfully with clean empty tables.")

if __name__ == "__main__":
    init_db()
