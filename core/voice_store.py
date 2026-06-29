from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB


DEFAULT_ROOMS = (
    ("general", "Общий", 10),
    ("games", "Игры", 20),
    ("afk", "AFK", 90),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_voice_tables():
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS voice_rooms (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL DEFAULT 0,
                slug        TEXT NOT NULL,
                name        TEXT NOT NULL,
                position    INTEGER NOT NULL DEFAULT 0,
                is_private  INTEGER NOT NULL DEFAULT 0,
                created_by  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(guild_id, slug)
            );
            """
        )
        conn.commit()


def ensure_default_voice_rooms(guild_id: int = 0):
    ensure_voice_tables()
    now = _now()
    with sqlite3.connect(SOCIAL_DB) as conn:
        for slug, name, position in DEFAULT_ROOMS:
            conn.execute(
                """
                INSERT OR IGNORE INTO voice_rooms(
                    guild_id, slug, name, position, is_private, created_by, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (int(guild_id), slug, name, int(position), now, now),
            )
        conn.commit()


def list_voice_rooms(guild_id: int = 0) -> list[dict[str, Any]]:
    ensure_default_voice_rooms(guild_id)
    with sqlite3.connect(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, guild_id, slug, name, position, is_private, created_by, created_at, updated_at
            FROM voice_rooms
            WHERE guild_id=?
            ORDER BY position ASC, id ASC
            """,
            (int(guild_id),),
        ).fetchall()
    return [
        {
            "id": row[0],
            "guild_id": row[1],
            "slug": row[2],
            "name": row[3],
            "position": row[4],
            "is_private": bool(row[5]),
            "created_by": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }
        for row in rows
    ]


def get_voice_room(room_id: int, guild_id: int = 0) -> dict[str, Any] | None:
    ensure_default_voice_rooms(guild_id)
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT id, guild_id, slug, name, position, is_private, created_by, created_at, updated_at
            FROM voice_rooms
            WHERE id=? AND guild_id=?
            """,
            (int(room_id), int(guild_id)),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "guild_id": row[1],
        "slug": row[2],
        "name": row[3],
        "position": row[4],
        "is_private": bool(row[5]),
        "created_by": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }


def create_voice_room(guild_id: int, name: str, created_by: int = 0, is_private: bool = False) -> dict[str, Any]:
    ensure_voice_tables()
    clean_name = name.strip()[:80] or "Новая комната"
    base_slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in clean_name).strip("-") or "room"
    now = _now()
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM voice_rooms WHERE guild_id=?",
            (int(guild_id),),
        ).fetchone()
        position = int(row[0] or 0) + 10
        slug = base_slug
        suffix = 2
        while conn.execute(
            "SELECT 1 FROM voice_rooms WHERE guild_id=? AND slug=?",
            (int(guild_id), slug),
        ).fetchone():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        cur = conn.execute(
            """
            INSERT INTO voice_rooms(guild_id, slug, name, position, is_private, created_by, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(guild_id), slug, clean_name, position, int(is_private), int(created_by), now, now),
        )
        conn.commit()
        room_id = cur.lastrowid
    room = get_voice_room(room_id, guild_id)
    assert room is not None
    return room
