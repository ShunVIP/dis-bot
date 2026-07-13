from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from core.paths import MODELS_DIR


MODEL_PATH = MODELS_DIR / "toxicity_nb.json"
MODEL_SCHEMA_VERSION = 1
DEFAULT_BUCKETS = 4096

TOXIC_PATTERNS = {
    1: [
        r"\bтупой\b", r"\bидиот\b", r"\bдебил\b", r"\bкретин\b", r"\bлох\b", r"\bнуб\b",
        r"\bноуб\b", r"\bзалупа\b", r"\bурод\b", r"\bуродли\w+", r"\bмудак\b", r"\bпридурок\b",
        r"\bшлюх\w*\b", r"\bпошёл\s+нах\w*", r"\bиди\s+нах\w*", r"\bнуб\w*\b", r"\bказёл\b",
        r"\bкозёл\b", r"\bбля\w*\b", r"\bговно\b", r"\bжопа\b", r"\bхуй\b", r"\bбнс\b",
        r"\bbns\b", r"\bкодзима\b", r"\bгений\b",
    ],
    2: [
        r"\bеба\w+\b", r"\bёба\w+\b", r"\bеблан\b", r"\bёблан\b", r"\bпиздёж\b", r"\bпиздёт\b",
        r"\bпиздун\b", r"\bзаткнись\b", r"\bзаткни\s+пасть", r"\bты\s+отстой\b", r"\bты\s+дно\b",
        r"\bпроиграл\s+в\s+жизни", r"\bнеудачник\b", r"\bсосёшь\b", r"\bсоси\b", r"\bебну\w*\b",
        r"\bёбну\w*\b",
    ],
    3: [
        r"\bпошёл\s+нахуй\b", r"\bиди\s+нахуй\b", r"\bпиздец\s+тебе\b", r"\bубью\b",
        r"\bубить\s+тебя", r"\bсдохни\b", r"\bсдохнешь\b",
    ],
}

_COMPILED = {
    level: [re.compile(pattern, re.IGNORECASE | re.UNICODE) for pattern in patterns]
    for level, patterns in TOXIC_PATTERNS.items()
}
_CACHE_LOCK = threading.RLock()
_CACHE_MTIME_NS = -1
_CACHE_MODEL: dict[str, Any] | None = None


def detect_rule_level(text: str) -> int:
    for level in (3, 2, 1):
        if any(pattern.search(text) for pattern in _COMPILED[level]):
            return level
    return 0


def _normalized_text(text: str) -> str:
    value = str(text).lower().replace("ё", "е")[:2000]
    value = re.sub(r"https?://\S+", " URL ", value)
    value = re.sub(r"<[@#!&][^>]+>", " MENTION ", value)
    return re.sub(r"\s+", " ", value).strip()


def hashed_features(text: str, buckets: int = DEFAULT_BUCKETS) -> Counter[int]:
    value = _normalized_text(text)
    features: Counter[int] = Counter()
    if not value:
        return features
    padded = f"^^{value}$$"
    for size in (3, 4, 5):
        for index in range(max(0, len(padded) - size + 1)):
            gram = padded[index:index + size].encode("utf-8")
            bucket = int.from_bytes(hashlib.blake2b(gram, digest_size=8).digest(), "big") % int(buckets)
            features[bucket] += 1
    return features


def _validate_model(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict) or int(data.get("schema_version") or 0) != MODEL_SCHEMA_VERSION:
        return None
    buckets = int(data.get("feature_buckets") or 0)
    probabilities = data.get("feature_log_prob")
    priors = data.get("class_log_prior")
    if buckets <= 0 or not isinstance(probabilities, dict) or not isinstance(priors, dict):
        return None
    for level in range(4):
        key = str(level)
        if key not in priors or not isinstance(probabilities.get(key), list) or len(probabilities[key]) != buckets:
            return None
    return data


def load_toxicity_model() -> dict[str, Any] | None:
    global _CACHE_MODEL, _CACHE_MTIME_NS
    try:
        mtime_ns = MODEL_PATH.stat().st_mtime_ns
    except OSError:
        return None
    with _CACHE_LOCK:
        if _CACHE_MODEL is not None and _CACHE_MTIME_NS == mtime_ns:
            return _CACHE_MODEL
        try:
            data = _validate_model(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            data = None
        _CACHE_MODEL = data
        _CACHE_MTIME_NS = mtime_ns
        return data


def predict_ml_level(text: str, model: dict[str, Any] | None = None) -> tuple[int, float, str]:
    active = model or load_toxicity_model()
    if not active:
        return 0, 0.0, ""
    buckets = int(active["feature_buckets"])
    features = hashed_features(text, buckets)
    scores = {}
    for level in range(4):
        key = str(level)
        score = float(active["class_log_prior"][key])
        probabilities = active["feature_log_prob"][key]
        score += sum(count * float(probabilities[bucket]) for bucket, count in features.items())
        scores[level] = score
    best_level = max(scores, key=scores.get)
    peak = scores[best_level]
    denominator = sum(math.exp(value - peak) for value in scores.values())
    confidence = 1.0 / denominator if denominator else 0.0
    return int(best_level), round(confidence, 6), str(active.get("model_version") or "")


def detect_toxicity(text: str) -> dict[str, Any]:
    rule_level = detect_rule_level(text)
    ml_level, confidence, version = predict_ml_level(text)
    return {
        "rule_level": rule_level,
        "ml_level": ml_level,
        "ml_confidence": confidence,
        "model_version": version,
        "effective_level": rule_level,
        "mode": "shadow" if version else "rules_only",
    }


def reset_model_cache() -> None:
    global _CACHE_MODEL, _CACHE_MTIME_NS
    with _CACHE_LOCK:
        _CACHE_MODEL = None
        _CACHE_MTIME_NS = -1
