# -*- coding: utf-8 -*-
from core.settings_store import (
    clear_feature_channel,
    get_feature_channel_ids,
    set_feature_channel,
)

FEATURE = "parody_training"


def ensure_parody_channel_settings():
    return None


def get_parody_excluded_channel_ids(guild_id: int) -> set[int]:
    return get_feature_channel_ids(guild_id, FEATURE, "exclude")


def set_parody_channel_excluded(guild_id: int, channel_id: int, reason: str = ""):
    set_feature_channel(guild_id, FEATURE, channel_id, "exclude", reason)


def clear_parody_channel_excluded(guild_id: int, channel_id: int) -> int:
    return clear_feature_channel(guild_id, FEATURE, channel_id, "exclude")


def filter_parody_channels(guild, channels):
    excluded = get_parody_excluded_channel_ids(guild.id)
    return [ch for ch in channels if ch.id not in excluded]
