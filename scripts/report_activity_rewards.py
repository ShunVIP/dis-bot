from __future__ import annotations

import json
import sqlite3

from core.paths import SOCIAL_DB


TABLES = (
    "activity_rewards_config",
    "activity_excluded_channels",
    "activity_rewards_config_legacy_backup",
    "activity_excluded_channels_legacy_backup",
    "activity_msg_counter",
    "activity_voice_counter",
)


def build_report() -> dict[str, object]:
    with sqlite3.connect(SOCIAL_DB) as conn:
        present = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            if table in present else 0
            for table in TABLES
        }
        legacy_configs = conn.execute(
            """
            SELECT guild_id, msg_enabled, msg_per_n, msg_coins, msg_rep_per_n, msg_rep,
                   voice_enabled, voice_per_min, voice_coins
            FROM activity_rewards_config ORDER BY guild_id
            """
        ).fetchall() if "activity_rewards_config" in present else []
        legacy_exclusions = conn.execute(
            "SELECT guild_id, channel_id, reason FROM activity_excluded_channels ORDER BY guild_id, channel_id"
        ).fetchall() if "activity_excluded_channels" in present else []
        canonical = conn.execute(
            """
            SELECT guild_id, enabled, payload FROM feature_settings
            WHERE feature='activity_rewards' ORDER BY guild_id
            """
        ).fetchall() if "feature_settings" in present else []
        canonical_exclusions = conn.execute(
            """
            SELECT guild_id, channel_id, reason FROM feature_channels
            WHERE feature='message_stats' AND mode='exclude' ORDER BY guild_id, channel_id
            """
        ).fetchall() if "feature_channels" in present else []
    return {
        "database": SOCIAL_DB,
        "present": {table: table in present for table in TABLES},
        "counts": counts,
        "legacy_configs": [list(row) for row in legacy_configs],
        "legacy_exclusions": [list(row) for row in legacy_exclusions],
        "canonical": [
            {"guild_id": int(row[0]), "enabled": bool(row[1]), "payload": json.loads(row[2] or "{}")}
            for row in canonical
        ],
        "canonical_exclusions": [list(row) for row in canonical_exclusions],
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
