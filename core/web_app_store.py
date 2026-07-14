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
from core.db import connection as db_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_web_tables():
    with db_connection(SOCIAL_DB) as conn:
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

            CREATE TABLE IF NOT EXISTS web_login_codes (
                code_hash       TEXT PRIMARY KEY,
                discord_user_id INTEGER NOT NULL,
                expires_at      TEXT NOT NULL,
                used_at         TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
            );

            """
        )
        # OAuth tokens are not needed after the identity snapshot is saved.
        # Clear values written by older builds instead of keeping reusable
        # Discord credentials in SQLite.
        conn.execute("UPDATE web_users SET access_token='', refresh_token='' WHERE access_token!='' OR refresh_token!=''")
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
    with db_connection(SOCIAL_DB) as conn:
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
                "",
                "",
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
    with db_connection(SOCIAL_DB) as conn:
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
    with db_connection(SOCIAL_DB) as conn:
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
    with db_connection(SOCIAL_DB) as conn:
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
    with db_connection(SOCIAL_DB) as conn:
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
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            "INSERT INTO web_sessions(session_id, discord_user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
            (session_hash, int(discord_user_id), expires_at, _now()),
        )
        conn.commit()
    return session_id


def get_session_user(session_id: str) -> dict[str, Any] | None:
    ensure_web_tables()
    if not session_id:
        return None
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT discord_user_id, expires_at FROM web_sessions WHERE session_id=?",
            (session_hash,),
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
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest() if session_id else ""
    with db_connection(SOCIAL_DB) as conn:
        conn.execute("DELETE FROM web_sessions WHERE session_id=?", (session_hash,))
        conn.commit()


def issue_login_code(discord_user_id: int, ttl_minutes: int = 10) -> str:
    ensure_web_tables()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(10))
    code_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            "DELETE FROM web_login_codes WHERE discord_user_id=? OR expires_at<? OR used_at!=''",
            (int(discord_user_id), _now()),
        )
        conn.execute(
            "INSERT INTO web_login_codes(code_hash, discord_user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
            (code_hash, int(discord_user_id), expires_at, _now()),
        )
    return f"{code[:5]}-{code[5:]}"


def consume_login_code(code: str) -> dict[str, Any] | None:
    ensure_web_tables()
    normalized = "".join(ch for ch in str(code).upper() if ch.isalnum())
    if len(normalized) != 10:
        return None
    code_hash = hashlib.sha256(normalized.encode("ascii")).hexdigest()
    now = datetime.now(timezone.utc)
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT discord_user_id, expires_at, used_at FROM web_login_codes WHERE code_hash=?",
            (code_hash,),
        ).fetchone()
        if not row or row[2]:
            return None
        try:
            if datetime.fromisoformat(row[1]) < now:
                return None
        except (TypeError, ValueError):
            return None
        updated = conn.execute(
            "UPDATE web_login_codes SET used_at=? WHERE code_hash=? AND used_at=''",
            (now.isoformat(), code_hash),
        ).rowcount
        if updated != 1:
            return None
        user_id = int(row[0])
    return get_web_user(user_id)
