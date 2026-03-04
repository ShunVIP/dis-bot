import os, sqlite3
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_DIR = os.path.join(PROJECT_ROOT, "datebase")
os.makedirs(DB_DIR, exist_ok=True)

DB_PATH = os.path.join(DB_DIR, "wwm.db")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def ensure_schema() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            method TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT,
            url TEXT,
            payload_json TEXT NOT NULL,
            content_hash TEXT,
            fetched_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_raw_fetched_at ON raw_records(fetched_at);
        CREATE INDEX IF NOT EXISTS ix_raw_source_method ON raw_records(source, method);
        CREATE INDEX IF NOT EXISTS ix_raw_entity_type ON raw_records(entity_type);

        -- State table for incremental fetching (continue tokens, last revid, last run timestamps, etc.)
        CREATE TABLE IF NOT EXISTS source_state (
            source TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source, key)
        );
        """)
        conn.commit()
