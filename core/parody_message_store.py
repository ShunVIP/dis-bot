from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from core.db import connection as db_connection
from core.paths import MESSAGES_DB


DB_PATH = MESSAGES_DB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_message_tables() -> None:
    with db_connection(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT    NOT NULL,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                message_id  INTEGER NOT NULL UNIQUE,
                content     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_um_user ON user_messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_um_created ON user_messages(created_at);

            CREATE TABLE IF NOT EXISTS collect_checkpoints (
                channel_id      INTEGER PRIMARY KEY,
                last_message_id INTEGER,
                last_collected  TEXT
            );

            CREATE TABLE IF NOT EXISTS known_users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );
            """
        )


def save_messages(rows: Iterable[tuple]) -> int:
    values = list(rows)
    if not values:
        return 0
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        cursor = conn.executemany(
            """
            INSERT OR IGNORE INTO user_messages(
                user_id, username, guild_id, channel_id, message_id, content, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return int(cursor.rowcount)


def update_checkpoint(channel_id: int, last_message_id: int) -> None:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO collect_checkpoints(channel_id, last_message_id, last_collected)
            VALUES(?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                last_message_id=excluded.last_message_id,
                last_collected=excluded.last_collected
            """,
            (int(channel_id), int(last_message_id), _now()),
        )


def get_checkpoint(channel_id: int) -> int | None:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT last_message_id FROM collect_checkpoints WHERE channel_id=?",
            (int(channel_id),),
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def reset_checkpoints() -> int:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        return int(conn.execute("DELETE FROM collect_checkpoints").rowcount)


def upsert_user(user_id: int, username: str) -> None:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO known_users(user_id, username, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                updated_at=excluded.updated_at
            """,
            (int(user_id), str(username)[:120], _now()),
        )


def get_user_messages(user_id: int) -> list[str]:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT content FROM user_messages WHERE user_id=? ORDER BY created_at ASC",
            (int(user_id),),
        ).fetchall()
    return [str(row[0]) for row in rows]


def get_user_stats(user_id: int) -> dict:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        count, first, last = conn.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM user_messages WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        row = conn.execute("SELECT username FROM known_users WHERE user_id=?", (int(user_id),)).fetchone()
    return {"count": int(count or 0), "first": first, "last": last, "username": row[0] if row else str(user_id)}


def get_all_user_ids() -> list[int]:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute("SELECT DISTINCT user_id FROM user_messages ORDER BY user_id").fetchall()
    return [int(row[0]) for row in rows]


def get_user_messages_by_year(user_id: int, year: int) -> list[str]:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT content FROM user_messages
            WHERE user_id=? AND strftime('%Y', created_at)=?
            ORDER BY created_at ASC
            """,
            (int(user_id), str(int(year))),
        ).fetchall()
    return [str(row[0]) for row in rows]


def get_user_messages_between_years(user_id: int, first_year: int, last_year: int) -> list[str]:
    start, end = sorted((int(first_year), int(last_year)))
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT content FROM user_messages
            WHERE user_id=? AND CAST(strftime('%Y', created_at) AS INTEGER) BETWEEN ? AND ?
            ORDER BY created_at ASC
            """,
            (int(user_id), start, end),
        ).fetchall()
    return [str(row[0]) for row in rows]


def get_available_years(user_id: int, minimum_messages: int = 20) -> list[int]:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT strftime('%Y', created_at) AS year, COUNT(*) AS count
            FROM user_messages WHERE user_id=?
            GROUP BY year HAVING count >= ? ORDER BY year DESC
            """,
            (int(user_id), max(1, int(minimum_messages))),
        ).fetchall()
    return [int(row[0]) for row in rows if row[0]]


def merge_user_messages(primary_id: int, secondary_id: int) -> int:
    ensure_message_tables()
    with db_connection(DB_PATH) as conn:
        exists = int(
            conn.execute("SELECT COUNT(*) FROM user_messages WHERE user_id=?", (int(secondary_id),)).fetchone()[0]
        )
        if not exists:
            return 0
        cursor = conn.execute(
            "UPDATE OR IGNORE user_messages SET user_id=? WHERE user_id=?",
            (int(primary_id), int(secondary_id)),
        )
        moved = int(cursor.rowcount)
        conn.execute("DELETE FROM user_messages WHERE user_id=?", (int(secondary_id),))
        conn.execute("DELETE FROM known_users WHERE user_id=?", (int(secondary_id),))
        return moved
