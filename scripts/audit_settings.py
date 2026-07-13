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
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _rows(conn: sqlite3.Connection, queries: dict[str, str]) -> dict[str, list[list[Any]]]:
    return {
        table: [list(row) for row in conn.execute(query).fetchall()] if _table_exists(conn, table) else []
        for table, query in queries.items()
    }


def build_report() -> dict[str, Any]:
    with sqlite3.connect(SOCIAL_DB) as conn:
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

    with sqlite3.connect(BIRTHDAYS_DB) as conn:
        birthdays_legacy = _rows(conn, {"birthday_config": "SELECT guild_id, channel_id FROM birthday_config"})

    return {
        "current": {"settings": current_settings, "channels": current_channels},
        "legacy": {**social_legacy, **birthdays_legacy},
    }


def main() -> int:
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
