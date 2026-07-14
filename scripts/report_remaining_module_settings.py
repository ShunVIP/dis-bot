from __future__ import annotations

import json
import sqlite3

from core.paths import SOCIAL_DB


TABLES = (
    "heroes_troll_config",
    "heroes_troll_config_legacy_backup",
    "heroes_sessions",
    "heroes_active_sessions",
    "rep_roles_config",
    "rep_roles_config_legacy_backup",
    "rep_role_thresholds",
    "rep_roles_active",
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
        heroes = conn.execute(
            "SELECT guild_id, channel_id FROM heroes_troll_config ORDER BY guild_id"
        ).fetchall() if "heroes_troll_config" in present else []
        rep_roles = conn.execute(
            "SELECT guild_id, enabled FROM rep_roles_config ORDER BY guild_id"
        ).fetchall() if "rep_roles_config" in present else []
        canonical = conn.execute(
            """
            SELECT guild_id, feature, enabled, payload FROM feature_settings
            WHERE feature IN ('heroes_troll', 'rep_roles') ORDER BY guild_id, feature
            """
        ).fetchall() if "feature_settings" in present else []
        channels = conn.execute(
            """
            SELECT guild_id, feature, channel_id, mode, reason FROM feature_channels
            WHERE feature IN ('heroes_troll', 'rep_roles') ORDER BY guild_id, feature, mode, channel_id
            """
        ).fetchall() if "feature_channels" in present else []
    return {
        "database": SOCIAL_DB,
        "present": {table: table in present for table in TABLES},
        "counts": counts,
        "legacy": {
            "heroes_troll_config": [list(row) for row in heroes],
            "rep_roles_config": [list(row) for row in rep_roles],
        },
        "canonical": [
            {
                "guild_id": int(row[0]),
                "feature": row[1],
                "enabled": bool(row[2]),
                "payload": json.loads(row[3] or "{}"),
            }
            for row in canonical
        ],
        "channels": [list(row) for row in channels],
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
