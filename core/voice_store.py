from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db import connection
from core.paths import SOCIAL_DB


DEFAULT_ROOMS = (
    ("general", "Общий", 10),
    ("games", "Игры", 20),
    ("afk", "AFK", 90),
)
MAX_CUSTOM_ROOMS = 30
INVITE_TTL_HOURS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _room_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0], "guild_id": row[1], "slug": row[2], "name": row[3],
        "position": row[4], "is_private": bool(row[5]), "created_by": row[6],
        "created_at": row[7], "updated_at": row[8],
    }


def _invite_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_voice_tables() -> None:
    with connection(SOCIAL_DB) as conn:
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

            CREATE TABLE IF NOT EXISTS voice_room_members (
                room_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                added_at TEXT NOT NULL,
                PRIMARY KEY(room_id, user_id),
                FOREIGN KEY(room_id) REFERENCES voice_rooms(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS voice_room_invites (
                token_hash TEXT PRIMARY KEY,
                room_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 1,
                uses INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(room_id) REFERENCES voice_rooms(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_voice_members_user ON voice_room_members(user_id, room_id);
            CREATE INDEX IF NOT EXISTS idx_voice_invites_room ON voice_room_invites(room_id, expires_at);
            """
        )


def ensure_default_voice_rooms(guild_id: int = 0) -> None:
    ensure_voice_tables()
    now = _now()
    with connection(SOCIAL_DB) as conn:
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


def list_voice_rooms(
    guild_id: int = 0,
    *,
    user_id: int = 0,
    include_private: bool = False,
) -> list[dict[str, Any]]:
    ensure_default_voice_rooms(guild_id)
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT r.id, r.guild_id, r.slug, r.name, r.position, r.is_private,
                            r.created_by, r.created_at, r.updated_at
            FROM voice_rooms AS r
            LEFT JOIN voice_room_members AS m ON m.room_id=r.id AND m.user_id=?
            WHERE r.guild_id=? AND (r.is_private=0 OR m.user_id IS NOT NULL OR ?=1)
            ORDER BY r.position ASC, r.id ASC
            """,
            (int(user_id), int(guild_id), int(include_private)),
        ).fetchall()
    return [_room_dict(row) for row in rows]


def get_voice_room(room_id: int, guild_id: int = 0) -> dict[str, Any] | None:
    ensure_default_voice_rooms(guild_id)
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT id, guild_id, slug, name, position, is_private, created_by, created_at, updated_at
            FROM voice_rooms
            WHERE id=? AND guild_id=?
            """,
            (int(room_id), int(guild_id)),
        ).fetchone()
    return _room_dict(row) if row else None


def can_access_voice_room(room_id: int, user_id: int, *, is_admin: bool = False) -> bool:
    ensure_voice_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT r.is_private, r.created_by, m.user_id
            FROM voice_rooms AS r
            LEFT JOIN voice_room_members AS m ON m.room_id=r.id AND m.user_id=?
            WHERE r.id=?
            """,
            (int(user_id), int(room_id)),
        ).fetchone()
    if not row:
        return False
    return not bool(row[0]) or int(row[1]) == int(user_id) or row[2] is not None or bool(is_admin)


def create_voice_room(guild_id: int, name: str, created_by: int = 0, is_private: bool = False) -> dict[str, Any]:
    ensure_default_voice_rooms(guild_id)
    clean_name = " ".join(name.split()).strip()[:80]
    if not clean_name:
        raise ValueError("name_required")
    if any(ord(ch) < 32 for ch in clean_name):
        raise ValueError("invalid_name")
    base_slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in clean_name).strip("-") or "room"
    now = _now()
    with connection(SOCIAL_DB) as conn:
        custom_count = conn.execute(
            "SELECT COUNT(*) FROM voice_rooms WHERE guild_id=? AND created_by<>0",
            (int(guild_id),),
        ).fetchone()[0]
        if int(custom_count) >= MAX_CUSTOM_ROOMS:
            raise ValueError("room_limit_reached")
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
        room_id = int(cur.lastrowid)
        if is_private:
            conn.execute(
                "INSERT INTO voice_room_members(room_id, user_id, role, added_at) VALUES(?, ?, 'owner', ?)",
                (room_id, int(created_by), now),
            )
    room = get_voice_room(room_id, guild_id)
    assert room is not None
    return room


def create_voice_invite(
    room_id: int,
    created_by: int,
    *,
    max_uses: int = 1,
    is_admin: bool = False,
) -> str:
    ensure_voice_tables()
    if max_uses < 1 or max_uses > 25:
        raise ValueError("invalid_max_uses")
    if not can_access_voice_room(room_id, created_by, is_admin=is_admin):
        raise PermissionError("room_access_denied")
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO voice_room_invites(
                token_hash, room_id, created_by, created_at, expires_at, max_uses, uses
            ) VALUES(?, ?, ?, ?, ?, ?, 0)
            """,
            (
                _invite_hash(token), int(room_id), int(created_by), now.isoformat(),
                (now + timedelta(hours=INVITE_TTL_HOURS)).isoformat(), int(max_uses),
            ),
        )
    return token


def redeem_voice_invite(room_id: int, user_id: int, token: str) -> bool:
    ensure_voice_tables()
    if not token or len(token) > 200:
        return False
    now = _now()
    token_hash = _invite_hash(token)
    with connection(SOCIAL_DB) as conn:
        claimed = conn.execute(
            """
            UPDATE voice_room_invites
            SET uses=uses+1
            WHERE token_hash=? AND room_id=? AND expires_at>? AND uses<max_uses
            """,
            (token_hash, int(room_id), now),
        )
        if claimed.rowcount != 1:
            return False
        conn.execute(
            """
            INSERT INTO voice_room_members(room_id, user_id, role, added_at)
            VALUES(?, ?, 'member', ?)
            ON CONFLICT(room_id, user_id) DO NOTHING
            """,
            (int(room_id), int(user_id), now),
        )
    return True
