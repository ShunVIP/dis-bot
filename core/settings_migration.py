from __future__ import annotations

import sqlite3
from typing import Any

from core.paths import BIRTHDAYS_DB, SOCIAL_DB
from core.db import connection as db_connection
from core.settings_store import (
    get_feature_channel_ids,
    has_feature_setting,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
    set_feature_runtime_state,
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _feature_configured(guild_id: int, feature: str) -> bool:
    return (
        has_feature_setting(guild_id, feature)
        or bool(get_feature_channel_ids(guild_id, feature, "output"))
        or bool(get_feature_channel_ids(guild_id, feature, "allow"))
        or bool(get_feature_channel_ids(guild_id, feature, "exclude"))
    )


def _int_set(raw: Any) -> set[int]:
    if not raw:
        return set()
    return {int(x) for x in str(raw).split(",") if x.strip().isdigit()}


def _migrate_daily_summary() -> int:
    count = 0
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "daily_summary_config"):
            return 0
        rows = conn.execute(
            "SELECT guild_id, channel_id, enabled FROM daily_summary_config"
        ).fetchall()
    for guild_id, channel_id, enabled in rows:
        guild_id = int(guild_id)
        if _feature_configured(guild_id, "daily_summary"):
            continue
        set_feature_enabled(guild_id, "daily_summary", bool(enabled))
        if channel_id:
            set_feature_channel(guild_id, "daily_summary", int(channel_id), "output", "legacy migration")
        count += 1
    return count


def _migrate_birthday() -> int:
    count = 0
    with db_connection(BIRTHDAYS_DB) as conn:
        if not _table_exists(conn, "birthday_config"):
            return 0
        rows = conn.execute("SELECT guild_id, channel_id FROM birthday_config").fetchall()
    for guild_id, channel_id in rows:
        guild_id = int(guild_id)
        if _feature_configured(guild_id, "birthday"):
            continue
        set_feature_enabled(guild_id, "birthday", True)
        if channel_id:
            set_feature_channel(guild_id, "birthday", int(channel_id), "output", "legacy migration")
        count += 1
    return count


def _migrate_toxicity() -> int:
    count = 0
    with db_connection(SOCIAL_DB) as conn:
        config_rows = (
            conn.execute("SELECT guild_id, enabled, threshold_lvl, channel_ids FROM toxicity_config").fetchall()
            if _table_exists(conn, "toxicity_config")
            else []
        )
        excluded_rows = (
            conn.execute("SELECT guild_id, channel_id, reason FROM toxicity_excluded_channels").fetchall()
            if _table_exists(conn, "toxicity_excluded_channels")
            else []
        )

    guild_ids = {int(row[0]) for row in config_rows} | {int(row[0]) for row in excluded_rows}
    config_by_guild = {int(row[0]): row for row in config_rows}
    excluded_by_guild: dict[int, list[tuple[int, str]]] = {}
    for guild_id, channel_id, reason in excluded_rows:
        excluded_by_guild.setdefault(int(guild_id), []).append((int(channel_id), str(reason or "")))

    for guild_id in guild_ids:
        if _feature_configured(guild_id, "toxicity"):
            continue
        row = config_by_guild.get(guild_id)
        enabled = bool(row[1]) if row else True
        threshold = int(row[2]) if row and row[2] else 1
        set_feature_enabled(guild_id, "toxicity", enabled)
        set_feature_payload(guild_id, "toxicity", {"threshold": max(1, min(threshold, 3))})
        if row:
            for channel_id in sorted(_int_set(row[3])):
                set_feature_channel(guild_id, "toxicity", channel_id, "allow", "legacy migration")
        for channel_id, reason in excluded_by_guild.get(guild_id, []):
            set_feature_channel(guild_id, "toxicity", channel_id, "exclude", reason or "legacy migration")
        count += 1
    return count


def _migrate_social_chat() -> int:
    count = 0
    with db_connection(SOCIAL_DB) as conn:
        config_rows = (
            conn.execute("SELECT guild_id, enabled, chance_percent, mention_only, channel_ids FROM social_chat_config").fetchall()
            if _table_exists(conn, "social_chat_config")
            else []
        )
        excluded_rows = (
            conn.execute("SELECT guild_id, channel_id, reason FROM social_chat_excluded_channels").fetchall()
            if _table_exists(conn, "social_chat_excluded_channels")
            else []
        )

    guild_ids = {int(row[0]) for row in config_rows} | {int(row[0]) for row in excluded_rows}
    config_by_guild = {int(row[0]): row for row in config_rows}
    excluded_by_guild: dict[int, list[tuple[int, str]]] = {}
    for guild_id, channel_id, reason in excluded_rows:
        excluded_by_guild.setdefault(int(guild_id), []).append((int(channel_id), str(reason or "")))

    for guild_id in guild_ids:
        if _feature_configured(guild_id, "social_chat"):
            continue
        row = config_by_guild.get(guild_id)
        enabled = bool(row[1]) if row else True
        chance = int(row[2]) if row and row[2] is not None else 12
        mention_only = bool(row[3]) if row else False
        set_feature_enabled(guild_id, "social_chat", enabled)
        set_feature_payload(
            guild_id,
            "social_chat",
            {"chance_percent": max(0, min(chance, 100)), "mention_only": mention_only},
        )
        if row:
            for channel_id in sorted(_int_set(row[4])):
                set_feature_channel(guild_id, "social_chat", channel_id, "allow", "legacy migration")
        for channel_id, reason in excluded_by_guild.get(guild_id, []):
            set_feature_channel(guild_id, "social_chat", channel_id, "exclude", reason or "legacy migration")
        count += 1
    return count


def _migrate_voice_roles() -> int:
    count = 0
    with db_connection(SOCIAL_DB) as conn:
        config_rows = (
            conn.execute("SELECT guild_id, enabled FROM voice_roles_config").fetchall()
            if _table_exists(conn, "voice_roles_config")
            else []
        )
        excluded_rows = (
            conn.execute("SELECT guild_id, channel_id, reason FROM voice_roles_excluded_channels").fetchall()
            if _table_exists(conn, "voice_roles_excluded_channels")
            else []
        )
    guild_ids = {int(row[0]) for row in config_rows} | {int(row[0]) for row in excluded_rows}
    enabled_by_guild = {int(row[0]): bool(row[1]) for row in config_rows}
    excluded_by_guild: dict[int, list[tuple[int, str]]] = {}
    for guild_id, channel_id, reason in excluded_rows:
        excluded_by_guild.setdefault(int(guild_id), []).append((int(channel_id), str(reason or "")))

    for guild_id in guild_ids:
        if _feature_configured(guild_id, "voice_roles"):
            continue
        set_feature_enabled(guild_id, "voice_roles", enabled_by_guild.get(guild_id, True))
        for channel_id, reason in excluded_by_guild.get(guild_id, []):
            set_feature_channel(guild_id, "voice_roles", channel_id, "exclude", reason or "legacy migration")
        count += 1
    return count


def _migrate_steam() -> int:
    count = 0
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "steam_config"):
            return 0
        rows = conn.execute("SELECT guild_id, notify_channel, discount_min_pct FROM steam_config").fetchall()
    for guild_id, channel_id, min_pct in rows:
        guild_id = int(guild_id)
        if _feature_configured(guild_id, "steam"):
            continue
        set_feature_enabled(guild_id, "steam", True)
        set_feature_payload(guild_id, "steam", {"discount_min_pct": max(0, min(int(min_pct or 50), 100))})
        if channel_id:
            set_feature_channel(guild_id, "steam", int(channel_id), "output", "legacy migration")
        count += 1
    return count


def _migrate_wwm_guild() -> int:
    count = 0
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "wwm_config"):
            return 0
        rows = conn.execute(
            "SELECT guild_id, welcome_channel_id, reception_channel_id, auto_nickname, nickname_template FROM wwm_config"
        ).fetchall()
    for guild_id, welcome_channel_id, reception_channel_id, auto_nickname, nickname_template in rows:
        guild_id = int(guild_id)
        if _feature_configured(guild_id, "wwm_guild"):
            continue
        set_feature_enabled(guild_id, "wwm_guild", True)
        payload = {
            "reception_channel_id": int(reception_channel_id or 0) or None,
            "auto_nickname": bool(auto_nickname),
            "nickname_template": str(nickname_template or "{game_nick}"),
        }
        set_feature_payload(guild_id, "wwm_guild", payload)
        if welcome_channel_id:
            set_feature_channel(guild_id, "wwm_guild", int(welcome_channel_id), "output", "legacy migration")
        count += 1
    return count


def _migrate_economy_tax(guild_ids: list[int] | tuple[int, ...] | None = None) -> int:
    target_guild_ids = [int(guild_id) for guild_id in (guild_ids or []) if int(guild_id)]
    if not target_guild_ids:
        return 0
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "tax_config"):
            return 0
        row = conn.execute(
            "SELECT enabled, rate_pct, interval_h, last_run FROM tax_config WHERE id=1"
        ).fetchone()
    if not row:
        return 0
    count = 0
    for guild_id in target_guild_ids:
        if _feature_configured(guild_id, "economy"):
            continue
        set_feature_payload(
            guild_id,
            "economy",
            {
                "tax_enabled": bool(row[0]),
                "tax_rate_pct": max(1, min(50, int(row[1] or 10))),
                "tax_interval_h": max(1, min(720, int(row[2] or 168))),
            },
        )
        if row[3]:
            set_feature_runtime_state(guild_id, "economy", {"tax_last_run": str(row[3])})
        count += 1
    return count


def _migrate_activity_tracker() -> int:
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "activity_tracker_config"):
            return 0
        rows = conn.execute(
            """
            SELECT guild_id, channel_id, enabled, notify_starts, notify_ends, article_lookup
            FROM activity_tracker_config
            """
        ).fetchall()
    count = 0
    for guild_id, _channel_id, enabled, _notify_starts, _notify_ends, _article_lookup in rows:
        guild_id = int(guild_id)
        if _feature_configured(guild_id, "activity_tracker"):
            continue
        # Proactive activity posts and habit reminders were retired. Preserve
        # only the silent-tracking switch; obsolete delivery settings stay in
        # the archived legacy table for auditability.
        set_feature_enabled(guild_id, "activity_tracker", bool(enabled))
        count += 1
    return count


def seed_admin_settings_from_legacy(log=None, guild_ids: list[int] | tuple[int, ...] | None = None) -> dict[str, int]:
    results = {
        "daily_summary": _migrate_daily_summary(),
        "birthday": _migrate_birthday(),
        "toxicity": _migrate_toxicity(),
        "social_chat": _migrate_social_chat(),
        "voice_roles": _migrate_voice_roles(),
        "steam": _migrate_steam(),
        "wwm_guild": _migrate_wwm_guild(),
        "economy": _migrate_economy_tax(guild_ids),
        "activity_tracker": _migrate_activity_tracker(),
    }
    migrated = sum(results.values())
    if log:
        log.bind(src="settings").info(
            "Admin settings seeded from legacy: {}",
            ", ".join(f"{key}={value}" for key, value in results.items()),
        )
    return results
