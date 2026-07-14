from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db import connection as db_connection
from core.paths import SOCIAL_DB
from core.settings_store import (
    clear_feature_channel,
    get_feature_channel_ids,
    get_feature_payload,
    has_feature_setting,
    is_feature_enabled,
    list_feature_channels,
    set_feature_channel,
    set_feature_payload,
)


FEATURE_REWARDS = "activity_rewards"
FEATURE_STATS = "message_stats"

DEFAULT_CONFIG: dict[str, int | bool] = {
    "msg_enabled": False,
    "msg_per_n": 10,
    "msg_coins": 2,
    "msg_rep_per_n": 50,
    "msg_rep": 1,
    "voice_enabled": False,
    "voice_per_min": 5,
    "voice_coins": 1,
}

_BOUNDS = {
    "msg_per_n": (1, 1000),
    "msg_coins": (1, 100),
    "msg_rep_per_n": (0, 1000),
    "msg_rep": (0, 5),
    "voice_per_min": (1, 120),
    "voice_coins": (1, 100),
}
_INITIALIZED_DATABASES: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone())


def _normalize(config: dict[str, Any]) -> dict[str, int | bool]:
    result = dict(DEFAULT_CONFIG)
    for key in ("msg_enabled", "voice_enabled"):
        if key in config:
            result[key] = bool(config[key])
    for key, (minimum, maximum) in _BOUNDS.items():
        if key in config:
            try:
                value = int(config[key])
            except (TypeError, ValueError):
                continue
            result[key] = max(minimum, min(maximum, value))
    return result


def ensure_activity_rewards_storage() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activity_msg_counter (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, guild_id)
            );
            CREATE TABLE IF NOT EXISTS activity_voice_counter (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                minutes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, guild_id)
            );
            """
        )
    migrate_legacy_activity_reward_settings()
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def _get_activity_reward_payload(guild_id: int) -> dict[str, int | bool]:
    return _normalize(get_feature_payload(guild_id, FEATURE_REWARDS, DEFAULT_CONFIG))


def get_activity_reward_config(guild_id: int) -> dict[str, int | bool]:
    result = _get_activity_reward_payload(guild_id)
    if not is_feature_enabled(guild_id, FEATURE_REWARDS, default=True):
        result["msg_enabled"] = False
        result["voice_enabled"] = False
    return result


def has_activity_reward_config(guild_id: int) -> bool:
    return has_feature_setting(guild_id, FEATURE_REWARDS)


def update_activity_reward_config(guild_id: int, updates: dict[str, Any]) -> dict[str, int | bool]:
    current = _get_activity_reward_payload(guild_id)
    current.update({
        key: value for key, value in updates.items()
        if key in DEFAULT_CONFIG and value is not None
    })
    normalized = _normalize(current)
    set_feature_payload(guild_id, FEATURE_REWARDS, normalized, enabled=True)
    return normalized


def is_activity_channel_excluded(guild_id: int, channel_id: int) -> bool:
    return int(channel_id) in get_feature_channel_ids(guild_id, FEATURE_STATS, "exclude")


def list_activity_channel_exclusions(guild_id: int) -> list[dict[str, Any]]:
    return list_feature_channels(guild_id, FEATURE_STATS, "exclude")


def exclude_activity_channel(guild_id: int, channel_id: int, reason: str = "") -> None:
    set_feature_channel(guild_id, FEATURE_STATS, channel_id, "exclude", reason.strip()[:120])


def include_activity_channel(guild_id: int, channel_id: int) -> bool:
    return bool(clear_feature_channel(guild_id, FEATURE_STATS, channel_id, "exclude"))


def increment_message_counter(user_id: int, guild_id: int) -> int:
    ensure_activity_rewards_storage()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO activity_msg_counter(user_id, guild_id, count) VALUES(?,?,1)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET count=count+1
            """,
            (int(user_id), int(guild_id)),
        )
        row = conn.execute(
            "SELECT count FROM activity_msg_counter WHERE user_id=? AND guild_id=?",
            (int(user_id), int(guild_id)),
        ).fetchone()
    return int(row[0])


def add_voice_minutes(user_id: int, guild_id: int, minutes: int) -> tuple[int, int]:
    ensure_activity_rewards_storage()
    amount = max(0, int(minutes))
    with db_connection(SOCIAL_DB) as conn:
        before = conn.execute(
            "SELECT minutes FROM activity_voice_counter WHERE user_id=? AND guild_id=?",
            (int(user_id), int(guild_id)),
        ).fetchone()
        previous = int(before[0]) if before else 0
        conn.execute(
            """
            INSERT INTO activity_voice_counter(user_id, guild_id, minutes) VALUES(?,?,?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET minutes=minutes+excluded.minutes
            """,
            (int(user_id), int(guild_id), amount),
        )
    return previous, previous + amount


def _archive_legacy_table(table: str, expected_rows: int) -> None:
    backup = f"{table}_legacy_backup"
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, table):
            return
        rows = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        if rows != expected_rows:
            raise RuntimeError(f"legacy table changed during migration: {table}")
        if _table_exists(conn, backup):
            if rows:
                raise RuntimeError(f"legacy backup already exists for non-empty table: {table}")
            conn.execute(f'DROP TABLE "{table}"')
            return
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{backup}"')
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_migration_archive(
                table_name TEXT PRIMARY KEY,
                backup_table TEXT NOT NULL,
                source_rows INTEGER NOT NULL,
                archived_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO settings_migration_archive(table_name, backup_table, source_rows, archived_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(table_name) DO UPDATE SET
                backup_table=excluded.backup_table,
                source_rows=MAX(settings_migration_archive.source_rows, excluded.source_rows),
                archived_at=excluded.archived_at
            """,
            (table, backup, rows, _now()),
        )


def migrate_legacy_activity_reward_settings() -> dict[str, int]:
    with db_connection(SOCIAL_DB) as conn:
        config_rows = conn.execute(
            """
            SELECT guild_id, msg_enabled, msg_per_n, msg_coins, msg_rep_per_n, msg_rep,
                   voice_enabled, voice_per_min, voice_coins
            FROM activity_rewards_config ORDER BY guild_id
            """
        ).fetchall() if _table_exists(conn, "activity_rewards_config") else []
        exclusion_rows = conn.execute(
            "SELECT guild_id, channel_id, reason FROM activity_excluded_channels ORDER BY guild_id, channel_id"
        ).fetchall() if _table_exists(conn, "activity_excluded_channels") else []

    for row in config_rows:
        guild_id = int(row[0])
        expected = _normalize(dict(zip(DEFAULT_CONFIG, row[1:])))
        if not has_feature_setting(guild_id, FEATURE_REWARDS):
            set_feature_payload(guild_id, FEATURE_REWARDS, expected, enabled=True)
        if get_activity_reward_config(guild_id) != expected:
            raise RuntimeError(f"activity reward settings mismatch for guild {guild_id}")
    for guild_id, channel_id, reason in exclusion_rows:
        exclude_activity_channel(int(guild_id), int(channel_id), str(reason or ""))

    expected_exclusions = {(int(row[0]), int(row[1])) for row in exclusion_rows}
    for guild_id, channel_id in expected_exclusions:
        if not is_activity_channel_excluded(guild_id, channel_id):
            raise RuntimeError(f"activity exclusion migration mismatch for guild {guild_id}")

    _archive_legacy_table("activity_rewards_config", len(config_rows))
    _archive_legacy_table("activity_excluded_channels", len(exclusion_rows))
    return {"configs": len(config_rows), "exclusions": len(exclusion_rows)}
