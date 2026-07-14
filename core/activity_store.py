from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db import connection
from core.paths import SOCIAL_DB


UTC = timezone.utc


def ensure_activity_tables() -> None:
    with connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activity_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                activity_type TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                ended_at      TEXT    NOT NULL,
                seconds       INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_active_sessions (
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                activity_type TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id, activity_name, activity_type)
            );
            CREATE TABLE IF NOT EXISTS activity_article_cache (
                activity_name TEXT NOT NULL,
                lang          TEXT NOT NULL,
                title         TEXT,
                extract       TEXT,
                url           TEXT,
                fetched_at    TEXT NOT NULL,
                PRIMARY KEY (activity_name, lang)
            );
            CREATE TABLE IF NOT EXISTS activity_game_profiles (
                activity_name TEXT PRIMARY KEY,
                title         TEXT,
                genre         TEXT,
                keywords      TEXT,
                source_text   TEXT,
                source_url    TEXT,
                fetched_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_haiku_history (
                guild_id      INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                haiku_text    TEXT    NOT NULL,
                created_at    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_activity_sessions_guild_started
                ON activity_sessions(guild_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_activity_sessions_guild_user
                ON activity_sessions(guild_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_activity_haiku_history_game
                ON activity_haiku_history(guild_id, activity_name, created_at);
            """
        )
        legacy_habits = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_game_habits'"
        ).fetchone()
        retired_habits = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_game_habits_retired_backup'"
        ).fetchone()
        if legacy_habits and not retired_habits:
            # Preserve the eight-column legacy data for audit, but make it
            # impossible for runtime code to discover or schedule reminders.
            conn.execute(
                "ALTER TABLE activity_game_habits RENAME TO activity_game_habits_retired_backup"
            )


def load_active_sessions() -> list[tuple[int, int, str, str, str]]:
    ensure_activity_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT guild_id,user_id,activity_name,activity_type,started_at "
            "FROM activity_active_sessions"
        ).fetchall()
    return [
        (int(guild_id), int(user_id), str(name), str(kind), str(started_at))
        for guild_id, user_id, name, kind, started_at in rows
    ]


def remember_activity_start(
    guild_id: int,
    user_id: int,
    name: str,
    activity_type: str,
    *,
    started_at: datetime | None = None,
) -> datetime:
    ensure_activity_tables()
    started = started_at or datetime.now(UTC)
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO activity_active_sessions(guild_id,user_id,activity_name,activity_type,started_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id,user_id,activity_name,activity_type) DO UPDATE SET
                started_at=excluded.started_at
            """,
            (int(guild_id), int(user_id), str(name), str(activity_type), started.isoformat()),
        )
    return started


def finish_activity_session(
    guild_id: int,
    user_id: int,
    name: str,
    activity_type: str,
    *,
    cached_started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> int:
    ensure_activity_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT started_at FROM activity_active_sessions
            WHERE guild_id=? AND user_id=? AND activity_name=? AND activity_type=?
            """,
            (int(guild_id), int(user_id), str(name), str(activity_type)),
        ).fetchone()
        conn.execute(
            """
            DELETE FROM activity_active_sessions
            WHERE guild_id=? AND user_id=? AND activity_name=? AND activity_type=?
            """,
            (int(guild_id), int(user_id), str(name), str(activity_type)),
        )
        started = cached_started_at
        if started is None and row:
            try:
                started = datetime.fromisoformat(str(row[0]))
            except (TypeError, ValueError):
                started = None
        if started is None:
            return 0
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        ended = ended_at or datetime.now(UTC)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=UTC)
        seconds = max(0, int((ended - started).total_seconds()))
        if seconds:
            conn.execute(
                """
                INSERT INTO activity_sessions(
                    guild_id,user_id,activity_name,activity_type,started_at,ended_at,seconds
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    int(guild_id), int(user_id), str(name), str(activity_type),
                    started.isoformat(), ended.isoformat(), seconds,
                ),
            )
    return seconds


def get_activity_top(guild_id: int, since: str) -> dict[str, list[tuple[Any, ...]]]:
    ensure_activity_tables()
    with connection(SOCIAL_DB) as conn:
        top_games = conn.execute(
            """
            SELECT activity_name,SUM(seconds) AS total FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND activity_type='game'
            GROUP BY activity_name ORDER BY total DESC LIMIT 10
            """,
            (int(guild_id), str(since)),
        ).fetchall()
        top_game_users = conn.execute(
            """
            SELECT user_id,SUM(seconds) AS total FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND activity_type='game'
            GROUP BY user_id ORDER BY total DESC LIMIT 10
            """,
            (int(guild_id), str(since)),
        ).fetchall()
        other_activities = conn.execute(
            """
            SELECT activity_name,activity_type,SUM(seconds) AS total FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND activity_type<>'game'
            GROUP BY activity_name,activity_type ORDER BY total DESC LIMIT 10
            """,
            (int(guild_id), str(since)),
        ).fetchall()
        top_all_users = conn.execute(
            """
            SELECT user_id,SUM(seconds) AS total FROM activity_sessions
            WHERE guild_id=? AND started_at>=?
            GROUP BY user_id ORDER BY total DESC LIMIT 10
            """,
            (int(guild_id), str(since)),
        ).fetchall()
    return {
        "top_games": top_games,
        "top_game_users": top_game_users,
        "other_activities": other_activities,
        "top_all_users": top_all_users,
    }
