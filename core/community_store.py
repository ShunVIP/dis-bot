from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB
from core.db import connection as db_connection


DEFAULT_ROLES = (
    ("owner", "Хозяин", "#f2c14e", 100),
    ("admin", "Админ", "#ef6f6c", 90),
    ("friend", "Свой", "#4fc3b1", 50),
    ("wwm", "WWM", "#52b484", 40),
    ("lol", "LoL", "#6aa7ff", 30),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_community_tables():
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS community_profiles (
                discord_user_id   INTEGER PRIMARY KEY,
                display_name      TEXT NOT NULL DEFAULT '',
                status_text       TEXT NOT NULL DEFAULT '',
                bio               TEXT NOT NULL DEFAULT '',
                accent_color      TEXT NOT NULL DEFAULT '#4fc3b1',
                banner_preset     TEXT NOT NULL DEFAULT 'midnight',
                avatar_decoration TEXT NOT NULL DEFAULT '',
                badges_json       TEXT NOT NULL DEFAULT '[]',
                updated_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS community_roles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                slug        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                color       TEXT NOT NULL DEFAULT '#9aa7b0',
                position    INTEGER NOT NULL DEFAULT 0,
                source      TEXT NOT NULL DEFAULT 'local',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS community_user_roles (
                discord_user_id INTEGER NOT NULL,
                role_slug       TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'local',
                assigned_at     TEXT NOT NULL,
                PRIMARY KEY(discord_user_id, role_slug)
            );
            """
        )
        now = _now()
        for slug, name, color, position in DEFAULT_ROLES:
            conn.execute(
                """
                INSERT OR IGNORE INTO community_roles(slug, name, color, position, source, created_at, updated_at)
                VALUES(?, ?, ?, ?, 'system', ?, ?)
                """,
                (slug, name, color, int(position), now, now),
            )
        conn.commit()


def get_profile(discord_user_id: int) -> dict[str, Any]:
    ensure_community_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT display_name, status_text, bio, accent_color,
                   banner_preset, avatar_decoration, badges_json, updated_at
            FROM community_profiles
            WHERE discord_user_id=?
            """,
            (int(discord_user_id),),
        ).fetchone()
    if not row:
        return {
            "discord_user_id": int(discord_user_id),
            "display_name": "",
            "status_text": "",
            "bio": "",
            "accent_color": "#4fc3b1",
            "banner_preset": "midnight",
            "avatar_decoration": "",
            "badges": [],
            "updated_at": "",
        }
    try:
        badges = json.loads(row[6] or "[]")
    except Exception:
        badges = []
    return {
        "discord_user_id": int(discord_user_id),
        "display_name": row[0],
        "status_text": row[1],
        "bio": row[2],
        "accent_color": row[3],
        "banner_preset": row[4],
        "avatar_decoration": row[5],
        "badges": badges,
        "updated_at": row[7],
    }


def upsert_profile(
    discord_user_id: int,
    display_name: str = "",
    status_text: str = "",
    bio: str = "",
    accent_color: str = "#4fc3b1",
    banner_preset: str = "midnight",
    avatar_decoration: str = "",
    badges: list[str] | None = None,
):
    ensure_community_tables()
    existing = get_profile(discord_user_id)
    clean_color = accent_color.strip()[:16] if accent_color else existing["accent_color"]
    if not clean_color.startswith("#"):
        clean_color = existing["accent_color"]
    payload = {
        "display_name": display_name.strip()[:80] or existing["display_name"],
        "status_text": status_text.strip()[:120],
        "bio": bio.strip()[:500],
        "accent_color": clean_color,
        "banner_preset": banner_preset.strip()[:40] or existing["banner_preset"],
        "avatar_decoration": avatar_decoration.strip()[:40],
        "badges": badges if badges is not None else existing["badges"],
    }
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO community_profiles(
                discord_user_id, display_name, status_text, bio, accent_color,
                banner_preset, avatar_decoration, badges_json, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                display_name=excluded.display_name,
                status_text=excluded.status_text,
                bio=excluded.bio,
                accent_color=excluded.accent_color,
                banner_preset=excluded.banner_preset,
                avatar_decoration=excluded.avatar_decoration,
                badges_json=excluded.badges_json,
                updated_at=excluded.updated_at
            """,
            (
                int(discord_user_id),
                payload["display_name"],
                payload["status_text"],
                payload["bio"],
                payload["accent_color"],
                payload["banner_preset"],
                payload["avatar_decoration"],
                json.dumps(payload["badges"], ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()


def list_roles() -> list[dict[str, Any]]:
    ensure_community_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT slug, name, color, position, source, updated_at
            FROM community_roles
            ORDER BY position DESC, id ASC
            """
        ).fetchall()
    return [
        {
            "slug": row[0],
            "name": row[1],
            "color": row[2],
            "position": row[3],
            "source": row[4],
            "updated_at": row[5],
        }
        for row in rows
    ]


def upsert_role(
    slug: str,
    name: str,
    color: str = "#9aa7b0",
    position: int = 0,
    source: str = "local",
) -> dict[str, Any]:
    ensure_community_tables()
    clean_slug = slug.strip().lower().replace(" ", "-")[:60]
    clean_name = name.strip()[:80]
    clean_color = color.strip()[:16] or "#9aa7b0"
    if not clean_slug or not clean_name:
        raise ValueError("role slug and name are required")
    if not clean_color.startswith("#"):
        clean_color = "#9aa7b0"
    now = _now()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO community_roles(slug, name, color, position, source, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                color=excluded.color,
                position=excluded.position,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (clean_slug, clean_name, clean_color, int(position), source.strip()[:40] or "local", now, now),
        )
        conn.commit()
    return next(role for role in list_roles() if role["slug"] == clean_slug)


def set_user_roles(discord_user_id: int, role_slugs: list[str], source: str = "local"):
    ensure_community_tables()
    clean = [slug.strip()[:60] for slug in role_slugs if slug.strip()]
    now = _now()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute("DELETE FROM community_user_roles WHERE discord_user_id=?", (int(discord_user_id),))
        conn.executemany(
            """
            INSERT OR IGNORE INTO community_user_roles(discord_user_id, role_slug, source, assigned_at)
            VALUES(?, ?, ?, ?)
            """,
            [(int(discord_user_id), slug, source, now) for slug in clean],
        )
        conn.commit()


def get_user_roles(discord_user_id: int) -> list[dict[str, Any]]:
    ensure_community_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT r.slug, r.name, r.color, r.position, ur.source
            FROM community_user_roles ur
            JOIN community_roles r ON r.slug=ur.role_slug
            WHERE ur.discord_user_id=?
            ORDER BY r.position DESC, r.id ASC
            """,
            (int(discord_user_id),),
        ).fetchall()
    return [
        {"slug": row[0], "name": row[1], "color": row[2], "position": row[3], "source": row[4]}
        for row in rows
    ]


def ensure_first_owner(discord_user_id: int, *, bootstrap_allowed: bool = False) -> bool:
    if not bootstrap_allowed:
        return False
    ensure_community_tables()
    now = _now()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute("SELECT COUNT(*) FROM community_user_roles WHERE role_slug='owner'").fetchone()
        if int(row[0] or 0) == 0:
            conn.execute(
                """
                INSERT OR IGNORE INTO community_user_roles(discord_user_id, role_slug, source, assigned_at)
                VALUES(?, 'owner', 'bootstrap', ?)
                """,
                (int(discord_user_id), now),
            )
            conn.commit()
            return True
    return False


def has_admin_access(discord_user_id: int) -> bool:
    roles = get_user_roles(discord_user_id)
    return any(role["slug"] in {"owner", "admin"} for role in roles)


def list_members() -> list[dict[str, Any]]:
    ensure_community_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT discord_user_id, username, global_name, avatar, updated_at
            FROM web_users
            ORDER BY updated_at DESC
            """
        ).fetchall()
    members = []
    for row in rows:
        user_id = int(row[0])
        profile = get_profile(user_id)
        roles = get_user_roles(user_id)
        members.append(
            {
                "id": user_id,
                "username": row[1],
                "global_name": row[2],
                "avatar": row[3],
                "updated_at": row[4],
                "profile": profile,
                "roles": roles,
            }
        )
    return members
