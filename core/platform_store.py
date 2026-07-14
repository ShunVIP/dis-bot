from __future__ import annotations

import sqlite3
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB
from core.db import connection as db_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_platform_tables():
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS platform_servers (
                id          INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                icon        TEXT NOT NULL DEFAULT '',
                banner      TEXT NOT NULL DEFAULT 'midnight',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_text_channels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL DEFAULT 0,
                category    TEXT NOT NULL DEFAULT 'Текстовые',
                name        TEXT NOT NULL,
                topic       TEXT NOT NULL DEFAULT '',
                position    INTEGER NOT NULL DEFAULT 0,
                is_private  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_dm_threads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id    INTEGER NOT NULL,
                peer_id     INTEGER NOT NULL,
                title       TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                member_low  INTEGER NOT NULL DEFAULT 0,
                member_high INTEGER NOT NULL DEFAULT 0,
                UNIQUE(owner_id, peer_id)
            );

            CREATE TABLE IF NOT EXISTS platform_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scope           TEXT NOT NULL,
                target_id       INTEGER NOT NULL,
                author_id       INTEGER NOT NULL,
                author_name     TEXT NOT NULL DEFAULT '',
                content         TEXT NOT NULL,
                attachment_json TEXT NOT NULL DEFAULT '[]',
                edited_at       TEXT NOT NULL DEFAULT '',
                deleted_at      TEXT NOT NULL DEFAULT '',
                guild_id        INTEGER NOT NULL DEFAULT 0,
                channel_id      INTEGER NOT NULL DEFAULT 0,
                source          TEXT NOT NULL DEFAULT 'platform',
                status          TEXT NOT NULL DEFAULT 'stored',
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_message_reactions (
                message_id INTEGER NOT NULL,
                emoji      TEXT NOT NULL,
                author_id  INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(message_id, emoji, author_id)
            );

            CREATE TABLE IF NOT EXISTS platform_dm_reads (
                thread_id            INTEGER NOT NULL,
                user_id              INTEGER NOT NULL,
                last_read_message_id INTEGER NOT NULL DEFAULT 0,
                updated_at           TEXT NOT NULL,
                PRIMARY KEY(thread_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS platform_discord_outbox (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id      INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS platform_activities (
                discord_user_id INTEGER PRIMARY KEY,
                activity_type   TEXT NOT NULL DEFAULT 'game',
                title           TEXT NOT NULL DEFAULT '',
                subtitle        TEXT NOT NULL DEFAULT '',
                image           TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_rate_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                action     TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id      INTEGER NOT NULL,
                action        TEXT NOT NULL,
                target_type   TEXT NOT NULL,
                target_id     INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at    TEXT NOT NULL
            );
            """
        )
        _ensure_column(conn, "platform_messages", "edited_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "platform_messages", "deleted_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "platform_messages", "guild_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "platform_messages", "channel_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "platform_messages", "source", "TEXT NOT NULL DEFAULT 'platform'")
        _ensure_column(conn, "platform_messages", "status", "TEXT NOT NULL DEFAULT 'stored'")
        _ensure_column(conn, "platform_dm_threads", "member_low", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "platform_dm_threads", "member_high", "INTEGER NOT NULL DEFAULT 0")
        _normalize_dm_threads(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_dm_pair ON platform_dm_threads(member_low, member_high)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_platform_messages_target ON platform_messages(scope, target_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_platform_dm_reads_user ON platform_dm_reads(user_id, thread_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_platform_outbox_status ON platform_discord_outbox(status, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_platform_rate_user_action ON platform_rate_events(user_id, action, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_platform_audit_created ON platform_audit_log(created_at, id)"
        )
        now = _now()
        conn.execute(
            """
            INSERT OR IGNORE INTO platform_servers(id, name, description, icon, banner, created_at, updated_at)
            VALUES(0, 'Ламповый Чай', 'Приватная платформа для своего круга', '', 'midnight', ?, ?)
            """,
            (now, now),
        )
        defaults = [
            ("INFO", "welcome", "Старт и важное", 10),
            ("INFO", "general", "Главный текстовый канал", 20),
            ("Качалка гачи", "wwm-info", "Where Winds Meet", 30),
            ("Качалка гачи", "wuwa-info", "Wuthering Waves", 40),
            ("Текстовые", "lamptea", "Общий трёп", 50),
            ("Текстовые", "flood", "Флудилка", 60),
        ]
        for category, name, topic, position in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_text_channels(server_id, category, name, topic, position, created_at, updated_at)
                SELECT 0, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS(
                    SELECT 1 FROM platform_text_channels WHERE server_id=0 AND name=?
                )
                """,
                (category, name, topic, position, now, now, name),
            )
        _migrate_legacy_web_chat(conn)
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone())


def _archive_table(conn: sqlite3.Connection, table: str) -> None:
    if not _table_exists(conn, table):
        return
    backup = f"{table}_retired_backup"
    if _table_exists(conn, backup):
        raise RuntimeError(f"cannot archive {table}: {backup} already exists")
    conn.execute(f"ALTER TABLE {table} RENAME TO {backup}")


def _migrate_legacy_web_chat(conn: sqlite3.Connection) -> None:
    """Move the retired fallback chat into the canonical platform channel once."""
    if _table_exists(conn, "web_chat_messages"):
        general = conn.execute(
            "SELECT id FROM platform_text_channels WHERE server_id=0 AND name='general' ORDER BY id LIMIT 1"
        ).fetchone()
        if not general:
            raise RuntimeError("general platform channel is missing")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_web_chat_migration (
                legacy_message_id   INTEGER PRIMARY KEY,
                platform_message_id INTEGER NOT NULL UNIQUE,
                migrated_at         TEXT NOT NULL
            )
            """
        )
        legacy_rows = conn.execute(
            """
            SELECT id, guild_id, channel_id, discord_user_id, author_name,
                   content, attachment_json, source, status, edited_at, deleted_at, created_at
            FROM web_chat_messages ORDER BY id
            """
        ).fetchall()
        for row in legacy_rows:
            if conn.execute(
                "SELECT 1 FROM platform_web_chat_migration WHERE legacy_message_id=?",
                (int(row[0]),),
            ).fetchone():
                continue
            cur = conn.execute(
                """
                INSERT INTO platform_messages(
                    scope, target_id, author_id, author_name, content, attachment_json,
                    edited_at, deleted_at, guild_id, channel_id, source, status, created_at
                ) VALUES('channel', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(general[0]), int(row[3]), row[4], row[5], row[6], row[9], row[10],
                 int(row[1]), int(row[2]), row[7], row[8], row[11]),
            )
            conn.execute(
                "INSERT INTO platform_web_chat_migration VALUES(?, ?, ?)",
                (int(row[0]), int(cur.lastrowid), _now()),
            )

        if _table_exists(conn, "web_chat_reactions"):
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_message_reactions(message_id, emoji, author_id, created_at)
                SELECT m.platform_message_id, r.emoji, r.discord_user_id, r.created_at
                FROM web_chat_reactions r
                JOIN platform_web_chat_migration m ON m.legacy_message_id=r.message_id
                """
            )
            missing_reactions = int(conn.execute(
                """
                SELECT COUNT(*)
                FROM web_chat_reactions r
                JOIN platform_web_chat_migration m ON m.legacy_message_id=r.message_id
                WHERE NOT EXISTS(
                    SELECT 1 FROM platform_message_reactions p
                    WHERE p.message_id=m.platform_message_id
                      AND p.emoji=r.emoji AND p.author_id=r.discord_user_id
                )
                """
            ).fetchone()[0])
            if missing_reactions:
                raise RuntimeError("legacy web chat reaction migration is incomplete")
        migrated = int(conn.execute("SELECT COUNT(*) FROM platform_web_chat_migration").fetchone()[0])
        if migrated < len(legacy_rows):
            raise RuntimeError("legacy web chat migration is incomplete")
        _archive_table(conn, "web_chat_reactions")
        _archive_table(conn, "web_chat_messages")

    if _table_exists(conn, "web_bot_outbox"):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_web_outbox_migration (
                legacy_outbox_id   INTEGER PRIMARY KEY,
                platform_outbox_id INTEGER NOT NULL UNIQUE,
                migrated_at        TEXT NOT NULL
            )
            """
        )
        legacy_rows = conn.execute(
            """
            SELECT id, guild_id, channel_id, discord_user_id, author_name,
                   content, status, error, created_at, sent_at
            FROM web_bot_outbox ORDER BY id
            """
        ).fetchall()
        for row in legacy_rows:
            if conn.execute(
                "SELECT 1 FROM platform_web_outbox_migration WHERE legacy_outbox_id=?",
                (int(row[0]),),
            ).fetchone():
                continue
            cur = conn.execute(
                """
                INSERT INTO platform_discord_outbox(
                    guild_id, channel_id, discord_user_id, author_name,
                    content, status, error, created_at, sent_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row[1:],
            )
            conn.execute(
                "INSERT INTO platform_web_outbox_migration VALUES(?, ?, ?)",
                (int(row[0]), int(cur.lastrowid), _now()),
            )
        migrated = int(conn.execute("SELECT COUNT(*) FROM platform_web_outbox_migration").fetchone()[0])
        if migrated < len(legacy_rows):
            raise RuntimeError("legacy Discord outbox migration is incomplete")
        _archive_table(conn, "web_bot_outbox")


def _normalize_dm_threads(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS platform_dm_threads_legacy_backup (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            peer_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS platform_dm_message_target_backup (
            message_id INTEGER PRIMARY KEY,
            original_target_id INTEGER NOT NULL,
            archived_at TEXT NOT NULL
        );
        """
    )
    rows = conn.execute(
        "SELECT id, owner_id, peer_id, title, created_at, updated_at FROM platform_dm_threads ORDER BY id"
    ).fetchall()
    pairs: dict[tuple[int, int], list[tuple]] = {}
    for row in rows:
        low, high = sorted((int(row[1]), int(row[2])))
        pairs.setdefault((low, high), []).append(row)
    for (low, high), items in pairs.items():
        canonical = items[0]
        canonical_id = int(canonical[0])
        title = next((str(item[3]) for item in items if item[3]), "")
        updated_at = max(str(item[5]) for item in items)
        duplicate_ids = [int(item[0]) for item in items[1:]]
        if duplicate_ids:
            placeholders = ",".join("?" for _ in duplicate_ids)
            archived_at = _now()
            conn.executemany(
                """
                INSERT OR IGNORE INTO platform_dm_threads_legacy_backup(
                    id, owner_id, peer_id, title, created_at, updated_at, archived_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [(*item[:6], archived_at) for item in items],
            )
            conn.execute(
                f"""
                INSERT OR IGNORE INTO platform_dm_message_target_backup(
                    message_id, original_target_id, archived_at
                )
                SELECT id, target_id, ? FROM platform_messages
                WHERE scope='dm' AND target_id IN ({placeholders})
                """,
                (archived_at, *duplicate_ids),
            )
            conn.execute(
                f"UPDATE platform_messages SET target_id=? WHERE scope='dm' AND target_id IN ({placeholders})",
                (canonical_id, *duplicate_ids),
            )
            conn.execute(
                f"DELETE FROM platform_dm_threads WHERE id IN ({placeholders})",
                duplicate_ids,
            )
        conn.execute(
            "UPDATE platform_dm_threads SET owner_id=?, peer_id=?, member_low=?, member_high=?, title=?, updated_at=? WHERE id=?",
            (low, high, low, high, title, updated_at, canonical_id),
        )


def list_servers() -> list[dict[str, Any]]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT id, name, description, icon, banner, updated_at FROM platform_servers ORDER BY id"
        ).fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "icon": row[3],
            "banner": row[4],
            "updated_at": row[5],
        }
        for row in rows
    ]


def get_server(server_id: int = 0) -> dict[str, Any]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT id, name, description, icon, banner, updated_at
            FROM platform_servers
            WHERE id=?
            """,
            (int(server_id),),
        ).fetchone()
    if not row:
        servers = list_servers()
        return servers[0] if servers else {
            "id": 0,
            "name": "Lamp Tea",
            "description": "",
            "icon": "",
            "banner": "midnight",
            "updated_at": "",
        }
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "icon": row[3],
        "banner": row[4],
        "updated_at": row[5],
    }


def update_server(
    server_id: int = 0,
    name: str = "",
    description: str = "",
    icon: str = "",
    banner: str = "midnight",
) -> dict[str, Any]:
    ensure_platform_tables()
    current = get_server(server_id)
    clean_name = name.strip()[:80] or current["name"] or "Lamp Tea"
    clean_description = description.strip()[:500]
    clean_icon = icon.strip()[:80]
    clean_banner = banner.strip()[:40] or current["banner"] or "midnight"
    now = _now()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO platform_servers(id, name, description, icon, banner, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                icon=excluded.icon,
                banner=excluded.banner,
                updated_at=excluded.updated_at
            """,
            (
                int(server_id),
                clean_name,
                clean_description,
                clean_icon,
                clean_banner,
                current.get("updated_at") or now,
                now,
            ),
        )
        conn.commit()
    return get_server(server_id)


def list_text_channels(server_id: int = 0) -> list[dict[str, Any]]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, server_id, category, name, topic, position, is_private
            FROM platform_text_channels
            WHERE server_id=?
            ORDER BY category ASC, position ASC, id ASC
            """,
            (int(server_id),),
        ).fetchall()
    return [
        {
            "id": row[0],
            "server_id": row[1],
            "category": row[2],
            "name": row[3],
            "topic": row[4],
            "position": row[5],
            "is_private": bool(row[6]),
        }
        for row in rows
    ]


def create_text_channel(server_id: int, category: str, name: str, topic: str = "") -> dict[str, Any]:
    ensure_platform_tables()
    clean_name = name.strip().lower().replace(" ", "-")[:60] or "new-channel"
    clean_category = category.strip()[:60] or "Текстовые"
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM platform_text_channels WHERE server_id=?",
            (int(server_id),),
        ).fetchone()
        position = int(row[0] or 0) + 10
        cur = conn.execute(
            """
            INSERT INTO platform_text_channels(server_id, category, name, topic, position, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (int(server_id), clean_category, clean_name, topic.strip()[:200], position, _now(), _now()),
        )
        conn.commit()
        channel_id = int(cur.lastrowid)
    return next(item for item in list_text_channels(server_id) if item["id"] == channel_id)


def list_dm_threads(owner_id: int) -> list[dict[str, Any]]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.member_low, d.member_high, d.title, d.updated_at,
                   u.username, u.global_name, u.avatar,
                   (
                       SELECT COUNT(1) FROM platform_messages m
                       WHERE m.scope='dm' AND m.target_id=d.id AND m.author_id<>?
                         AND m.deleted_at='' AND m.id>COALESCE(r.last_read_message_id, 0)
                   ) AS unread_count
            FROM platform_dm_threads d
            LEFT JOIN web_users u ON u.discord_user_id=(CASE WHEN d.member_low=? THEN d.member_high ELSE d.member_low END)
            LEFT JOIN platform_dm_reads r ON r.thread_id=d.id AND r.user_id=?
            WHERE d.member_low=? OR d.member_high=?
            ORDER BY d.updated_at DESC
            """,
            (int(owner_id), int(owner_id), int(owner_id), int(owner_id), int(owner_id)),
        ).fetchall()
    result = []
    for row in rows:
        peer_id = int(row[2]) if int(row[1]) == int(owner_id) else int(row[1])
        result.append({
            "id": int(row[0]),
            "owner_id": int(owner_id),
            "peer_id": peer_id,
            "title": row[3] or row[6] or row[5] or str(peer_id),
            "updated_at": row[4],
            "peer": {"id": peer_id, "username": row[5], "global_name": row[6], "avatar": row[7]},
            "unread_count": int(row[8] or 0),
        })
    return result


def get_or_create_dm(owner_id: int, peer_id: int, title: str = "") -> dict[str, Any]:
    ensure_platform_tables()
    now = _now()
    low, high = sorted((int(owner_id), int(peer_id)))
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO platform_dm_threads(
                owner_id, peer_id, title, created_at, updated_at, member_low, member_high
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (low, high, title.strip()[:80], now, now, low, high),
        )
    return next(item for item in list_dm_threads(owner_id) if item["peer_id"] == int(peer_id))


def mark_dm_read(thread_id: int, user_id: int) -> bool:
    """Mark a DM read only when the caller is one of its two members."""
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        member = conn.execute(
            """
            SELECT 1 FROM platform_dm_threads
            WHERE id=? AND (member_low=? OR member_high=?)
            """,
            (int(thread_id), int(user_id), int(user_id)),
        ).fetchone()
        if not member:
            return False
        row = conn.execute(
            """
            SELECT COALESCE(MAX(id), 0) FROM platform_messages
            WHERE scope='dm' AND target_id=?
            """,
            (int(thread_id),),
        ).fetchone()
        last_message_id = int(row[0] or 0)
        conn.execute(
            """
            INSERT INTO platform_dm_reads(thread_id,user_id,last_read_message_id,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(thread_id,user_id) DO UPDATE SET
                last_read_message_id=MAX(last_read_message_id, excluded.last_read_message_id),
                updated_at=excluded.updated_at
            """,
            (int(thread_id), int(user_id), last_message_id, _now()),
        )
    return True


def can_access_platform_target(scope: str, target_id: int, user_id: int, can_admin: bool = False) -> bool:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        if scope == "dm":
            row = conn.execute(
                "SELECT 1 FROM platform_dm_threads WHERE id=? AND (member_low=? OR member_high=?)",
                (int(target_id), int(user_id), int(user_id)),
            ).fetchone()
            return bool(row)
        row = conn.execute(
            "SELECT is_private FROM platform_text_channels WHERE id=?",
            (int(target_id),),
        ).fetchone()
        return bool(row and (not bool(row[0]) or can_admin))


def get_platform_message_context(message_id: int) -> dict[str, Any] | None:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT scope, target_id, author_id FROM platform_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
    return {"scope": row[0], "target_id": int(row[1]), "author_id": int(row[2])} if row else None


def add_platform_message(
    scope: str,
    target_id: int,
    author_id: int,
    author_name: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    guild_id: int = 0,
    channel_id: int = 0,
    source: str = "platform",
    queue_discord: bool = False,
) -> int:
    ensure_platform_tables()
    clean_scope = "dm" if scope == "dm" else "channel"
    text = content.strip()[:1800]
    clean_attachments = _clean_attachments(attachments or [])
    if not text and not clean_attachments:
        raise ValueError("empty message")
    with db_connection(SOCIAL_DB) as conn:
        cur = conn.execute(
            """
            INSERT INTO platform_messages(
                scope, target_id, author_id, author_name, content, attachment_json,
                guild_id, channel_id, source, status, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_scope,
                int(target_id),
                int(author_id),
                author_name[:120],
                text,
                json.dumps(clean_attachments, ensure_ascii=False),
                int(guild_id),
                int(channel_id),
                str(source or "platform")[:32],
                "pending" if queue_discord and guild_id and channel_id else "stored",
                _now(),
            ),
        )
        message_id = int(cur.lastrowid)
        if queue_discord and guild_id and channel_id:
            conn.execute(
                """
                INSERT INTO platform_discord_outbox(
                    message_id, guild_id, channel_id, discord_user_id,
                    author_name, content, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (message_id, int(guild_id), int(channel_id), int(author_id), author_name[:120], text, _now()),
            )
        if clean_scope == "dm":
            conn.execute("UPDATE platform_dm_threads SET updated_at=? WHERE id=?", (_now(), int(target_id)))
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
        if not _is_owned_upload_url(url):
            raise ValueError("attachment must be an uploaded ViPik file")
        clean.append({
            "url": url[:500],
            "name": str(item.get("name") or "file")[:180],
            "content_type": str(item.get("content_type") or "application/octet-stream")[:120],
            "size": int(item.get("size") or 0),
        })
    return clean


def _is_owned_upload_url(url: str) -> bool:
    prefix = "/uploads/"
    if not str(url).startswith(prefix):
        return False
    name = str(url)[len(prefix):]
    return bool(
        name
        and "/" not in name
        and "\\" not in name
        and ".." not in name
        and re.fullmatch(r"[A-Za-z0-9._-]{1,180}", name)
    )


def consume_platform_rate_limit(
    user_id: int,
    action: str,
    limits: tuple[tuple[int, int], ...],
    *,
    now: float | None = None,
) -> dict[str, int | bool]:
    """Atomically consume a persistent per-user action budget."""
    ensure_platform_tables()
    moment = float(time.time() if now is None else now)
    clean_action = str(action).strip()[:40] or "unknown"
    clean_limits = tuple(
        (max(1, int(count)), max(1, int(window))) for count, window in limits
    )
    if not clean_limits:
        return {"allowed": True, "retry_after": 0}
    longest_window = max(window for _, window in clean_limits)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM platform_rate_events WHERE created_at<?",
            (moment - max(longest_window, 3600),),
        )
        retry_after = 0
        for count, window in clean_limits:
            row = conn.execute(
                """
                SELECT COUNT(1),MIN(created_at) FROM platform_rate_events
                WHERE user_id=? AND action=? AND created_at>?
                """,
                (int(user_id), clean_action, moment - window),
            ).fetchone()
            if int(row[0] or 0) >= count:
                retry_after = max(
                    retry_after,
                    max(1, int(math.ceil(float(row[1]) + window - moment))),
                )
        if retry_after:
            return {"allowed": False, "retry_after": retry_after}
        conn.execute(
            "INSERT INTO platform_rate_events(user_id,action,created_at) VALUES(?,?,?)",
            (int(user_id), clean_action, moment),
        )
    return {"allowed": True, "retry_after": 0}


def record_platform_audit(
    actor_id: int,
    action: str,
    target_type: str,
    target_id: int,
    metadata: dict[str, Any] | None = None,
) -> int:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        cursor = conn.execute(
            """
            INSERT INTO platform_audit_log(actor_id,action,target_type,target_id,metadata_json,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(actor_id), str(action)[:80], str(target_type)[:40], int(target_id),
                json.dumps(metadata or {}, ensure_ascii=False)[:2000], _now(),
            ),
        )
    return int(cursor.lastrowid)


def list_platform_audit(limit: int = 100) -> list[dict[str, Any]]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id,actor_id,action,target_type,target_id,metadata_json,created_at
            FROM platform_audit_log ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    result = []
    for row in rows:
        try:
            metadata = json.loads(row[5] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        result.append({
            "id": int(row[0]), "actor_id": int(row[1]), "action": row[2],
            "target_type": row[3], "target_id": int(row[4]),
            "metadata": metadata if isinstance(metadata, dict) else {}, "created_at": row[6],
        })
    return result


def list_platform_messages(scope: str, target_id: int, limit: int = 80) -> list[dict[str, Any]]:
    ensure_platform_tables()
    clean_scope = "dm" if scope == "dm" else "channel"
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, scope, target_id, author_id, author_name, content, attachment_json,
                   edited_at, deleted_at, created_at, guild_id, channel_id, source, status
            FROM platform_messages
            WHERE scope=? AND target_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (clean_scope, int(target_id), max(1, min(int(limit), 200))),
        ).fetchall()
        message_ids = [int(row[0]) for row in rows]
        reactions = _reaction_summary(conn, message_ids)
    return [
        {
            "id": row[0],
            "scope": row[1],
            "target_id": row[2],
            "author_id": row[3],
            "author_name": row[4],
            "content": row[5],
            "attachments": _parse_attachments(row[6]),
            "edited_at": row[7],
            "deleted_at": row[8],
            "created_at": row[9],
            "guild_id": row[10],
            "channel_id": row[11],
            "source": row[12],
            "status": row[13],
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


def _reaction_summary(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" for _ in message_ids)
    rows = conn.execute(
        f"""
        SELECT message_id, emoji, COUNT(*) AS count
        FROM platform_message_reactions
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


def edit_platform_message(message_id: int, author_id: int, content: str, can_admin: bool = False) -> bool:
    ensure_platform_tables()
    text = content.strip()[:1800]
    if not text:
        raise ValueError("empty message")
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT author_id, deleted_at, scope FROM platform_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
        if not row or row[1]:
            return False
        if int(row[0]) != int(author_id) and not (can_admin and row[2] != "dm"):
            return False
        conn.execute(
            "UPDATE platform_messages SET content=?, edited_at=? WHERE id=?",
            (text, _now(), int(message_id)),
        )
        conn.commit()
        return True


def delete_platform_message(message_id: int, author_id: int, can_admin: bool = False) -> bool:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT author_id, deleted_at, scope FROM platform_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
        if not row or row[1]:
            return False
        if int(row[0]) != int(author_id) and not (can_admin and row[2] != "dm"):
            return False
        conn.execute(
            "UPDATE platform_messages SET content='', deleted_at=? WHERE id=?",
            (_now(), int(message_id)),
        )
        conn.commit()
        return True


def toggle_platform_reaction(message_id: int, author_id: int, emoji: str) -> bool:
    ensure_platform_tables()
    clean = emoji.strip()[:24] or "+"
    with db_connection(SOCIAL_DB) as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM platform_message_reactions
            WHERE message_id=? AND emoji=? AND author_id=?
            """,
            (int(message_id), clean, int(author_id)),
        ).fetchone()
        if exists:
            conn.execute(
                """
                DELETE FROM platform_message_reactions
                WHERE message_id=? AND emoji=? AND author_id=?
                """,
                (int(message_id), clean, int(author_id)),
            )
            active = False
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_message_reactions(message_id, emoji, author_id, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (int(message_id), clean, int(author_id), _now()),
            )
            active = True
        conn.commit()
        return active


def get_general_channel_id() -> int:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT id FROM platform_text_channels WHERE server_id=0 AND name='general' ORDER BY id LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError("general platform channel is missing")
    return int(row[0])


def add_general_chat_message(
    discord_user_id: int,
    author_name: str,
    content: str,
    guild_id: int = 0,
    channel_id: int = 0,
    source: str = "web",
    attachments: list[dict[str, Any]] | None = None,
) -> int:
    return add_platform_message(
        "channel",
        get_general_channel_id(),
        discord_user_id,
        author_name,
        content,
        attachments,
        guild_id=guild_id,
        channel_id=channel_id,
        source=source,
        queue_discord=source == "web" and bool(guild_id and channel_id),
    )


def list_general_chat_messages(limit: int = 80) -> list[dict[str, Any]]:
    messages = list_platform_messages("channel", get_general_channel_id(), limit)
    return [
        {
            **message,
            "discord_user_id": message["author_id"],
        }
        for message in messages
    ]


def _is_general_message(message_id: int) -> bool:
    context = get_platform_message_context(message_id)
    return bool(
        context
        and context["scope"] == "channel"
        and context["target_id"] == get_general_channel_id()
    )


def edit_general_chat_message(message_id: int, author_id: int, content: str, can_admin: bool = False) -> bool:
    return _is_general_message(message_id) and edit_platform_message(message_id, author_id, content, can_admin)


def delete_general_chat_message(message_id: int, author_id: int, can_admin: bool = False) -> bool:
    return _is_general_message(message_id) and delete_platform_message(message_id, author_id, can_admin)


def toggle_general_chat_reaction(message_id: int, author_id: int, emoji: str) -> bool:
    if not _is_general_message(message_id):
        raise ValueError("message is outside the general channel")
    return toggle_platform_reaction(message_id, author_id, emoji)


def claim_pending_discord_outbox(limit: int = 20) -> list[dict[str, Any]]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, message_id, guild_id, channel_id, discord_user_id, author_name, content
            FROM platform_discord_outbox
            WHERE status='pending'
            ORDER BY id ASC LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE platform_discord_outbox SET status='sending' WHERE id=? AND status='pending'",
                [(int(row[0]),) for row in rows],
            )
    return [
        {
            "id": row[0],
            "message_id": row[1],
            "guild_id": row[2],
            "channel_id": row[3],
            "discord_user_id": row[4],
            "author_name": row[5],
            "content": row[6],
        }
        for row in rows
    ]


def mark_discord_outbox_sent(item_id: int) -> None:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT message_id FROM platform_discord_outbox WHERE id=?",
            (int(item_id),),
        ).fetchone()
        conn.execute(
            "UPDATE platform_discord_outbox SET status='sent', sent_at=?, error='' WHERE id=?",
            (_now(), int(item_id)),
        )
        if row and int(row[0]):
            conn.execute("UPDATE platform_messages SET status='sent' WHERE id=?", (int(row[0]),))


def mark_discord_outbox_failed(item_id: int, error: str) -> None:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT message_id FROM platform_discord_outbox WHERE id=?",
            (int(item_id),),
        ).fetchone()
        conn.execute(
            "UPDATE platform_discord_outbox SET status='pending', error=? WHERE id=?",
            (error[:300], int(item_id)),
        )
        if row and int(row[0]):
            conn.execute("UPDATE platform_messages SET status='pending' WHERE id=?", (int(row[0]),))


def list_activities() -> list[dict[str, Any]]:
    ensure_platform_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT a.discord_user_id, a.activity_type, a.title, a.subtitle, a.image, a.updated_at,
                   u.username, u.global_name
            FROM platform_activities a
            LEFT JOIN web_users u ON u.discord_user_id=a.discord_user_id
            ORDER BY a.updated_at DESC LIMIT 25
            """
        ).fetchall()
    return [
        {
            "discord_user_id": row[0],
            "activity_type": row[1],
            "title": row[2],
            "subtitle": row[3],
            "image": row[4],
            "updated_at": row[5],
            "username": row[6],
            "global_name": row[7],
        }
        for row in rows
    ]
