from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.paths import DATABASES


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def audit_database(name: str, path: str) -> dict[str, Any]:
    db_path = Path(path)
    result: dict[str, Any] = {
        "name": name,
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "integrity": "missing",
        "foreign_key_violations": [],
        "tables": [],
        "error": "",
    }
    if not db_path.exists():
        return result

    conn: sqlite3.Connection | None = None
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        result["integrity"] = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        result["foreign_key_violations"] = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for table_name, create_sql in tables:
            quoted = _quote_identifier(str(table_name))
            columns = [
                {
                    "name": row[1],
                    "type": row[2],
                    "not_null": bool(row[3]),
                    "primary_key": bool(row[5]),
                }
                for row in conn.execute(f"PRAGMA table_info({quoted})")
            ]
            row_count = int(conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            result["tables"].append(
                {
                    "name": table_name,
                    "rows": row_count,
                    "columns": columns,
                    "create_sql": create_sql or "",
                }
            )
    except sqlite3.Error as exc:
        result["integrity"] = "error"
        result["error"] = str(exc)
    finally:
        if conn is not None:
            conn.close()
    return result


def audit_all(databases: dict[str, str] | None = None) -> dict[str, Any]:
    items = databases or DATABASES
    reports = [audit_database(name, path) for name, path in items.items()]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "databases": reports,
        "summary": {
            "configured": len(reports),
            "present": sum(1 for report in reports if report["exists"]),
            "healthy": sum(1 for report in reports if report["integrity"] == "ok"),
            "tables": sum(len(report["tables"]) for report in reports),
            "rows": sum(table["rows"] for report in reports for table in report["tables"]),
        },
    }


def ml_data_manifest(audit: dict[str, Any]) -> dict[str, Any]:
    """Describe local/VPS placement without copying private user data."""
    table_index = {
        (db["name"], table["name"]): table["rows"]
        for db in audit.get("databases", [])
        for table in db.get("tables", [])
    }
    return {
        "generated_at": audit.get("generated_at", ""),
        "policy": {
            "vps": ["collection", "aggregation", "Markov refresh", "lightweight inference"],
            "local_pc": ["GPT training", "embedding/index builds", "batch feature engineering"],
            "transfer": "Only versioned derived artifacts and explicitly synchronized source databases.",
        },
        "datasets": {
            "parody_messages": {
                "database": "messages",
                "table": "user_messages",
                "rows": table_index.get(("messages", "user_messages"), 0),
                "training_location": "local_pc",
            },
            "community_activity": {
                "database": "social",
                "tables": ["daily_message_stats", "voice_daily", "activity_daily"],
                "training_location": "local_pc",
                "inference_location": "vps",
            },
            "phrase_feedback": {
                "database": "parody_ratings",
                "table": "phrase_ratings",
                "rows": table_index.get(("parody_ratings", "phrase_ratings"), 0),
                "training_location": "local_pc",
            },
        },
    }


def write_audit(path: str, databases: dict[str, str] | None = None) -> dict[str, Any]:
    report = audit_all(databases)
    report["ml_manifest"] = ml_data_manifest(report)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

