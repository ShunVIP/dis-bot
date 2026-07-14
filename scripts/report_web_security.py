from __future__ import annotations

import json
import sqlite3

from config import (
    APP_ALLOWED_GUILD_IDS,
    APP_API_HOST,
    APP_BASE_URL,
    APP_OWNER_USER_IDS,
    BOT_API_TOKEN,
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_OAUTH_SCOPES,
    DISCORD_REDIRECT_URI,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
)
from core.paths import SOCIAL_DB


def _ids(value: str) -> list[int]:
    result = []
    for item in str(value or "").split(","):
        if item.strip().isdigit() and int(item.strip()) > 0:
            result.append(int(item.strip()))
    return sorted(set(result))


def build_report() -> dict[str, object]:
    admins: list[dict[str, object]] = []
    with sqlite3.connect(SOCIAL_DB) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='community_user_roles'"
        ).fetchone()
        if exists:
            rows = conn.execute(
                """
                SELECT ur.discord_user_id, ur.role_slug, COALESCE(u.username, ''),
                       COALESCE(u.global_name, ''), ur.source
                FROM community_user_roles ur
                LEFT JOIN web_users u ON u.discord_user_id=ur.discord_user_id
                WHERE ur.role_slug IN ('owner', 'admin')
                ORDER BY ur.role_slug DESC, ur.discord_user_id
                """
            ).fetchall()
            admins = [
                {
                    "discord_user_id": int(row[0]),
                    "role": row[1],
                    "username": row[2],
                    "global_name": row[3],
                    "source": row[4],
                }
                for row in rows
            ]

    allowed_guild_ids = _ids(APP_ALLOWED_GUILD_IDS)
    owner_user_ids = _ids(APP_OWNER_USER_IDS)
    scopes = set(DISCORD_OAUTH_SCOPES.split())
    checks = {
        "oauth_credentials": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI),
        "oauth_guild_scope": "guilds" in scopes,
        "admission_allowlist": bool(allowed_guild_ids),
        "owner_allowlist": bool(owner_user_ids),
        "bot_bridge_token": bool(BOT_API_TOKEN),
        "livekit_complete": bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "https_app_url": APP_BASE_URL.lower().startswith("https://"),
        "https_redirect": DISCORD_REDIRECT_URI.lower().startswith("https://"),
        "loopback_bind": APP_API_HOST in {"127.0.0.1", "::1", "localhost"},
    }
    return {
        "checks": checks,
        "allowed_guild_ids": allowed_guild_ids,
        "owner_user_ids": owner_user_ids,
        "current_admins": admins,
        "ready_for_public_network": all(
            checks[name]
            for name in (
                "oauth_credentials",
                "oauth_guild_scope",
                "admission_allowlist",
                "bot_bridge_token",
                "https_app_url",
                "https_redirect",
            )
        ),
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
