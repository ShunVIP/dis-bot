# core/web_app_store.py
from __future__ import annotations

import json
import secrets
import sqlite3
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from core.paths import SOCIAL_DB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_web_tables():
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS web_users (
                discord_user_id INTEGER PRIMARY KEY,
                username        TEXT NOT NULL DEFAULT '',
                global_name     TEXT NOT NULL DEFAULT '',
                avatar          TEXT NOT NULL DEFAULT '',
                access_token    TEXT NOT NULL DEFAULT '',
                refresh_token   TEXT NOT NULL DEFAULT '',
                token_expires_at TEXT NOT NULL DEFAULT '',
                connections_json TEXT NOT NULL DEFAULT '[]',
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_login_profiles (
                discord_user_id INTEGER PRIMARY KEY,
                email           TEXT NOT NULL DEFAULT '',
                login_name      TEXT NOT NULL DEFAULT '',
                password_hash   TEXT NOT NULL DEFAULT '',
                password_salt   TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_web_login_email
                ON web_login_profiles(email)
                WHERE email != '';

            CREATE TABLE IF NOT EXISTS web_sessions (
                session_id      TEXT PRIMARY KEY,
                discord_user_id INTEGER NOT NULL,
                expires_at      TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_chat_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL DEFAULT 0,
                channel_id      INTEGER NOT NULL DEFAULT 0,
                discord_user_id INTEGER NOT NULL,
                author_name     TEXT NOT NULL DEFAULT '',
                content         TEXT NOT NULL,
                attachment_json TEXT NOT NULL DEFAULT '[]',
                source          TEXT NOT NULL DEFAULT 'web',
                status          TEXT NOT NULL DEFAULT 'stored',
                edited_at       TEXT NOT NULL DEFAULT '',
                deleted_at      TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_chat_reactions (
                message_id      INTEGER NOT NULL,
                emoji           TEXT NOT NULL,
                discord_user_id INTEGER NOT NULL,
                created_at      TEXT NOT NULL,
                PRIMARY KEY(message_id, emoji, discord_user_id)
            );

            CREATE TABLE IF NOT EXISTS web_bot_outbox (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                channel_id      INTEGER NOT NULL,
                discord_user_id INTEGER NOT NULL,
                author_name     TEXT NOT NULL DEFAULT '',
                content         TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                error           TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                sent_at         TEXT NOT NULL DEFAULT ''
            );
            """
        )
        _ensure_column(conn, "web_chat_messages", "attachment_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "web_chat_messages", "edited_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "web_chat_messages", "deleted_at", "TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value.encode("utf-8"),
        180_000,
    ).hex()
    return digest, salt_value


def upsert_web_user(
    discord_user_id: int,
    username: str,
    global_name: str = "",
    avatar: str = "",
    access_token: str = "",
    refresh_token: str = "",
    token_expires_at: str = "",
    connections: list[dict[str, Any]] | None = None,
):
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO web_users(
                discord_user_id, username, global_name, avatar,
                access_token, refresh_token, token_expires_at,
                connections_json, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                username=excluded.username,
                global_name=excluded.global_name,
                avatar=excluded.avatar,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                token_expires_at=excluded.token_expires_at,
                connections_json=excluded.connections_json,
                updated_at=excluded.updated_at
            """,
            (
                int(discord_user_id),
                username,
                global_name,
                avatar,
                access_token,
                refresh_token,
                token_expires_at,
                json.dumps(connections or [], ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()


def upsert_login_profile(
    discord_user_id: int,
    email: str = "",
    login_name: str = "",
    password: str | None = None,
):
    ensure_web_tables()
    clean_email = email.strip().lower()
    clean_login = login_name.strip()[:80]
    password_hash = ""
    password_salt = ""
    if password:
        password_hash, password_salt = _hash_password(password)
    with sqlite3.connect(SOCIAL_DB) as conn:
        existing = conn.execute(
            "SELECT email, login_name, password_hash, password_salt FROM web_login_profiles WHERE discord_user_id=?",
            (int(discord_user_id),),
        ).fetchone()
        if existing:
            clean_email = clean_email or existing[0]
            clean_login = clean_login or existing[1]
            password_hash = password_hash or existing[2]
            password_salt = password_salt or existing[3]
        conn.execute(
            """
            INSERT INTO web_login_profiles(discord_user_id, email, login_name, password_hash, password_salt, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                email=excluded.email,
                login_name=excluded.login_name,
                password_hash=excluded.password_hash,
                password_salt=excluded.password_salt,
                updated_at=excluded.updated_at
            """,
            (int(discord_user_id), clean_email, clean_login, password_hash, password_salt, _now()),
        )
        conn.commit()


def get_login_profile(discord_user_id: int) -> dict[str, Any] | None:
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT discord_user_id, email, login_name, password_hash, updated_at
            FROM web_login_profiles
            WHERE discord_user_id=?
            """,
            (int(discord_user_id),),
        ).fetchone()
    if not row:
        return None
    return {
        "discord_user_id": int(row[0]),
        "email": row[1],
        "login_name": row[2],
        "has_password": bool(row[3]),
        "updated_at": row[4],
    }


def authenticate_local_user(email: str, password: str) -> dict[str, Any] | None:
    ensure_web_tables()
    clean_email = email.strip().lower()
    if not clean_email or not password:
        return None
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT discord_user_id, password_hash, password_salt
            FROM web_login_profiles
            WHERE email=?
            """,
            (clean_email,),
        ).fetchone()
    if not row or not row[1] or not row[2]:
        return None
    digest, _salt = _hash_password(password, row[2])
    if not hmac.compare_digest(digest, row[1]):
        return None
    return get_web_user(int(row[0]))


def get_web_user(discord_user_id: int) -> dict[str, Any] | None:
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT discord_user_id, username, global_name, avatar,
                   connections_json, updated_at
            FROM web_users WHERE discord_user_id=?
            """,
            (int(discord_user_id),),
        ).fetchone()
    if not row:
        return None
    try:
        connections = json.loads(row[4] or "[]")
    except Exception:
        connections = []
    login_profile = get_login_profile(int(row[0]))
    return {
        "id": int(row[0]),
        "username": row[1],
        "global_name": row[2],
        "avatar": row[3],
        "connections": connections,
        "login_profile": login_profile,
        "updated_at": row[5],
    }


def create_session(discord_user_id: int, ttl_days: int = 14) -> str:
    ensure_web_tables()
    session_id = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.execute(
            "INSERT INTO web_sessions(session_id, discord_user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
            (session_id, int(discord_user_id), expires_at, _now()),
        )
        conn.commit()
    return session_id


def get_session_user(session_id: str) -> dict[str, Any] | None:
    ensure_web_tables()
    if not session_id:
        return None
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT discord_user_id, expires_at FROM web_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row[1]) < datetime.now(timezone.utc):
            return None
    except Exception:
        return None
    return get_web_user(int(row[0]))


def delete_session(session_id: str):
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.execute("DELETE FROM web_sessions WHERE session_id=?", (session_id,))
        conn.commit()


def add_chat_message(
    discord_user_id: int,
    author_name: str,
    content: str,
    guild_id: int = 0,
    channel_id: int = 0,
    source: str = "web",
    attachments: list[dict[str, Any]] | None = None,
) -> int:
    ensure_web_tables()
    text = content.strip()[:1800]
    clean_attachments = _clean_attachments(attachments or [])
    if not text and not clean_attachments:
        raise ValueError("empty message")
    with sqlite3.connect(SOCIAL_DB) as conn:
        cur = conn.execute(
            """
            INSERT INTO web_chat_messages(
                guild_id, channel_id, discord_user_id, author_name,
                content, attachment_json, source, status, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, 'stored', ?)
            """,
            (
                int(guild_id),
                int(channel_id),
                int(discord_user_id),
                author_name,
                text,
                json.dumps(clean_attachments, ensure_ascii=False),
                source,
                _now(),
            ),
        )
        message_id = int(cur.lastrowid)
        if source == "web" and guild_id and channel_id:
            conn.execute(
                """
                INSERT INTO web_bot_outbox(
                    guild_id, channel_id, discord_user_id, author_name,
                    content, status, created_at
                )
                VALUES(?, ?, ?, ?, ?, 'pending', ?)
                """,
                (int(guild_id), int(channel_id), int(discord_user_id), author_name, text, _now()),
            )
        conn.commit()
    return message_id


def _clean_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        clean.append({
            "url": url[:500],
            "name": str(item.get("name") or "file")[:180],
            "content_type": str(item.get("content_type") or "application/octet-stream")[:120],
            "size": int(item.get("size") or 0),
        })
    return clean


def list_chat_messages(limit: int = 50) -> list[dict[str, Any]]:
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, guild_id, channel_id, discord_user_id, author_name,
                   content, attachment_json, source, status, edited_at, deleted_at, created_at
            FROM web_chat_messages
            ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        message_ids = [int(row[0]) for row in rows]
        reactions = _reaction_summary(conn, "web_chat_reactions", message_ids)
    return [
        {
            "id": row[0],
            "guild_id": row[1],
            "channel_id": row[2],
            "discord_user_id": row[3],
            "author_name": row[4],
            "content": row[5],
            "attachments": _parse_attachments(row[6]),
            "source": row[7],
            "status": row[8],
            "edited_at": row[9],
            "deleted_at": row[10],
            "created_at": row[11],
            "reactions": reactions.get(int(row[0]), []),
        }
        for row in reversed(rows)
    ]


def _parse_attachments(value: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(value or "[]")
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _reaction_summary(conn: sqlite3.Connection, table: str, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" for _ in message_ids)
    rows = conn.execute(
        f"""
        SELECT message_id, emoji, COUNT(*) AS count
        FROM {table}
        WHERE message_id IN ({placeholders})
        GROUP BY message_id, emoji
        ORDER BY message_id ASC, emoji ASC
        """,
        message_ids,
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {}
    for message_id, emoji, count in rows:
        result.setdefault(int(message_id), []).append({"emoji": emoji, "count": int(count)})
    return result


def edit_chat_message(message_id: int, discord_user_id: int, content: str, can_admin: bool = False) -> bool:
    ensure_web_tables()
    text = content.strip()[:1800]
    if not text:
        raise ValueError("empty message")
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT discord_user_id, deleted_at FROM web_chat_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
        if not row or row[1]:
            return False
        if int(row[0]) != int(discord_user_id) and not can_admin:
            return False
        conn.execute(
            "UPDATE web_chat_messages SET content=?, edited_at=? WHERE id=?",
            (text, _now(), int(message_id)),
        )
        conn.commit()
        return True


def delete_chat_message(message_id: int, discord_user_id: int, can_admin: bool = False) -> bool:
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT discord_user_id, deleted_at FROM web_chat_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
        if not row or row[1]:
            return False
        if int(row[0]) != int(discord_user_id) and not can_admin:
            return False
        conn.execute(
            "UPDATE web_chat_messages SET content='', deleted_at=? WHERE id=?",
            (_now(), int(message_id)),
        )
        conn.commit()
        return True


def toggle_chat_reaction(message_id: int, discord_user_id: int, emoji: str) -> bool:
    ensure_web_tables()
    clean = emoji.strip()[:24] or "+"
    with sqlite3.connect(SOCIAL_DB) as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM web_chat_reactions
            WHERE message_id=? AND emoji=? AND discord_user_id=?
            """,
            (int(message_id), clean, int(discord_user_id)),
        ).fetchone()
        if exists:
            conn.execute(
                """
                DELETE FROM web_chat_reactions
                WHERE message_id=? AND emoji=? AND discord_user_id=?
                """,
                (int(message_id), clean, int(discord_user_id)),
            )
            active = False
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO web_chat_reactions(message_id, emoji, discord_user_id, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (int(message_id), clean, int(discord_user_id), _now()),
            )
            active = True
        conn.commit()
        return active


def claim_pending_outbox(limit: int = 20) -> list[dict[str, Any]]:
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, guild_id, channel_id, discord_user_id, author_name, content
            FROM web_bot_outbox
            WHERE status='pending'
            ORDER BY id ASC LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        ids = [int(row[0]) for row in rows]
        if ids:
            conn.executemany(
                "UPDATE web_bot_outbox SET status='sending' WHERE id=?",
                [(item_id,) for item_id in ids],
            )
        conn.commit()
    return [
        {
            "id": row[0],
            "guild_id": row[1],
            "channel_id": row[2],
            "discord_user_id": row[3],
            "author_name": row[4],
            "content": row[5],
        }
        for row in rows
    ]


def mark_outbox_sent(item_id: int):
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.execute(
            "UPDATE web_bot_outbox SET status='sent', sent_at=?, error='' WHERE id=?",
            (_now(), int(item_id)),
        )
        conn.commit()


def mark_outbox_failed(item_id: int, error: str):
    ensure_web_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.execute(
            "UPDATE web_bot_outbox SET status='pending', error=? WHERE id=?",
            (error[:300], int(item_id)),
        )
        conn.commit()
