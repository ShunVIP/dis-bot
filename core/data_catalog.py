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
    from core.ml_artifacts import MANIFEST_PATH, load_artifact_manifest

    artifact_manifest = load_artifact_manifest(verify_files=True)
    artifacts = artifact_manifest.get("artifacts", [])
    table_index = {
        (db["name"], table["name"]): table["rows"]
        for db in audit.get("databases", [])
        for table in db.get("tables", [])
    }
    return {
        "generated_at": audit.get("generated_at", ""),
        "policy": {
            "vps": ["collection", "aggregation", "Markov refresh", "lightweight inference"],
            "local_pc": ["ML training", "embedding/index builds", "batch feature engineering"],
            "transfer": "Only versioned derived artifacts and explicitly synchronized source databases.",
        },
        "artifact_registry": {
            "path": str(MANIFEST_PATH),
            "schema_version": artifact_manifest.get("schema_version", 0),
            "updated_at": artifact_manifest.get("updated_at", ""),
            "artifacts": len(artifacts),
            "available": sum(1 for item in artifacts if item.get("available")),
            "missing": sum(1 for item in artifacts if not item.get("available")),
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
                "tables": ["msg_stats_daily", "voice_totals_daily", "activity_sessions"],
                "training_location": "local_pc",
                "inference_location": "vps",
            },
            "phrase_feedback": {
                "database": "parody_ratings",
                "table": "phrase_ratings",
                "rows": table_index.get(("parody_ratings", "phrase_ratings"), 0),
                "training_location": "local_pc",
            },
            "toxicity_shadow": {
                "database": "social",
                "tables": ["toxicity_log", "toxicity_ml_shadow", "toxicity_ml_feedback"],
                "training_location": "local_pc_or_private_vps",
                "inference_location": "vps",
                "enforcement": "rules_only",
            },
            "conversation_feedback": {
                "database": "social",
                "tables": ["conversation_turns", "conversation_feedback", "conversation_preferences", "gamer_profiles", "conversation_runtime_status"],
                "rows": table_index.get(("social", "conversation_turns"), 0),
                "rated_rows": table_index.get(("social", "conversation_feedback"), 0),
                "training_location": "local_pc",
                "inference_location": "local_pc_via_tailscale",
                "collection_policy": "explicit_bot_conversations_only",
                "training_policy": "user_training_opt_in + self_positive_feedback",
                "training_provider": "ollama_only_markov_excluded",
                "personalization_policy": "user_memory_opt_in",
                "base_model": "Qwen/Qwen3-8B",
                "adaptation": "local_pc_qlora",
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


def repair_wwm_orphan_features(path: str) -> dict[str, int]:
    """Archive and remove only feature rows whose parent entity is missing."""
    from core.db import connection as db_connection

    with db_connection(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orphan_entity_features_backup (
                entity_id INTEGER PRIMARY KEY,
                predicted_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                snippet_en TEXT,
                keywords_json TEXT,
                updated_at TEXT NOT NULL,
                archived_at TEXT NOT NULL
            )
            """
        )
        orphan_where = "entity_id NOT IN (SELECT entity_id FROM entities)"
        before = int(conn.execute(f"SELECT COUNT(*) FROM entity_features WHERE {orphan_where}").fetchone()[0])
        archived_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            f"""
            INSERT OR REPLACE INTO orphan_entity_features_backup(
                entity_id, predicted_type, confidence, snippet_en, keywords_json, updated_at, archived_at
            )
            SELECT entity_id, predicted_type, confidence, snippet_en, keywords_json, updated_at, ?
            FROM entity_features WHERE {orphan_where}
            """,
            (archived_at,),
        )
        deleted = conn.execute(f"DELETE FROM entity_features WHERE {orphan_where}").rowcount
        remaining = int(conn.execute(f"SELECT COUNT(*) FROM entity_features WHERE {orphan_where}").fetchone()[0])
    return {"found": before, "archived": before, "deleted": int(deleted), "remaining": remaining}
