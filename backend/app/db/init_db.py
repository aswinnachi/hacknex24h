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
    source_type TEXT DEFAULT 'uploaded',
    session_id TEXT,
    frame_number INTEGER,
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

CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    camera_id TEXT,
    rule_type TEXT NOT NULL,
    object_class TEXT,
    min_confidence REAL DEFAULT 0.3,
    time_start TEXT,
    time_end TEXT,
    zone_points TEXT,
    tripwire_line TEXT,
    direction TEXT,
    cooldown_seconds REAL DEFAULT 30.0,
    severity TEXT DEFAULT 'warning',
    metadata TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    camera_name TEXT,
    trigger_timestamp REAL NOT NULL,
    object_class TEXT,
    track_id TEXT,
    confidence REAL,
    attributes TEXT,
    trigger_reason TEXT NOT NULL,
    evidence_frame TEXT,
    event_id TEXT,
    severity TEXT DEFAULT 'warning',
    status TEXT DEFAULT 'active',
    acknowledged_at REAL,
    resolved_at REAL,
    created_at REAL,
    FOREIGN KEY(rule_id) REFERENCES alert_rules(id)
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id TEXT PRIMARY KEY,
    owner_session TEXT,
    filename TEXT NOT NULL,
    file_size INTEGER,
    duration REAL,
    fps REAL,
    resolution TEXT,
    status TEXT DEFAULT 'queued',
    stage TEXT,
    progress_pct REAL DEFAULT 0.0,
    current_frame INTEGER DEFAULT 0,
    total_frames INTEGER DEFAULT 0,
    frames_analyzed INTEGER DEFAULT 0,
    unique_tracks_count INTEGER DEFAULT 0,
    total_detections_count INTEGER DEFAULT 0,
    original_video_key TEXT,
    annotated_video_key TEXT,
    json_results_key TEXT,
    csv_results_key TEXT,
    summary_json TEXT,
    error_message TEXT,
    created_at REAL,
    started_at REAL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS lab_detections (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    tracking_id INTEGER,
    track_state TEXT,
    label TEXT NOT NULL,
    confidence REAL,
    raw_confidence REAL,
    timestamp_offset REAL,
    frame_index INTEGER,
    bbox_x1 REAL,
    bbox_y1 REAL,
    bbox_x2 REAL,
    bbox_y2 REAL,
    attributes TEXT,
    evidence_frame_key TEXT,
    created_at REAL,
    FOREIGN KEY(job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE
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
            ("events", "source_type", "TEXT DEFAULT 'uploaded'"),
            ("events", "session_id", "TEXT"),
            ("events", "frame_number", "INTEGER"),
            ("cameras", "analysis_summary", "TEXT"),
            ("cameras", "source_type", "TEXT DEFAULT 'uploaded'"),
            ("cameras", "dataset_id", "TEXT"),
            ("cameras", "is_demo", "INTEGER DEFAULT 0"),
            ("cameras", "created_at", "REAL"),
            ("cameras", "status", "TEXT DEFAULT 'READY'"),
            ("cameras", "width", "INTEGER"),
            ("cameras", "height", "INTEGER"),
            ("queries", "result_summary", "TEXT"),
            ("queries", "result_count", "INTEGER")
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
