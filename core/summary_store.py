from __future__ import annotations

from datetime import datetime, timezone

from core.db import connection
from core.paths import SOCIAL_DB


def ensure_summary_tables() -> None:
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summary_post_log (
                guild_id INTEGER NOT NULL,
                summary_type TEXT NOT NULL,
                period_key TEXT NOT NULL,
                posted_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, summary_type, period_key)
            )
            """
        )


def mark_summary_posted(guild_id: int, summary_type: str, period_key: str) -> bool:
    ensure_summary_tables()
    with connection(SOCIAL_DB) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO summary_post_log(guild_id, summary_type, period_key, posted_at)
            VALUES(?, ?, ?, ?)
            """,
            (int(guild_id), str(summary_type), str(period_key), datetime.now(timezone.utc).isoformat()),
        )
    return cursor.rowcount == 1


def was_summary_posted(guild_id: int, summary_type: str, period_key: str) -> bool:
    ensure_summary_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM summary_post_log
            WHERE guild_id=? AND summary_type=? AND period_key=?
            """,
            (int(guild_id), str(summary_type), str(period_key)),
        ).fetchone()
    return bool(row)
