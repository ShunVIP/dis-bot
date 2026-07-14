from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db import connection
from core.paths import BIRTHDAYS_DB, SOCIAL_DB
from scripts.audit_settings import build_report


SOCIAL_TABLES = (
    "daily_summary_config",
    "toxicity_config",
    "toxicity_excluded_channels",
    "social_chat_config",
    "social_chat_excluded_channels",
    "voice_roles_config",
    "voice_roles_excluded_channels",
    "steam_config",
    "wwm_config",
    "tax_config",
    "activity_tracker_config",
)
BIRTHDAY_TABLES = ("birthday_config",)
BACKUP_SUFFIX = "_legacy_backup"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _archive_tables(database: str, tables: tuple[str, ...], *, apply: bool) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    with connection(database) as conn:
        for table in tables:
            backup = f"{table}{BACKUP_SUFFIX}"
            if not _table_exists(conn, table):
                continue
            if _table_exists(conn, backup):
                raise RuntimeError(f"backup table already exists: {backup}")
            rows = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            actions.append({"table": table, "backup": backup, "rows": rows})
            if apply:
                conn.execute(f'ALTER TABLE "{table}" RENAME TO "{backup}"')
        if apply and actions and database == SOCIAL_DB:
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
            now = datetime.now(timezone.utc).isoformat()
            conn.executemany(
                """
                INSERT INTO settings_migration_archive(table_name, backup_table, source_rows, archived_at)
                VALUES(?, ?, ?, ?)
                """,
                [(item["table"], item["backup"], item["rows"], now) for item in actions],
            )
    return actions


def finalize(*, apply: bool = False) -> dict[str, object]:
    audit = build_report()
    coverage = audit["coverage"]
    if not coverage["safe_to_finalize"]:
        raise RuntimeError("settings migration coverage is incomplete: " + "; ".join(coverage["issues"]))
    actions = {
        "social": _archive_tables(SOCIAL_DB, SOCIAL_TABLES, apply=False),
        "birthdays": _archive_tables(BIRTHDAYS_DB, BIRTHDAY_TABLES, apply=False),
    }
    if apply:
        actions = {
            "social": _archive_tables(SOCIAL_DB, SOCIAL_TABLES, apply=True),
            "birthdays": _archive_tables(BIRTHDAYS_DB, BIRTHDAY_TABLES, apply=True),
        }
    return {"applied": apply, "coverage": coverage, "actions": actions}


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive verified legacy settings tables")
    parser.add_argument("--apply", action="store_true", help="Rename legacy tables after coverage validation")
    args = parser.parse_args()
    try:
        result = finalize(apply=args.apply)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
