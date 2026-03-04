import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "datebase", "wwm.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def ensure_postprocess_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS refresh_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,          -- running/ok/error
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS entities (
        entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_title TEXT NOT NULL,
        canonical_key TEXT NOT NULL UNIQUE,   -- нормализованный ключ для поиска
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS entity_sources (
        entity_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        method TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        external_id TEXT NOT NULL,
        title TEXT,
        url TEXT,
        content_hash TEXT,
        fetched_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY(entity_id, source, method, entity_type, external_id, fetched_at),
        FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
    );

    CREATE TABLE IF NOT EXISTS aliases (
        entity_id INTEGER NOT NULL,
        alias TEXT NOT NULL,
        alias_key TEXT NOT NULL,
        PRIMARY KEY(entity_id, alias_key),
        FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
    );

    CREATE INDEX IF NOT EXISTS ix_entity_sources_source ON entity_sources(source);
    CREATE INDEX IF NOT EXISTS ix_entity_sources_type ON entity_sources(entity_type);
    CREATE INDEX IF NOT EXISTS ix_aliases_key ON aliases(alias_key);
    """)
    conn.commit()

def normalize_key(s: str) -> str:
    s = (s or "").strip().lower()
    # выкидываем всё кроме букв/цифр/пробелов, схлопываем пробелы
    s = re.sub(r"[^a-z0-9а-яё\s]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def best_title(title: Optional[str], url: Optional[str]) -> str:
    t = (title or "").strip()
    if t:
        return t
    # если title пустой (у тебя так в Game8 сейчас), берём хвост URL
    if url:
        return url.rstrip("/").split("/")[-1]
    return "unknown"
GAME8_SUFFIX_RE = re.compile(r"\s*\|\s*Where\s+Winds\s+Meet\s*\|\s*Game8\s*$", re.IGNORECASE)
GAME8_SUFFIX_RE2 = re.compile(r"\s*\|\s*Game8\s*$", re.IGNORECASE)

def clean_canonical_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "unknown"

    t = GAME8_SUFFIX_RE.sub("", t).strip()
    t = GAME8_SUFFIX_RE2.sub("", t).strip()

    if "game8" in t.lower() and "|" in t:
        t = t.split("|", 1)[0].strip()

    return t or "unknown"
def upsert_entity(conn: sqlite3.Connection, canonical_title: str) -> int:
    key = normalize_key(canonical_title)
    cur = conn.cursor()
    cur.execute("SELECT entity_id FROM entities WHERE canonical_key=?", (key,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO entities(canonical_title, canonical_key, created_at) VALUES(?, ?, ?)",
        (canonical_title, key, now_iso())
    )
    return cur.lastrowid

def upsert_alias(conn: sqlite3.Connection, entity_id: int, alias: str) -> None:
    alias = (alias or "").strip()
    if not alias:
        return
    k = normalize_key(alias)
    if not k:
        return
    conn.execute(
        "INSERT OR IGNORE INTO aliases(entity_id, alias, alias_key) VALUES(?, ?, ?)",
        (entity_id, alias, k)
    )

def postprocess_v1(run_id: str = "manual") -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_postprocess_schema(conn)

        # фиксируем запуск
        conn.execute(
            "INSERT OR REPLACE INTO refresh_runs(run_id, started_at, status, notes) VALUES(?, ?, ?, ?)",
            (run_id, now_iso(), "running", "postprocess_v1")
        )
        conn.commit()

        cur = conn.cursor()
        cur.execute("""
            SELECT source, method, entity_type, external_id, title, url, payload_json, content_hash, fetched_at
            FROM raw_records
            ORDER BY fetched_at ASC
        """)

        count = 0
        for r in cur.fetchall():
            title = best_title(r["title"], r["url"])
            title = clean_canonical_title(title)
            entity_id = upsert_entity(conn, title)

            # source snapshot
            conn.execute("""
                INSERT OR IGNORE INTO entity_sources(
                    entity_id, source, method, entity_type, external_id, title, url,
                    content_hash, fetched_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entity_id, r["source"], r["method"], r["entity_type"], r["external_id"],
                r["title"], r["url"], r["content_hash"], r["fetched_at"], r["payload_json"]
            ))

            # aliases
            upsert_alias(conn, entity_id, title)  # очищенный canonical
            if r["title"]:
                upsert_alias(conn, entity_id, clean_canonical_title(r["title"]))

            count += 1
            if count % 200 == 0:
                conn.commit()

        conn.commit()
        conn.execute(
            "UPDATE refresh_runs SET finished_at=?, status=? WHERE run_id=?",
            (now_iso(), "ok", run_id)
        )
        conn.commit()

        print(f"postprocess_v1 done. raw processed: {count}")

    except Exception as e:
        conn.execute(
            "UPDATE refresh_runs SET finished_at=?, status=?, notes=? WHERE run_id=?",
            (now_iso(), "error", str(e), run_id)
        )
        conn.commit()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    postprocess_v1(run_id="manual_postprocess_v1")
