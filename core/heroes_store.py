from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.db import connection as db_connection
from core.paths import SOCIAL_DB
from core.settings_store import (
    get_feature_channel_ids,
    has_feature_setting,
    is_feature_enabled,
    set_feature_channel,
    set_feature_enabled,
)


FEATURE_HEROES_TROLL = "heroes_troll"
UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
_INITIALIZED_DATABASES: set[str] = set()


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone())


def ensure_heroes_storage() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS heroes_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                seconds INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS heroes_active_sessions (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
    migrate_legacy_heroes_settings()
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def migrate_legacy_heroes_settings() -> int:
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT guild_id, channel_id FROM heroes_troll_config ORDER BY guild_id"
        ).fetchall() if _table_exists(conn, "heroes_troll_config") else []
    for guild_id, channel_id in rows:
        guild_id = int(guild_id)
        channel_id = int(channel_id or 0)
        if not has_feature_setting(guild_id, FEATURE_HEROES_TROLL):
            set_feature_enabled(guild_id, FEATURE_HEROES_TROLL, True)
            if channel_id:
                set_feature_channel(guild_id, FEATURE_HEROES_TROLL, channel_id, "output", "legacy migration")
        actual = get_feature_channel_ids(guild_id, FEATURE_HEROES_TROLL, "output")
        if actual != ({channel_id} if channel_id else set()):
            raise RuntimeError(f"heroes troll channel migration mismatch for guild {guild_id}")
    _archive_legacy_config(len(rows))
    return len(rows)


def _archive_legacy_config(expected_rows: int) -> None:
    table = "heroes_troll_config"
    backup = f"{table}_legacy_backup"
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, table):
            return
        rows = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        if rows != expected_rows:
            raise RuntimeError("heroes troll config changed during migration")
        if _table_exists(conn, backup):
            if rows:
                raise RuntimeError("heroes troll backup already exists for non-empty config")
            conn.execute(f'DROP TABLE "{table}"')
            return
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{backup}"')
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_migration_archive(
                table_name TEXT PRIMARY KEY, backup_table TEXT NOT NULL,
                source_rows INTEGER NOT NULL, archived_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings_migration_archive VALUES(?,?,?,?)",
            (table, backup, rows, datetime.now(UTC).isoformat()),
        )


def get_heroes_output_channel_id(guild_id: int) -> int | None:
    values = get_feature_channel_ids(guild_id, FEATURE_HEROES_TROLL, "output")
    return next(iter(values), None)


def heroes_troll_enabled(guild_id: int) -> bool:
    return is_feature_enabled(guild_id, FEATURE_HEROES_TROLL, default=True)


def set_heroes_output_channel(guild_id: int, channel_id: int) -> None:
    set_feature_enabled(guild_id, FEATURE_HEROES_TROLL, True)
    set_feature_channel(guild_id, FEATURE_HEROES_TROLL, channel_id, "output", "Discord command")


def load_active_sessions() -> list[dict[str, object]]:
    ensure_heroes_storage()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT guild_id, user_id, game_name, started_at FROM heroes_active_sessions"
        ).fetchall()
    result = []
    for guild_id, user_id, game_name, started_at in rows:
        try:
            started = datetime.fromisoformat(started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            started = datetime.now(UTC)
        result.append({
            "guild_id": int(guild_id), "user_id": int(user_id),
            "game_name": str(game_name), "started_at": started,
        })
    return result


def remember_active_session(guild_id: int, user_id: int, game_name: str, started_at: datetime) -> None:
    ensure_heroes_storage()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO heroes_active_sessions(guild_id, user_id, game_name, started_at)
            VALUES(?,?,?,?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                game_name=excluded.game_name, started_at=excluded.started_at
            """,
            (int(guild_id), int(user_id), game_name, started_at.isoformat()),
        )


def pop_active_session(guild_id: int, user_id: int) -> None:
    ensure_heroes_storage()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            "DELETE FROM heroes_active_sessions WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        )


def save_finished_session(
    guild_id: int, user_id: int, game_name: str, started_at: datetime, ended_at: datetime
) -> int:
    ensure_heroes_storage()
    seconds = int((ended_at - started_at).total_seconds())
    if seconds <= 0:
        return 0
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO heroes_sessions(guild_id, user_id, game_name, started_at, ended_at, seconds)
            VALUES(?,?,?,?,?,?)
            """,
            (int(guild_id), int(user_id), game_name, started_at.isoformat(), ended_at.isoformat(), seconds),
        )
    return seconds


def get_last_week_heroes_top(guild_id: int) -> list[tuple[int, int]]:
    ensure_heroes_storage()
    today = datetime.now(MSK).date()
    start_this_week = today - timedelta(days=today.weekday())
    start_prev_week = start_this_week - timedelta(days=7)
    start_prev_week_utc = datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat()
    start_this_week_utc = datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM heroes_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<?
            GROUP BY user_id HAVING total_seconds>0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (int(guild_id), start_prev_week_utc, start_this_week_utc),
        ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]
