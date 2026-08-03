from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core import daily_store
from core.economy import add_coins, debit_coins, list_positive_wallets
from core.economy_profile import can_receive_currency
from core.settings_store import (
    get_feature_payload,
    get_feature_runtime_state,
    set_feature_runtime_state,
)


FEATURE_ECONOMY = "economy"
MSK = ZoneInfo("Europe/Moscow")


def milestone_bonus(streak: int) -> int:
    return 25 if int(streak) in (7, 14, 30, 60, 100) else 0


def compute_reward(streak: int) -> int:
    clean = max(1, int(streak))
    return 25 + 5 * min(clean - 1, 7) + milestone_bonus(clean)


def tax_config(guild_id: int | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"enabled": False, "rate_pct": 10, "interval_h": 168, "last_run": ""}
    if guild_id is None:
        return config
    payload = get_feature_payload(int(guild_id), FEATURE_ECONOMY)
    config["enabled"] = bool(payload.get("tax_enabled", False))
    try:
        config["rate_pct"] = max(1, min(50, int(payload.get("tax_rate_pct", 10))))
    except (TypeError, ValueError):
        pass
    try:
        config["interval_h"] = max(1, min(720, int(payload.get("tax_interval_h", 168))))
    except (TypeError, ValueError):
        pass
    state = get_feature_runtime_state(int(guild_id), FEATURE_ECONOMY)
    config["last_run"] = str(state.get("tax_last_run") or "")
    return config


def claim_daily(user_id: int, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(MSK)).astimezone(MSK)
    if not can_receive_currency(int(user_id)):
        return {"status": "profile_required"}
    state = daily_store.advance_daily_streak(int(user_id), current.date())
    if not state["claimed"]:
        next_midnight = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return {
            "status": "already_claimed",
            "streak": int(state["streak"]),
            "next_claim_timestamp": int(next_midnight.timestamp()),
        }
    streak = int(state["streak"])
    reward = compute_reward(streak)
    balance = add_coins(int(user_id), reward, "daily", {"streak": streak})
    return {"status": "claimed", "streak": streak, "reward": reward, "balance": balance}


def collect_tax(guild_id: int | None, now: datetime | None = None) -> dict[str, int]:
    config = tax_config(guild_id)
    if guild_id is None or not config["enabled"]:
        return {"users": 0, "collected": 0}
    collected = 0
    users = 0
    for user_id, balance in list_positive_wallets():
        tax = max(1, int(int(balance) * int(config["rate_pct"]) / 100))
        debit = debit_coins(
            user_id,
            tax,
            "tax",
            {"rate": int(config["rate_pct"])},
            allow_partial=True,
        )
        actual = int(debit.get("actual", 0))
        if actual:
            collected += actual
            users += 1
    stamp = (now or datetime.now(tz=MSK)).isoformat()
    set_feature_runtime_state(int(guild_id), FEATURE_ECONOMY, {"tax_last_run": stamp})
    return {"users": users, "collected": collected}


def fine_user(user_id: int, requested: int, *, moderator_id: int, reason: str) -> dict[str, int]:
    result = debit_coins(
        int(user_id),
        int(requested),
        "fine",
        {"by": int(moderator_id), "reason": str(reason)[:300]},
        allow_partial=True,
    )
    return {
        "requested": int(result.get("requested", requested)),
        "before": int(result.get("before", 0)),
        "actual": int(result.get("actual", 0)),
        "remaining": int(result.get("balance", 0)),
    }
