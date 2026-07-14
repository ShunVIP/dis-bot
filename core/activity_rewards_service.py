from __future__ import annotations

from typing import Any

from core.activity_rewards_store import (
    add_activity_reputation,
    add_voice_minutes,
    get_activity_reward_config,
    increment_message_counter,
)
from core.economy import add_coins
from core.economy_profile import can_receive_currency


def reward_message(user_id: int, guild_id: int) -> dict[str, int]:
    config = get_activity_reward_config(guild_id)
    if not config["msg_enabled"]:
        return {"count": 0, "coins": 0, "reputation": 0}
    count = increment_message_counter(user_id, guild_id)
    coins = 0
    reputation = 0
    if count % int(config["msg_per_n"]) == 0:
        coins = int(config["msg_coins"])
        add_coins(user_id, coins, "msg_activity", {"guild": guild_id, "count": count})
    rep_interval = int(config["msg_rep_per_n"])
    if rep_interval and count % rep_interval == 0 and can_receive_currency(user_id):
        reputation = int(config["msg_rep"])
        if reputation:
            add_activity_reputation(user_id, reputation)
    return {"count": count, "coins": coins, "reputation": reputation}


def reward_voice(user_id: int, guild_id: int, seconds: int) -> dict[str, int]:
    config = get_activity_reward_config(guild_id)
    minutes = max(0, int(seconds)) // 60
    if not config["voice_enabled"] or not minutes:
        return {"minutes": minutes, "total_minutes": 0, "coins": 0}
    previous, total = add_voice_minutes(user_id, guild_id, minutes)
    interval = int(config["voice_per_min"])
    coins_per_interval = int(config["voice_coins"])
    coins = ((total // interval) - (previous // interval)) * coins_per_interval
    if coins > 0:
        add_coins(user_id, coins, "voice_activity", {"guild": guild_id, "minutes": total})
    return {"minutes": minutes, "total_minutes": total, "coins": coins}


def update_reward_settings(guild_id: int, values: dict[str, Any]) -> dict[str, int | bool]:
    from core.activity_rewards_store import update_activity_reward_config

    return update_activity_reward_config(guild_id, values)
