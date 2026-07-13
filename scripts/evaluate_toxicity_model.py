from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import MESSAGES_DB, SOCIAL_DB
from core.toxicity_model_service import detect_rule_level, load_toxicity_model, predict_ml_level


def evaluate(sample_size: int = 5000) -> dict:
    model = load_toxicity_model()
    if not model:
        raise RuntimeError("toxicity model is unavailable")

    texts: list[str] = []
    with sqlite3.connect(MESSAGES_DB) as conn:
        texts.extend(
            str(row[0])
            for row in conn.execute(
                "SELECT content FROM user_messages WHERE length(trim(content)) BETWEEN 4 AND 240 ORDER BY created_at ASC LIMIT ?",
                (sample_size,),
            )
        )
    with sqlite3.connect(SOCIAL_DB) as conn:
        positives = [
            (str(text), int(level))
            for text, level in conn.execute(
                "SELECT msg_snippet,level FROM toxicity_log WHERE length(trim(msg_snippet)) > 0"
            )
        ]
        try:
            reviewed = [
                (str(text), int(level))
                for text, level in conn.execute(
                    "SELECT msg_snippet,corrected_level FROM toxicity_ml_feedback"
                )
            ]
        except sqlite3.Error:
            reviewed = []

    matrix = {str(actual): {str(predicted): 0 for predicted in range(4)} for actual in range(4)}
    for text in texts:
        actual = detect_rule_level(text)
        predicted, _, _ = predict_ml_level(text, model)
        matrix[str(actual)][str(predicted)] += 1
    for text, actual in positives:
        predicted, _, _ = predict_ml_level(text, model)
        matrix[str(max(0, min(3, actual)))][str(predicted)] += 1

    reviewed_correct = 0
    for text, actual in reviewed:
        predicted, _, _ = predict_ml_level(text, model)
        reviewed_correct += int(predicted == actual)

    rule_clean = sum(matrix["0"].values())
    rule_clean_ml_positive = sum(matrix["0"][str(level)] for level in (1, 2, 3))
    rule_positive = sum(sum(matrix[str(level)].values()) for level in (1, 2, 3))
    rule_positive_ml_positive = sum(
        matrix[str(actual)][str(predicted)]
        for actual in (1, 2, 3)
        for predicted in (1, 2, 3)
    )
    return {
        "model_version": model.get("model_version", ""),
        "mode": model.get("mode", "shadow"),
        "class_docs": model.get("class_docs", {}),
        "weak_rule_matrix": matrix,
        "rule_clean_ml_positive_rate": round(rule_clean_ml_positive / rule_clean, 6) if rule_clean else 0.0,
        "rule_positive_ml_positive_rate": round(rule_positive_ml_positive / rule_positive, 6) if rule_positive else 0.0,
        "reviewed_examples": len(reviewed),
        "reviewed_accuracy": round(reviewed_correct / len(reviewed), 6) if reviewed else None,
        "note": "Rule agreement is diagnostic only; human-reviewed accuracy gates enforcement.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate toxicity shadow model against rules and reviewed labels")
    parser.add_argument("--sample-size", type=int, default=5000)
    args = parser.parse_args()
    print(json.dumps(evaluate(max(100, args.sample_size)), ensure_ascii=False, indent=2))
