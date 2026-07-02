from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from core.paths import BIRTHDAYS_DB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_birthday_tables():
    with sqlite3.connect(BIRTHDAYS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                birthday TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS birthday_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER
            )
            """
        )
        for statement in (
            "ALTER TABLE birthdays ADD COLUMN updated_by INTEGER",
            "ALTER TABLE birthdays ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'",
            "ALTER TABLE birthdays ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            "UPDATE birthdays SET updated_by=COALESCE(updated_by, user_id), updated_at=? WHERE updated_at=''",
            (_now(),),
        )
        conn.commit()


def validate_birthday(date_str: str) -> str:
    clean = date_str.strip()
    datetime.strptime(f"{clean}.2020", "%d.%m.%Y")
    return clean


def set_birthday(user_id: int, birthday: str, *, updated_by: int | None = None, source: str = "user"):
    clean = validate_birthday(birthday)
    ensure_birthday_tables()
    with sqlite3.connect(BIRTHDAYS_DB) as conn:
        conn.execute(
            """
            INSERT INTO birthdays(user_id, birthday, updated_by, source, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                birthday=excluded.birthday,
                updated_by=excluded.updated_by,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (int(user_id), clean, int(updated_by or user_id), source[:40], _now()),
        )
        conn.commit()


def remove_birthday(user_id: int):
    ensure_birthday_tables()
    with sqlite3.connect(BIRTHDAYS_DB) as conn:
        conn.execute("DELETE FROM birthdays WHERE user_id=?", (int(user_id),))
        conn.commit()


def get_birthday(user_id: int) -> dict | None:
    ensure_birthday_tables()
    with sqlite3.connect(BIRTHDAYS_DB) as conn:
        row = conn.execute(
            "SELECT user_id, birthday, updated_by, source, updated_at FROM birthdays WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    return {
        "user_id": int(row[0]),
        "birthday": str(row[1]),
        "updated_by": int(row[2] or row[0]),
        "source": str(row[3] or "legacy"),
        "updated_at": str(row[4] or ""),
    }


def list_birthdays() -> list[dict]:
    ensure_birthday_tables()
    with sqlite3.connect(BIRTHDAYS_DB) as conn:
        rows = conn.execute(
            """
            SELECT user_id, birthday, updated_by, source, updated_at
            FROM birthdays
            ORDER BY substr(birthday, 4, 2), substr(birthday, 1, 2), user_id
            """
        ).fetchall()
    return [
        {
            "user_id": int(row[0]),
            "birthday": str(row[1]),
            "updated_by": int(row[2] or row[0]),
            "source": str(row[3] or "legacy"),
            "updated_at": str(row[4] or ""),
        }
        for row in rows
    ]
