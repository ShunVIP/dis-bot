from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import BIRTHDAYS_DB, SOCIAL_DB
from core.db import connection
from core.settings_store import ensure_settings_tables


SOCIAL_LEGACY_TABLES = {
    "daily_summary_config": "SELECT guild_id, channel_id, enabled FROM daily_summary_config",
    "toxicity_config": "SELECT guild_id, enabled, threshold_lvl, channel_ids FROM toxicity_config",
    "toxicity_excluded_channels": "SELECT guild_id, channel_id, reason FROM toxicity_excluded_channels",
    "social_chat_config": "SELECT guild_id, enabled, chance_percent, mention_only, channel_ids FROM social_chat_config",
    "social_chat_excluded_channels": "SELECT guild_id, channel_id, reason FROM social_chat_excluded_channels",
    "voice_roles_config": "SELECT guild_id, enabled FROM voice_roles_config",
    "voice_roles_excluded_channels": "SELECT guild_id, channel_id, reason FROM voice_roles_excluded_channels",
    "steam_config": "SELECT guild_id, notify_channel, discount_min_pct FROM steam_config",
    "wwm_config": "SELECT guild_id, welcome_channel_id, reception_channel_id, auto_nickname, nickname_template FROM wwm_config",
    "tax_config": "SELECT id, enabled, rate_pct, interval_h, last_run FROM tax_config",
    "activity_tracker_config": (
        "SELECT guild_id, channel_id, enabled, notify_starts, notify_ends, article_lookup "
        "FROM activity_tracker_config"
    ),
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _rows(conn: sqlite3.Connection, queries: dict[str, str]) -> dict[str, list[list[Any]]]:
    return {
        table: [list(row) for row in conn.execute(query).fetchall()] if _table_exists(conn, table) else []
        for table, query in queries.items()
    }


def _table_state(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, dict[str, bool]]:
    return {
        table: {
            "active": _table_exists(conn, table),
            "backup": _table_exists(conn, f"{table}_legacy_backup"),
        }
        for table in tables
    }


def build_report() -> dict[str, Any]:
    ensure_settings_tables()
    with connection(SOCIAL_DB) as conn:
        current_settings = [
            {
                "guild_id": int(guild_id),
                "feature": feature,
                "enabled": bool(enabled),
                "payload": json.loads(payload or "{}"),
            }
            for guild_id, feature, enabled, payload in conn.execute(
                "SELECT guild_id, feature, enabled, payload FROM feature_settings ORDER BY guild_id, feature"
            )
        ]
        current_channels = [
            {
                "guild_id": int(guild_id),
                "feature": feature,
                "channel_id": int(channel_id),
                "mode": mode,
                "reason": reason,
            }
            for guild_id, feature, channel_id, mode, reason in conn.execute(
                "SELECT guild_id, feature, channel_id, mode, reason FROM feature_channels ORDER BY guild_id, feature, mode, channel_id"
            )
        ]
        social_legacy = _rows(conn, SOCIAL_LEGACY_TABLES)
        social_tables = _table_state(conn, tuple(SOCIAL_LEGACY_TABLES))

    with connection(BIRTHDAYS_DB) as conn:
        birthdays_legacy = _rows(conn, {"birthday_config": "SELECT guild_id, channel_id FROM birthday_config"})
        birthday_tables = _table_state(conn, ("birthday_config",))

    report = {
        "current": {"settings": current_settings, "channels": current_channels},
        "legacy": {**social_legacy, **birthdays_legacy},
        "tables": {**social_tables, **birthday_tables},
    }
    report["coverage"] = analyze_coverage(report)
    return report


def _int_set(raw: Any) -> set[int]:
    return {int(value) for value in str(raw or "").split(",") if value.strip().isdigit()}


def analyze_coverage(report: dict[str, Any]) -> dict[str, Any]:
    current = report.get("current", {})
    legacy = report.get("legacy", {})
    settings = {
        (int(row["guild_id"]), str(row["feature"])): row
        for row in current.get("settings", [])
    }
    channels: dict[tuple[int, str, str], set[int]] = {}
    for row in current.get("channels", []):
        key = (int(row["guild_id"]), str(row["feature"]), str(row["mode"]))
        channels.setdefault(key, set()).add(int(row["channel_id"]))
    issues: list[str] = []

    def setting(guild_id: int, feature: str) -> dict[str, Any] | None:
        row = settings.get((int(guild_id), feature))
        if not row:
            issues.append(f"missing setting {feature} for guild {guild_id}")
        return row

    def expect_channels(guild_id: int, feature: str, mode: str, expected: set[int]) -> None:
        actual = channels.get((int(guild_id), feature, mode), set())
        if actual != expected:
            issues.append(
                f"channel mismatch {feature}.{mode} guild {guild_id}: "
                f"expected {sorted(expected)}, got {sorted(actual)}"
            )

    for guild_id, channel_id, enabled in legacy.get("daily_summary_config", []):
        row = setting(guild_id, "daily_summary")
        if row and bool(row["enabled"]) != bool(enabled):
            issues.append(f"enabled mismatch daily_summary guild {guild_id}")
        expect_channels(guild_id, "daily_summary", "output", {int(channel_id)} if channel_id else set())

    for guild_id, channel_id in legacy.get("birthday_config", []):
        setting(guild_id, "birthday")
        expect_channels(guild_id, "birthday", "output", {int(channel_id)} if channel_id else set())

    toxicity_excluded: dict[int, set[int]] = {}
    for guild_id, channel_id, _reason in legacy.get("toxicity_excluded_channels", []):
        toxicity_excluded.setdefault(int(guild_id), set()).add(int(channel_id))
    for guild_id, enabled, threshold, channel_ids in legacy.get("toxicity_config", []):
        row = setting(guild_id, "toxicity")
        if row:
            if bool(row["enabled"]) != bool(enabled):
                issues.append(f"enabled mismatch toxicity guild {guild_id}")
            if int(row.get("payload", {}).get("threshold", 1)) != max(1, min(int(threshold or 1), 3)):
                issues.append(f"payload mismatch toxicity guild {guild_id}")
        expect_channels(guild_id, "toxicity", "allow", _int_set(channel_ids))
    for guild_id, expected in toxicity_excluded.items():
        setting(guild_id, "toxicity")
        expect_channels(guild_id, "toxicity", "exclude", expected)

    social_excluded: dict[int, set[int]] = {}
    for guild_id, channel_id, _reason in legacy.get("social_chat_excluded_channels", []):
        social_excluded.setdefault(int(guild_id), set()).add(int(channel_id))
    for guild_id, enabled, chance, mention_only, channel_ids in legacy.get("social_chat_config", []):
        row = setting(guild_id, "social_chat")
        if row:
            payload = row.get("payload", {})
            if bool(row["enabled"]) != bool(enabled):
                issues.append(f"enabled mismatch social_chat guild {guild_id}")
            if int(payload.get("chance_percent", 12)) != max(0, min(int(chance or 0), 100)) or bool(
                payload.get("mention_only", False)
            ) != bool(mention_only):
                issues.append(f"payload mismatch social_chat guild {guild_id}")
        expect_channels(guild_id, "social_chat", "allow", _int_set(channel_ids))
    for guild_id, expected in social_excluded.items():
        setting(guild_id, "social_chat")
        expect_channels(guild_id, "social_chat", "exclude", expected)

    voice_excluded: dict[int, set[int]] = {}
    for guild_id, channel_id, _reason in legacy.get("voice_roles_excluded_channels", []):
        voice_excluded.setdefault(int(guild_id), set()).add(int(channel_id))
    for guild_id, enabled in legacy.get("voice_roles_config", []):
        row = setting(guild_id, "voice_roles")
        if row and bool(row["enabled"]) != bool(enabled):
            issues.append(f"enabled mismatch voice_roles guild {guild_id}")
    for guild_id, expected in voice_excluded.items():
        setting(guild_id, "voice_roles")
        expect_channels(guild_id, "voice_roles", "exclude", expected)

    for guild_id, channel_id, min_pct in legacy.get("steam_config", []):
        row = setting(guild_id, "steam")
        if row and int(row.get("payload", {}).get("discount_min_pct", 50)) != max(0, min(int(min_pct or 50), 100)):
            issues.append(f"payload mismatch steam guild {guild_id}")
        expect_channels(guild_id, "steam", "output", {int(channel_id)} if channel_id else set())

    for guild_id, welcome, reception, auto_nickname, template in legacy.get("wwm_config", []):
        row = setting(guild_id, "wwm_guild")
        if row:
            payload = row.get("payload", {})
            expected_payload = {
                "reception_channel_id": int(reception or 0) or None,
                "auto_nickname": bool(auto_nickname),
                "nickname_template": str(template or "{game_nick}"),
            }
            if any(payload.get(key) != value for key, value in expected_payload.items()):
                issues.append(f"payload mismatch wwm_guild guild {guild_id}")
        expect_channels(guild_id, "wwm_guild", "output", {int(welcome)} if welcome else set())

    tax_rows = legacy.get("tax_config", [])
    if tax_rows:
        _row_id, enabled, rate, interval, _last_run = tax_rows[0]
        economy_rows = [row for (guild_id, feature), row in settings.items() if feature == "economy"]
        if not economy_rows:
            issues.append("missing migrated economy setting for tax_config")
        for row in economy_rows:
            payload = row.get("payload", {})
            expected = {
                "tax_enabled": bool(enabled),
                "tax_rate_pct": max(1, min(50, int(rate or 10))),
                "tax_interval_h": max(1, min(720, int(interval or 168))),
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                issues.append(f"payload mismatch economy guild {row['guild_id']}")

    for guild_id, _channel_id, enabled, _notify_starts, _notify_ends, _article_lookup in legacy.get(
        "activity_tracker_config", []
    ):
        row = setting(guild_id, "activity_tracker")
        if row:
            if bool(row["enabled"]) != bool(enabled):
                issues.append(f"enabled mismatch activity_tracker guild {guild_id}")

    legacy_rows = sum(len(rows) for rows in legacy.values())
    tables = report.get("tables", {})
    active_tables = sorted(table for table, state in tables.items() if state.get("active"))
    backup_tables = sorted(table for table, state in tables.items() if state.get("backup"))
    return {
        "legacy_rows": legacy_rows,
        "active_tables": active_tables,
        "backup_tables": backup_tables,
        "issues": issues,
        "safe_to_finalize": bool(active_tables) and not issues,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["coverage"]["issues"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
