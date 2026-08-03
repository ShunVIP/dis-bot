from __future__ import annotations

import os
import random
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from core import steam_store


MSK = ZoneInfo("Europe/Moscow")
STEAM_API_BASE = "https://api.steampowered.com"
STORE_API_BASE = "https://store.steampowered.com/api"


async def resolve_steam_id(query: str, api_key: str) -> str | None:
    query = query.strip()
    if re.fullmatch(r"\d{17}", query):
        return query
    match = re.search(r"/profiles/(\d{17})", query)
    if match:
        return match.group(1)
    match = re.search(r"/id/([^/?\s]+)", query)
    vanity = match.group(1) if match else query
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/",
            params={"key": api_key, "vanityurl": vanity},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json()
    payload = data.get("response", {})
    return str(payload["steamid"]) if payload.get("success") == 1 else None


async def get_player_summary(steam_id: str, api_key: str) -> dict[str, Any] | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": api_key, "steamids": steam_id},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json()
    players = data.get("response", {}).get("players", [])
    return players[0] if players else None


async def get_owned_games(steam_id: str, api_key: str) -> list[dict[str, Any]]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": api_key,
                "steamid": steam_id,
                "include_appinfo": 1,
                "include_played_free_games": 1,
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                return []
            data = await response.json()
    return list(data.get("response", {}).get("games", []))


async def get_wishlist(steam_id: str) -> dict[str, Any]:
    url = f"https://store.steampowered.com/wishlist/profiles/{steam_id}/wishlistdata/"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status != 200:
                return {}
            try:
                return await response.json(content_type=None)
            except Exception:
                return {}


async def get_app_details(appid: int) -> dict[str, Any] | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{STORE_API_BASE}/appdetails",
            params={"appids": int(appid), "cc": "ru", "l": "russian"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
    entry = data.get(str(appid), {})
    return entry.get("data") if entry.get("success") else None


async def search_store_app(query: str) -> dict[str, Any] | None:
    query = (query or "").strip()
    if not query:
        return None
    if query.isdigit():
        details = await get_app_details(int(query))
        return {"appid": int(query), "name": details.get("name") or f"App {query}"} if details else None
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": query, "cc": "ru", "l": "russian"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
    items = data.get("items") or []
    if not items:
        return None
    best = items[0]
    return {"appid": int(best["id"]), "name": best.get("name") or query}


def get_api_key() -> str:
    try:
        from config import STEAM_API_KEY

        if STEAM_API_KEY:
            return str(STEAM_API_KEY)
    except ImportError:
        pass
    return os.environ.get("STEAM_API_KEY", "")


async def sync_owned_games(user_id: int, steam_id: str, api_key: str) -> list[dict[str, Any]]:
    games = await get_owned_games(steam_id, api_key)
    steam_store.upsert_owned_games(user_id, games)
    return games


def format_minutes(minutes: int) -> str:
    hours, remainder = divmod(max(0, int(minutes)), 60)
    if not hours:
        return f"{remainder}м"
    return f"{hours}ч {remainder}м" if remainder else f"{hours}ч"


def period_key(kind: str, now: datetime | None = None) -> str:
    current = now or datetime.now(MSK)
    if kind == "backlog":
        year, week, _ = current.isocalendar()
        return f"{year}-W{week:02d}"
    return current.date().isoformat()


def choose_game(games: list[dict[str, Any]], *, weighted: bool = False) -> dict[str, Any] | None:
    candidates = [game for game in games if int(game.get("appid", 0)) > 0]
    if not candidates:
        return None
    if not weighted:
        return random.choice(candidates)
    preferred = [game for game in candidates if int(game.get("playtime_forever", 0)) >= 30] or candidates
    weights = [
        max(1, 3000 - min(int(game.get("playtime_forever", 0)), 3000) + int(game.get("playtime_2weeks", 0)))
        for game in preferred
    ]
    return random.choices(preferred, weights=weights, k=1)[0]


def common_games(
    first: list[dict[str, Any]], second: list[dict[str, Any]], *, limit: int = 10
) -> tuple[int, list[tuple[dict[str, Any], dict[str, Any]]]]:
    first_by_id = {int(game["appid"]): game for game in first if int(game.get("appid", 0)) > 0}
    second_by_id = {int(game["appid"]): game for game in second if int(game.get("appid", 0)) > 0}
    shared_ids = set(first_by_id) & set(second_by_id)
    ranked = sorted(
        shared_ids,
        key=lambda appid: int(first_by_id[appid].get("playtime_forever", 0))
        + int(second_by_id[appid].get("playtime_forever", 0)),
        reverse=True,
    )[: max(1, int(limit))]
    return len(shared_ids), [(first_by_id[appid], second_by_id[appid]) for appid in ranked]


def challenge_text(game_name: str) -> str:
    return random.choice(
        [
            f"Запусти **{game_name}** хотя бы на 30 минут и не называй это тестом лаунчера.",
            f"Сделай один честный заход в **{game_name}** и выйди до того, как игра начнёт жить в голове.",
            f"Найди в **{game_name}** один момент, за который её можно похвалить. Даже если придётся копать.",
            f"Сыграй в **{game_name}** без альт-таба первые 20 минут. Босс этого челленджа — внимание.",
        ]
    )


def backlog_text(game_name: str, tone: str) -> str:
    variants = (
        [
            f"**{game_name}** лежит в библиотеке почти нетронутой. Покупка была, прохождения не было. Классика жанра.",
            f"**{game_name}** смотрит из бэклога и тихо спрашивает, зачем её вообще спасали скидкой.",
        ]
        if tone == "hard"
        else [
            f"**{game_name}** давно ждёт первого нормального запуска. Можно дать ей один вечер и посмотреть, зацепит ли.",
            f"В бэклоге мягко светится **{game_name}**. Не срочно, но игра явно просит шанс.",
        ]
    )
    return random.choice(variants)
