"""Authorization boundary for private platform uploads."""

from __future__ import annotations

from core.platform_store import (
    can_access_platform_target,
    get_platform_upload_access,
    register_platform_upload,
)


def register_upload(
    *,
    stored_name: str,
    owner_id: int,
    original_name: str,
    content_type: str,
    size: int,
) -> dict:
    """Persist ownership before an upload URL is returned to the browser."""

    return register_platform_upload(
        stored_name,
        owner_id,
        original_name,
        content_type,
        size,
    )


def can_download_upload(
    stored_name: str,
    user_id: int,
    *,
    can_admin: bool = False,
) -> bool:
    """Allow draft owners or members of an active attachment target.

    Once a file has been attached, its original owner no longer receives a
    special bypass.  This keeps a DM attachment private to that DM and makes a
    deleted message's file unavailable through a remembered direct URL.
    """

    access = get_platform_upload_access(stored_name)
    if not access:
        return False
    if not access["attached"]:
        return int(access["owner_id"]) == int(user_id)
    return any(
        can_access_platform_target(
            target["scope"],
            target["target_id"],
            int(user_id),
            can_admin=can_admin,
        )
        for target in access["targets"]
    )
