from __future__ import annotations

from typing import Any

from core.platform_store import list_platform_audit, record_platform_audit
from core.toxicity_store import (
    count_toxicity_feedback,
    list_pending_shadow_samples,
    save_toxicity_feedback,
    toxicity_storage_counts,
)


TOXICITY_LEVEL_LABELS = {
    0: "норма",
    1: "подкол",
    2: "токсично",
    3: "жёстко",
}


def moderation_overview(limit: int = 50) -> dict[str, Any]:
    """Build the admin moderation dashboard without exposing store tuples to HTTP/UI."""
    clean_limit = max(1, min(int(limit), 100))
    counts = toxicity_storage_counts()
    pending = [
        {
            "message_id": message_id,
            "rule_level": rule_level,
            "ml_level": ml_level,
            "ml_confidence": confidence,
            "model_version": model_version,
            "snippet": snippet,
        }
        for message_id, rule_level, ml_level, confidence, model_version, snippet
        in list_pending_shadow_samples(clean_limit)
    ]
    feedback_count = count_toxicity_feedback()
    pending_count = max(0, counts.get("toxicity_ml_shadow", 0) - feedback_count)
    return {
        "summary": {
            "events": counts.get("toxicity_log", 0),
            "shadow_samples": counts.get("toxicity_ml_shadow", 0),
            "pending_samples": pending_count,
            "reviewed_samples": feedback_count,
            "enforcement": "rules_only",
        },
        "levels": [
            {"level": level, "label": label}
            for level, label in TOXICITY_LEVEL_LABELS.items()
        ],
        "pending": pending,
        "audit": list_platform_audit(clean_limit),
    }


def review_toxicity_sample(message_id: int, corrected_level: int, reviewer_id: int) -> bool:
    level = int(corrected_level)
    if level not in TOXICITY_LEVEL_LABELS:
        raise ValueError("corrected_level must be between 0 and 3")
    saved = save_toxicity_feedback(message_id, level, reviewer_id)
    if saved:
        record_platform_audit(
            reviewer_id,
            "toxicity.feedback",
            "message",
            message_id,
            {"corrected_level": level, "label": TOXICITY_LEVEL_LABELS[level]},
        )
    return saved
