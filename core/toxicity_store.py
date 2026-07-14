from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.db import connection as db_connection
from core.paths import SOCIAL_DB
from core.settings_store import (
    clear_feature_channel,
    clear_feature_channels,
    get_feature_payload,
    get_feature_policy,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)


FEATURE_TOXICITY = "toxicity"
UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
_INITIALIZED_DATABASES: set[str] = set()


def ensure_toxicity_storage() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS toxicity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                msg_snippet TEXT NOT NULL DEFAULT '',
                logged_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS toxicity_weekly (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                week TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id, week)
            );
            CREATE TABLE IF NOT EXISTS toxicity_ml_shadow (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rule_level INTEGER NOT NULL,
                ml_level INTEGER NOT NULL,
                ml_confidence REAL NOT NULL,
                model_version TEXT NOT NULL DEFAULT '',
                msg_snippet TEXT NOT NULL DEFAULT '',
                logged_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS toxicity_ml_feedback (
                message_id INTEGER PRIMARY KEY,
                msg_snippet TEXT NOT NULL,
                corrected_level INTEGER NOT NULL CHECK(corrected_level BETWEEN 0 AND 3),
                reviewer_id INTEGER NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_toxicity_log_guild_time
                ON toxicity_log(guild_id, logged_at);
            CREATE INDEX IF NOT EXISTS idx_toxicity_shadow_review
                ON toxicity_ml_shadow(logged_at, rule_level, ml_level);
            """
        )
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def get_toxicity_config(guild_id: int) -> tuple[bool, int, set[int], set[int]]:
    policy = get_feature_policy(guild_id, FEATURE_TOXICITY)
    payload = get_feature_payload(guild_id, FEATURE_TOXICITY)
    try:
        threshold = int(payload.get("threshold") or 1)
    except (TypeError, ValueError):
        threshold = 1
    return (
        policy.enabled,
        max(1, min(threshold, 3)),
        set(policy.allowed_channel_ids),
        set(policy.excluded_channel_ids),
    )


def set_toxicity_enabled(guild_id: int, enabled: bool) -> None:
    set_feature_enabled(guild_id, FEATURE_TOXICITY, enabled)


def set_toxicity_threshold(guild_id: int, threshold: int) -> None:
    payload = get_feature_payload(guild_id, FEATURE_TOXICITY)
    payload["threshold"] = max(1, min(int(threshold), 3))
    set_feature_payload(guild_id, FEATURE_TOXICITY, payload)


def set_toxicity_allow_channels(guild_id: int, channel_ids: set[int]) -> None:
    clear_feature_channels(guild_id, FEATURE_TOXICITY, "allow")
    for channel_id in sorted(channel_ids):
        set_feature_channel(guild_id, FEATURE_TOXICITY, channel_id, "allow", "Discord admin command")


def exclude_toxicity_channel(guild_id: int, channel_id: int, reason: str = "") -> None:
    set_feature_channel(
        guild_id, FEATURE_TOXICITY, channel_id, "exclude", reason or "Discord admin command"
    )


def include_toxicity_channel(guild_id: int, channel_id: int) -> int:
    return clear_feature_channel(guild_id, FEATURE_TOXICITY, channel_id, "exclude")


def current_week(now: datetime | None = None) -> str:
    value = now or datetime.now(MSK)
    if value.tzinfo is None:
        value = value.replace(tzinfo=MSK)
    return value.astimezone(MSK).strftime("%Y-W%W")


def save_shadow_prediction(
    *, message_id: int, guild_id: int, channel_id: int, user_id: int,
    text: str, prediction: dict, logged_at: datetime | None = None,
) -> bool:
    ensure_toxicity_storage()
    if not prediction.get("model_version"):
        return False
    with db_connection(SOCIAL_DB) as conn:
        inserted = conn.execute(
            """
            INSERT OR IGNORE INTO toxicity_ml_shadow(
                message_id,guild_id,channel_id,user_id,rule_level,ml_level,
                ml_confidence,model_version,msg_snippet,logged_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(message_id), int(guild_id), int(channel_id), int(user_id),
                int(prediction["rule_level"]), int(prediction["ml_level"]),
                float(prediction["ml_confidence"]), str(prediction["model_version"]),
                text[:160], (logged_at or datetime.now(UTC)).isoformat(),
            ),
        ).rowcount
    return bool(inserted)


def record_toxic_event(
    guild_id: int, user_id: int, channel_id: int, level: int, text: str,
    *, logged_at: datetime | None = None,
) -> int:
    ensure_toxicity_storage()
    timestamp = logged_at or datetime.now(UTC)
    week = current_week(timestamp)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO toxicity_log(guild_id,user_id,channel_id,level,msg_snippet,logged_at)
            VALUES(?,?,?,?,?,?)
            """,
            (int(guild_id), int(user_id), int(channel_id), int(level), text[:100], timestamp.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO toxicity_weekly(user_id,guild_id,week,count) VALUES(?,?,?,1)
            ON CONFLICT(user_id,guild_id,week) DO UPDATE SET count=count+1
            """,
            (int(user_id), int(guild_id), week),
        )
        row = conn.execute(
            "SELECT count FROM toxicity_weekly WHERE user_id=? AND guild_id=? AND week=?",
            (int(user_id), int(guild_id), week),
        ).fetchone()
    return int(row[0]) if row else 1


def get_toxicity_top(guild_id: int, period: str = "week", limit: int = 10) -> list[tuple[int, int]]:
    ensure_toxicity_storage()
    with db_connection(SOCIAL_DB) as conn:
        if period == "week":
            rows = conn.execute(
                """
                SELECT user_id,count FROM toxicity_weekly
                WHERE guild_id=? AND week=? ORDER BY count DESC,user_id LIMIT ?
                """,
                (int(guild_id), current_week(), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT user_id,SUM(count) FROM toxicity_weekly
                WHERE guild_id=? GROUP BY user_id ORDER BY SUM(count) DESC,user_id LIMIT ?
                """,
                (int(guild_id), int(limit)),
            ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


def list_pending_shadow_samples(limit: int = 20) -> list[tuple[int, int, int, float, str, str]]:
    ensure_toxicity_storage()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT s.message_id,s.rule_level,s.ml_level,s.ml_confidence,s.model_version,s.msg_snippet
            FROM toxicity_ml_shadow s
            LEFT JOIN toxicity_ml_feedback f ON f.message_id=s.message_id
            WHERE f.message_id IS NULL
            ORDER BY (s.rule_level != s.ml_level) DESC,s.ml_confidence DESC,s.logged_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        (int(row[0]), int(row[1]), int(row[2]), float(row[3]), str(row[4]), str(row[5]))
        for row in rows
    ]


def count_toxicity_feedback() -> int:
    ensure_toxicity_storage()
    with db_connection(SOCIAL_DB) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM toxicity_ml_feedback").fetchone()[0])


def save_toxicity_feedback(message_id: int, corrected_level: int, reviewer_id: int) -> bool:
    ensure_toxicity_storage()
    level = int(corrected_level)
    if level not in range(4):
        raise ValueError("corrected_level must be between 0 and 3")
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT msg_snippet FROM toxicity_ml_shadow WHERE message_id=?", (int(message_id),)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            INSERT INTO toxicity_ml_feedback(message_id,msg_snippet,corrected_level,reviewer_id,reviewed_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(message_id) DO UPDATE SET corrected_level=excluded.corrected_level,
                reviewer_id=excluded.reviewer_id,reviewed_at=excluded.reviewed_at
            """,
            (int(message_id), str(row[0]), level, int(reviewer_id), datetime.now(UTC).isoformat()),
        )
    return True


def toxicity_storage_counts() -> dict[str, int]:
    ensure_toxicity_storage()
    tables = ("toxicity_log", "toxicity_weekly", "toxicity_ml_shadow", "toxicity_ml_feedback")
    with db_connection(SOCIAL_DB) as conn:
        return {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}


def inspect_toxicity_storage(database: str | None = None) -> dict[str, object]:
    """Read-only row counts for production/backup comparison."""
    path = Path(database or SOCIAL_DB).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    tables = ("toxicity_log", "toxicity_weekly", "toxicity_ml_shadow", "toxicity_ml_feedback")
    with closing(sqlite3.connect(uri, uri=True, timeout=15.0)) as conn:
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if table in present else None
            for table in tables
        }
        pending = 0
        if {"toxicity_ml_shadow", "toxicity_ml_feedback"}.issubset(present):
            pending = int(conn.execute(
                """
                SELECT COUNT(*) FROM toxicity_ml_shadow s
                LEFT JOIN toxicity_ml_feedback f ON f.message_id=s.message_id
                WHERE f.message_id IS NULL
                """
            ).fetchone()[0])
    return {
        "database": str(path),
        "counts": counts,
        "pending_shadow_samples": pending,
        "reviewed_samples": int(counts.get("toxicity_ml_feedback") or 0),
        "enforcement": "rules_only",
    }
