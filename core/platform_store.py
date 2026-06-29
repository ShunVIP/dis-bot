from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_platform_tables():
    with sqlite3.connect(SOCIAL_DB) as conn:
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
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_message_reactions (
                message_id INTEGER NOT NULL,
                emoji      TEXT NOT NULL,
                author_id  INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(message_id, emoji, author_id)
            );

            CREATE TABLE IF NOT EXISTS platform_activities (
                discord_user_id INTEGER PRIMARY KEY,
                activity_type   TEXT NOT NULL DEFAULT 'game',
                title           TEXT NOT NULL DEFAULT '',
                subtitle        TEXT NOT NULL DEFAULT '',
                image           TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL
            );
            """
        )
        _ensure_column(conn, "platform_messages", "edited_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "platform_messages", "deleted_at", "TEXT NOT NULL DEFAULT ''")
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
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def list_servers() -> list[dict[str, Any]]:
    ensure_platform_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
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
    with sqlite3.connect(SOCIAL_DB) as conn:
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
    with sqlite3.connect(SOCIAL_DB) as conn:
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
    with sqlite3.connect(SOCIAL_DB) as conn:
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
    with sqlite3.connect(SOCIAL_DB) as conn:
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
    with sqlite3.connect(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.owner_id, d.peer_id, d.title, d.updated_at,
                   u.username, u.global_name, u.avatar
            FROM platform_dm_threads d
            LEFT JOIN web_users u ON u.discord_user_id=d.peer_id
            WHERE d.owner_id=?
            ORDER BY d.updated_at DESC
            """,
            (int(owner_id),),
        ).fetchall()
    return [
        {
            "id": row[0],
            "owner_id": row[1],
            "peer_id": row[2],
            "title": row[3] or row[6] or row[5] or str(row[2]),
            "updated_at": row[4],
            "peer": {"id": row[2], "username": row[5], "global_name": row[6], "avatar": row[7]},
        }
        for row in rows
    ]


def get_or_create_dm(owner_id: int, peer_id: int, title: str = "") -> dict[str, Any]:
    ensure_platform_tables()
    now = _now()
    with sqlite3.connect(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO platform_dm_threads(owner_id, peer_id, title, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (int(owner_id), int(peer_id), title.strip()[:80], now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO platform_dm_threads(owner_id, peer_id, title, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (int(peer_id), int(owner_id), title.strip()[:80], now, now),
        )
        conn.commit()
    return next(item for item in list_dm_threads(owner_id) if item["peer_id"] == int(peer_id))


def add_platform_message(
    scope: str,
    target_id: int,
    author_id: int,
    author_name: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
) -> int:
    ensure_platform_tables()
    clean_scope = "dm" if scope == "dm" else "channel"
    text = content.strip()[:1800]
    clean_attachments = _clean_attachments(attachments or [])
    if not text and not clean_attachments:
        raise ValueError("empty message")
    with sqlite3.connect(SOCIAL_DB) as conn:
        cur = conn.execute(
            """
            INSERT INTO platform_messages(scope, target_id, author_id, author_name, content, attachment_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_scope,
                int(target_id),
                int(author_id),
                author_name[:120],
                text,
                json.dumps(clean_attachments, ensure_ascii=False),
                _now(),
            ),
        )
        if clean_scope == "dm":
            conn.execute("UPDATE platform_dm_threads SET updated_at=? WHERE id=?", (_now(), int(target_id)))
        conn.commit()
        return int(cur.lastrowid)


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


def list_platform_messages(scope: str, target_id: int, limit: int = 80) -> list[dict[str, Any]]:
    ensure_platform_tables()
    clean_scope = "dm" if scope == "dm" else "channel"
    with sqlite3.connect(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, scope, target_id, author_id, author_name, content, attachment_json, edited_at, deleted_at, created_at
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
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT author_id, deleted_at FROM platform_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
        if not row or row[1]:
            return False
        if int(row[0]) != int(author_id) and not can_admin:
            return False
        conn.execute(
            "UPDATE platform_messages SET content=?, edited_at=? WHERE id=?",
            (text, _now(), int(message_id)),
        )
        conn.commit()
        return True


def delete_platform_message(message_id: int, author_id: int, can_admin: bool = False) -> bool:
    ensure_platform_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT author_id, deleted_at FROM platform_messages WHERE id=?",
            (int(message_id),),
        ).fetchone()
        if not row or row[1]:
            return False
        if int(row[0]) != int(author_id) and not can_admin:
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
    with sqlite3.connect(SOCIAL_DB) as conn:
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


def list_activities() -> list[dict[str, Any]]:
    ensure_platform_tables()
    with sqlite3.connect(SOCIAL_DB) as conn:
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
