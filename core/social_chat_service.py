from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.settings_store import (
    get_feature_policy,
    list_feature_settings,
    set_feature_payload,
)


FEATURE_SOCIAL_CHAT = "social_chat"
POLICY_VERSION = 2


@dataclass(frozen=True)
class SocialChatPolicy:
    enabled: bool
    chance_percent: int
    mention_only: bool
    ambient_opt_in: bool
    allowed_channel_ids: frozenset[int]
    excluded_channel_ids: frozenset[int]


def _bounded_chance(value: object) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def normalize_social_chat_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Make unsolicited replies impossible without an explicit v2 opt-in."""
    normalized = dict(payload or {})
    ambient_opt_in = bool(normalized.get("ambient_opt_in", False))
    mention_only = bool(normalized.get("mention_only", True)) or not ambient_opt_in
    chance_percent = _bounded_chance(normalized.get("chance_percent", 0))
    if mention_only or not ambient_opt_in:
        chance_percent = 0
    normalized.update({
        "chance_percent": chance_percent,
        "mention_only": mention_only,
        "ambient_opt_in": ambient_opt_in,
        "policy_version": POLICY_VERSION,
    })
    return normalized


def get_social_chat_policy(guild_id: int) -> SocialChatPolicy:
    policy = get_feature_policy(guild_id, FEATURE_SOCIAL_CHAT)
    payload = normalize_social_chat_payload(policy.extra or {})
    return SocialChatPolicy(
        enabled=policy.enabled,
        chance_percent=int(payload["chance_percent"]),
        mention_only=bool(payload["mention_only"]),
        ambient_opt_in=bool(payload["ambient_opt_in"]),
        allowed_channel_ids=frozenset(policy.allowed_channel_ids),
        excluded_channel_ids=frozenset(policy.excluded_channel_ids),
    )


def update_social_chat_policy(
    guild_id: int,
    *,
    ambient_opt_in: bool | None = None,
    chance_percent: int | None = None,
) -> SocialChatPolicy:
    current = get_feature_policy(guild_id, FEATURE_SOCIAL_CHAT)
    payload = dict(current.extra or {})
    if ambient_opt_in is not None:
        payload["ambient_opt_in"] = bool(ambient_opt_in)
        payload["mention_only"] = not bool(ambient_opt_in)
    if chance_percent is not None:
        payload["chance_percent"] = _bounded_chance(chance_percent)
    set_feature_payload(guild_id, FEATURE_SOCIAL_CHAT, normalize_social_chat_payload(payload))
    return get_social_chat_policy(guild_id)


def migrate_social_chat_consent_policy() -> dict[str, int]:
    """Persist safe semantics for pre-v2 settings already stored canonically."""
    inspected = migrated = 0
    for row in list_feature_settings(FEATURE_SOCIAL_CHAT):
        inspected += 1
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        normalized = normalize_social_chat_payload(payload)
        if normalized != payload:
            set_feature_payload(int(row["guild_id"]), FEATURE_SOCIAL_CHAT, normalized)
            migrated += 1
    return {"inspected": inspected, "migrated": migrated}
