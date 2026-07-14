from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from core.db import connection as db_connection
from core.paths import SOCIAL_DB


_INITIALIZED_DATABASES: set[str] = set()


def ensure_reputation_storage() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reputation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                given_by INTEGER NOT NULL,
                delta INTEGER NOT NULL DEFAULT 1,
                date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mood (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                mood INTEGER NOT NULL,
                date TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reputation)")}
        if "delta" not in columns:
            conn.execute("ALTER TABLE reputation ADD COLUMN delta INTEGER NOT NULL DEFAULT 1")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_reputation_user ON reputation(user_id);
            CREATE INDEX IF NOT EXISTS idx_reputation_giver_day ON reputation(given_by,date,delta);
            CREATE INDEX IF NOT EXISTS idx_mood_user_day ON mood(user_id,date);
            """
        )
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def _day(value: date | str | None = None) -> str:
    if isinstance(value, str):
        return value
    return (value or date.today()).isoformat()


def get_reputation(user_id: int) -> int:
    ensure_reputation_storage()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(delta),0) FROM reputation WHERE user_id=?", (int(user_id),)
        ).fetchone()
    return max(0, int(row[0] if row else 0))


def give_daily_reputation(user_id: int, given_by: int, day: date | str | None = None) -> bool:
    ensure_reputation_storage()
    day_value = _day(day)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM reputation WHERE given_by=? AND date=? AND delta>0 LIMIT 1",
            (int(given_by), day_value),
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "INSERT INTO reputation(user_id,given_by,delta,date) VALUES(?,?,1,?)",
            (int(user_id), int(given_by), day_value),
        )
    return True


def take_daily_reputation(user_id: int, given_by: int, day: date | str | None = None) -> str:
    """Return ok, already_used, or already_zero."""
    ensure_reputation_storage()
    day_value = _day(day)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM reputation WHERE given_by=? AND date=? AND delta<0 LIMIT 1",
            (int(given_by), day_value),
        ).fetchone()
        if exists:
            return "already_used"
        total = int(conn.execute(
            "SELECT COALESCE(SUM(delta),0) FROM reputation WHERE user_id=?", (int(user_id),)
        ).fetchone()[0])
        if total <= 0:
            return "already_zero"
        conn.execute(
            "INSERT INTO reputation(user_id,given_by,delta,date) VALUES(?,?,-1,?)",
            (int(user_id), int(given_by), day_value),
        )
    return "ok"


def add_system_reputation(user_id: int, delta: int = 1, day: date | str | None = None) -> int:
    ensure_reputation_storage()
    amount = int(delta)
    if not amount:
        return get_reputation(user_id)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            "INSERT INTO reputation(user_id,given_by,delta,date) VALUES(?,?,?,?)",
            (int(user_id), 0, amount, _day(day)),
        )
    return get_reputation(user_id)


def list_reputation_top(limit: int = 50) -> list[tuple[int, int]]:
    ensure_reputation_storage()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT user_id,SUM(delta) AS total FROM reputation
            GROUP BY user_id HAVING total>0 ORDER BY total DESC,user_id LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


def list_reputation_history(user_id: int, direction: str = "received", limit: int = 20) -> list[tuple[int, int, str]]:
    ensure_reputation_storage()
    column = "user_id" if direction == "received" else "given_by"
    other = "given_by" if direction == "received" else "user_id"
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            f"SELECT {other},delta,date FROM reputation WHERE {column}=? ORDER BY date DESC,id DESC LIMIT ?",
            (int(user_id), max(1, int(limit))),
        ).fetchall()
    return [(int(row[0]), int(row[1]), str(row[2])) for row in rows]


def save_daily_mood(user_id: int, mood: int, day: date | str | None = None) -> bool:
    ensure_reputation_storage()
    value = int(mood)
    if not 1 <= value <= 10:
        raise ValueError("mood must be between 1 and 10")
    with db_connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        day_value = _day(day)
        exists = conn.execute(
            "SELECT 1 FROM mood WHERE user_id=? AND date=? LIMIT 1",
            (int(user_id), day_value),
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "INSERT INTO mood(user_id,mood,date) VALUES(?,?,?)",
            (int(user_id), value, day_value),
        )
    return True


def list_daily_moods(day: date | str | None = None) -> list[tuple[int, int]]:
    ensure_reputation_storage()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT user_id,mood FROM mood WHERE date=? ORDER BY mood DESC,user_id",
            (_day(day),),
        ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


def inspect_reputation_storage(database: str | None = None) -> dict[str, object]:
    path = Path(database or SOCIAL_DB).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=15.0)) as conn:
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if table in present else None
            for table in ("reputation", "mood")
        }
        totals = conn.execute(
            "SELECT COUNT(DISTINCT user_id),COALESCE(SUM(delta),0) FROM reputation"
        ).fetchone() if "reputation" in present else (0, 0)
    return {
        "database": str(path),
        "counts": counts,
        "reputation_users": int(totals[0]),
        "reputation_delta_sum": int(totals[1]),
    }
