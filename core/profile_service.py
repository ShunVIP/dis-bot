from __future__ import annotations

import sqlite3
from typing import Any

from core.birthday_store import get_birthday, set_birthday, validate_birthday
from core.community_store import get_profile, get_user_roles, upsert_profile
from core.conversation_store import (
    delete_user_conversation_data,
    get_conversation_preferences,
    set_conversation_preferences,
)
from core.db import connection as db_connection
from core.economy import get_balance
from core.economy_profile import get_economy_profile, set_economy_profile
from core.game_profiles import (
    GAME_LOL,
    get_game_account,
    get_latest_lol_snapshot,
    get_player_model_profile,
)
from core.paths import SOCIAL_DB
from core.gamer_profile_service import normalize_requested_tags
from core.gamer_profile_store import delete_gamer_profile, refresh_gamer_profile


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _steam_profile(user_id: int) -> dict[str, Any] | None:
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "steam_profiles"):
            return None
        row = conn.execute(
            "SELECT steam_id, added_at FROM steam_profiles WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        games = 0
        playtime_minutes = 0
        if _table_exists(conn, "steam_owned_games_cache"):
            games, playtime_minutes = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(playtime_forever), 0) FROM steam_owned_games_cache WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
    return {
        "steam_id": str(row[0]),
        "added_at": str(row[1]),
        "cached_games": int(games or 0),
        "playtime_minutes": int(playtime_minutes or 0),
    }


def _wwm_profile(user_id: int) -> dict[str, Any] | None:
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, "wwm_profiles"):
            return None
        row = conn.execute(
            """
            SELECT guild_id, game_nick, nick_synced, character_card, character_updated_at, updated_at
            FROM wwm_profiles WHERE user_id=? ORDER BY updated_at DESC LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    return {
        "guild_id": int(row[0]),
        "game_nick": str(row[1]),
        "nick_synced": bool(row[2]),
        "character_card": str(row[3] or ""),
        "character_updated_at": str(row[4] or ""),
        "updated_at": str(row[5] or ""),
    }


def get_unified_profile(user_id: int) -> dict[str, Any]:
    riot = get_game_account(user_id, GAME_LOL)
    preferences = get_conversation_preferences(user_id)
    gamer_profile = (
        refresh_gamer_profile(0, user_id)
        if preferences.get("memory_opt_in")
        else {"archetypes": [], "top_games": []}
    )
    return {
        "user_id": int(user_id),
        "community": get_profile(user_id),
        "roles": get_user_roles(user_id),
        "birthday": get_birthday(user_id),
        "economy": {
            "profile": get_economy_profile(user_id),
            "balance": get_balance(user_id),
        },
        "games": {
            "steam": _steam_profile(user_id),
            "wwm": _wwm_profile(user_id),
            "lol": {
                "account": riot,
                "snapshot": get_latest_lol_snapshot(user_id) if riot else None,
                "model": get_player_model_profile(user_id, GAME_LOL) if riot else None,
            },
        },
        "ai": {
            "conversation": preferences,
            "gamer_profile": gamer_profile,
        },
    }


def update_unified_profile(user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    community = data.get("community")
    if isinstance(community, dict):
        badges = community.get("badges")
        if badges is not None and not isinstance(badges, list):
            raise ValueError("badges_must_be_list")
        upsert_profile(
            user_id,
            display_name=str(community.get("display_name") or ""),
            status_text=str(community.get("status_text") or ""),
            bio=str(community.get("bio") or ""),
            accent_color=str(community.get("accent_color") or "#4fc3b1"),
            banner_preset=str(community.get("banner_preset") or "midnight"),
            avatar_decoration=str(community.get("avatar_decoration") or ""),
            badges=[str(item)[:40] for item in badges] if badges is not None else None,
        )

    birthday = data.get("birthday")
    if birthday not in (None, ""):
        value = validate_birthday(str(birthday))
        set_birthday(user_id, value, updated_by=user_id, source="web_user")

    economy_data = data.get("economy")
    if isinstance(economy_data, dict) and economy_data.get("gender"):
        set_economy_profile(
            user_id,
            str(economy_data["gender"]),
            bool(economy_data.get("age_confirmed")),
        )

    ai_data = data.get("ai")
    if isinstance(ai_data, dict):
        tags = normalize_requested_tags(ai_data.get("gamer_tags") or []) if "gamer_tags" in ai_data else None
        set_conversation_preferences(
            user_id,
            memory_opt_in=bool(ai_data["memory_opt_in"]) if "memory_opt_in" in ai_data else None,
            training_opt_in=bool(ai_data["training_opt_in"]) if "training_opt_in" in ai_data else None,
            gamer_tags=tags,
        )
    return get_unified_profile(user_id)


def forget_ai_personalization(user_id: int) -> dict[str, int]:
    removed = delete_user_conversation_data(user_id)
    removed["gamer_profiles"] = delete_gamer_profile(user_id)
    return removed
