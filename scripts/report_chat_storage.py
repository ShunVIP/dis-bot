from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from core.paths import SOCIAL_DB


TABLES = (
    "web_chat_messages",
    "web_chat_reactions",
    "web_bot_outbox",
    "web_chat_messages_retired_backup",
    "web_chat_reactions_retired_backup",
    "web_bot_outbox_retired_backup",
    "platform_messages",
    "platform_message_reactions",
    "platform_discord_outbox",
    "platform_web_chat_migration",
    "platform_web_outbox_migration",
    "platform_text_channels",
    "platform_dm_threads",
    "platform_dm_reads",
    "platform_rate_events",
    "platform_audit_log",
)


def _count(conn: sqlite3.Connection, table: str) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def build_report(path: str = SOCIAL_DB) -> dict[str, object]:
    database = str(Path(path).resolve())
    with closing(sqlite3.connect(database)) as conn:
        existing_tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts = {table: _count(conn, table) for table in TABLES}
        channels = conn.execute(
            "SELECT id, name FROM platform_text_channels ORDER BY id"
        ).fetchall() if counts["platform_text_channels"] else []
        web_sources = conn.execute(
            "SELECT source, COUNT(*) FROM web_chat_messages GROUP BY source"
        ).fetchall() if counts["web_chat_messages"] else []
        platform_scopes = conn.execute(
            "SELECT scope, COUNT(*) FROM platform_messages GROUP BY scope"
        ).fetchall() if counts["platform_messages"] else []
    return {
        "database": database,
        "present": {table: table in existing_tables for table in TABLES},
        "counts": counts,
        "channels": [{"id": row[0], "name": row[1]} for row in channels],
        "web_sources": dict(web_sources),
        "platform_scopes": dict(platform_scopes),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only chat/DM storage report")
    parser.add_argument("--db", default=SOCIAL_DB)
    args = parser.parse_args()
    print(json.dumps(build_report(args.db), ensure_ascii=False, indent=2))
