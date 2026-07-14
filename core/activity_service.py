from __future__ import annotations

from core.settings_store import (
    is_feature_enabled,
    set_feature_enabled,
)


FEATURE_ACTIVITY_TRACKER = "activity_tracker"
def is_activity_enabled(guild_id: int) -> bool:
    """Activity tracking is silent and controlled by one canonical switch."""
    return is_feature_enabled(int(guild_id), FEATURE_ACTIVITY_TRACKER, default=True)


def set_activity_enabled(guild_id: int, enabled: bool) -> None:
    set_feature_enabled(int(guild_id), FEATURE_ACTIVITY_TRACKER, bool(enabled))
