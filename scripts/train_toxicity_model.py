from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ml_artifacts import register_artifact
from core.paths import MESSAGES_DB, MODELS_DIR, SOCIAL_DB
from core.toxicity_model_service import DEFAULT_BUCKETS, MODEL_SCHEMA_VERSION, detect_rule_level, hashed_features


def _reviewed_examples(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    try:
        return [
            (str(text), max(0, min(3, int(level))))
            for text, level in conn.execute(
                "SELECT msg_snippet, corrected_level FROM toxicity_ml_feedback ORDER BY reviewed_at"
            )
            if str(text).strip()
        ]
    except sqlite3.Error:
        return []


def load_examples(max_clean: int) -> tuple[list[tuple[str, int]], dict[str, int]]:
    positives: list[tuple[str, int]] = []
    reviewed: list[tuple[str, int]] = []
    with sqlite3.connect(SOCIAL_DB) as conn:
        positives = [
            (str(text), max(1, min(3, int(level))))
            for text, level in conn.execute(
                "SELECT msg_snippet, level FROM toxicity_log WHERE length(trim(msg_snippet)) > 0"
            )
        ]
        reviewed = _reviewed_examples(conn)

    clean: list[tuple[str, int]] = []
    with sqlite3.connect(MESSAGES_DB) as conn:
        for (text,) in conn.execute(
            "SELECT content FROM user_messages WHERE length(trim(content)) BETWEEN 4 AND 240 ORDER BY created_at DESC LIMIT ?",
            (max_clean * 8,),
        ):
            value = str(text)
            if detect_rule_level(value) == 0:
                clean.append((value, 0))
                if len(clean) >= max_clean:
                    break

    # Human feedback has the final say for identical snippets.
    by_text = {text: level for text, level in clean + positives}
    by_text.update({text: level for text, level in reviewed})
    examples = list(by_text.items())
    return examples, {"clean": len(clean), "rule_positive": len(positives), "reviewed": len(reviewed)}


def train_model(examples: list[tuple[str, int]], buckets: int = DEFAULT_BUCKETS, alpha: float = 1.0) -> dict:
    class_docs = Counter({level: 0 for level in range(4)})
    feature_counts = {level: Counter() for level in range(4)}
    feature_totals = Counter({level: 0 for level in range(4)})
    for text, level in examples:
        level = max(0, min(3, int(level)))
        features = hashed_features(text, buckets)
        class_docs[level] += 1
        feature_counts[level].update(features)
        feature_totals[level] += sum(features.values())

    total_docs = sum(class_docs.values())
    if total_docs < 50 or class_docs[0] < 20 or sum(class_docs[level] for level in (1, 2, 3)) < 20:
        raise ValueError("need at least 20 clean and 20 toxic examples (50 total)")

    class_log_prior = {}
    feature_log_prob = {}
    for level in range(4):
        class_log_prior[str(level)] = math.log((class_docs[level] + alpha) / (total_docs + 4 * alpha))
        denominator = feature_totals[level] + alpha * buckets
        feature_log_prob[str(level)] = [
            math.log((feature_counts[level].get(bucket, 0) + alpha) / denominator)
            for bucket in range(buckets)
        ]

    trained_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_version": f"tox-nb-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "pipeline": "toxicity_nb_shadow",
        "mode": "shadow",
        "trained_at": trained_at,
        "feature_buckets": buckets,
        "class_docs": {str(level): class_docs[level] for level in range(4)},
        "class_log_prior": class_log_prior,
        "feature_log_prob": feature_log_prob,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the lightweight shadow toxicity classifier")
    parser.add_argument("--max-clean", type=int, default=2000)
    args = parser.parse_args()

    examples, sources = load_examples(max(100, args.max_clean))
    model = train_model(examples)
    model["training_sources"] = sources
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = MODELS_DIR / "toxicity_nb.json"
    temporary = target.with_suffix(f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    register_artifact(
        pipeline="toxicity_nb_shadow",
        user_id=None,
        kind="classifier",
        path=target,
        source_rows=len(examples),
        execution_location="local_pc",
        metadata={"mode": "shadow", "sources": sources},
    )
    print(f"[toxicity-ml] model={model['model_version']} examples={len(examples)} sources={sources}")


if __name__ == "__main__":
    main()
