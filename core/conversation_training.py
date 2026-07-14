from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from core.gamer_profile_service import build_gamer_context, normalize_requested_tags


UTC = timezone.utc


def sanitize_training_text(text: str) -> str:
    value = str(text)[:4000]
    value = re.sub(r"https?://\S+", "URL", value)
    value = re.sub(r"<@!?\d+>", "@user", value)
    value = re.sub(r"<@&\d+>", "@role", value)
    value = re.sub(r"<#\d+>", "#channel", value)
    value = re.sub(r"\b\d{15,20}\b", "ID", value)
    value = value.replace("@everyone", "everyone").replace("@here", "here")
    return re.sub(r"\s+", " ", value).strip()


def build_sft_records(
    examples: Iterable[dict[str, object]], system_prompt: str,
) -> list[dict[str, object]]:
    records = []
    seen = set()
    for example in examples:
        user_text = sanitize_training_text(str(example.get("user_text") or ""))
        bot_text = sanitize_training_text(str(example.get("bot_text") or ""))
        if len(user_text) < 2 or len(bot_text) < 2:
            continue
        digest = hashlib.sha256(f"{user_text}\0{bot_text}".encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        profile = example.get("gamer_profile") if isinstance(example.get("gamer_profile"), dict) else {}
        explicit_tags = example.get("gamer_tags") if isinstance(example.get("gamer_tags"), list) else []
        inferred_tags = [
            str(item.get("tag")) for item in profile.get("archetypes", [])
            if isinstance(item, dict) and item.get("tag")
        ]
        cohorts = normalize_requested_tags([*explicit_tags, *inferred_tags])
        gamer_context = build_gamer_context(profile, explicit_tags)
        messages = [{"role": "system", "content": system_prompt.strip()}]
        if gamer_context:
            messages.append({
                "role": "system",
                "content": "Добровольно разрешённый игровой контекст: " + gamer_context,
            })
        messages.extend((
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": bot_text},
        ))
        records.append({"messages": messages, "example_hash": digest, "cohorts": cohorts})
    return records


def dataset_metadata(records: list[dict[str, object]], *, source_database: str) -> dict[str, object]:
    combined = "".join(str(item.get("example_hash") or "") for item in records)
    cohort_counts = Counter(
        str(tag)
        for item in records
        for tag in (item.get("cohorts") if isinstance(item.get("cohorts"), list) else [])
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_model": "Qwen/Qwen3-8B",
        "base_model_license": "Apache-2.0",
        "method": "QLoRA-SFT",
        "examples": len(records),
        "cohorts": dict(sorted(cohort_counts.items())),
        "dataset_sha256": hashlib.sha256(combined.encode("ascii")).hexdigest(),
        "source_database": source_database,
        "privacy": "training_opt_in + self_positive_feedback; ids and mentions redacted",
        "parody_data": "excluded; parody remains Markov-only",
    }
