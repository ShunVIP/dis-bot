from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import SOCIAL_DB


TABLES = (
    "activity_tracker_config",
    "activity_game_habits",
    "activity_game_habits_retired_backup",
    "conversation_turns",
    "conversation_feedback",
    "conversation_preferences",
    "conversation_runtime_status",
    "gamer_profiles",
    "toxicity_log",
    "toxicity_ml_shadow",
    "toxicity_ml_feedback",
)


def build_report(path: str = SOCIAL_DB) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        rows = {
            table: int(conn.execute(f'SELECT COUNT(1) FROM "{table}"').fetchone()[0])
            if table in names
            else 0
            for table in TABLES
        }
        toxicity_levels = (
            {
                str(level): int(count)
                for level, count in conn.execute(
                    "SELECT corrected_level,COUNT(1) FROM toxicity_ml_feedback GROUP BY corrected_level"
                )
            }
            if "toxicity_ml_feedback" in names
            else {}
        )
        conversation_scores = (
            {
                str(score): int(count)
                for score, count in conn.execute(
                    "SELECT score,COUNT(1) FROM conversation_feedback GROUP BY score"
                )
            }
            if "conversation_feedback" in names
            else {}
        )
        consent = {"memory_users": 0, "training_users": 0, "approved_examples": 0}
        if "conversation_preferences" in names:
            consent["memory_users"], consent["training_users"] = map(int, conn.execute(
                "SELECT COALESCE(SUM(memory_opt_in),0),COALESCE(SUM(training_opt_in),0) FROM conversation_preferences"
            ).fetchone())
        if {"conversation_turns", "conversation_feedback", "conversation_preferences"}.issubset(names):
            consent["approved_examples"] = int(conn.execute(
                """
                SELECT COUNT(1) FROM conversation_turns t
                JOIN conversation_preferences p ON p.user_id=t.user_id AND p.training_opt_in=1
                JOIN conversation_feedback f ON f.bot_message_id=t.bot_message_id
                    AND f.reviewer_user_id=t.user_id AND f.score=1
                WHERE t.provider='ollama'
                """
            ).fetchone()[0])
    return {
        "database": str(Path(path).resolve()),
        "rows": rows,
        "toxicity_feedback_levels": toxicity_levels,
        "conversation_feedback_scores": conversation_scores,
        "conversation_consent": consent,
        "toxicity_enforcement_ready": rows["toxicity_ml_feedback"] >= 500
        and all(int(toxicity_levels.get(str(level), 0)) >= 50 for level in range(4)),
        "conversation_finetune_ready": consent["approved_examples"] >= 50,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report ML feedback readiness without exporting message text")
    parser.add_argument("--db", default=SOCIAL_DB)
    args = parser.parse_args()
    print(json.dumps(build_report(args.db), ensure_ascii=False, indent=2))
