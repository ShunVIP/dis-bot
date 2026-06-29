# core/riot_client.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

from config import RIOT_API_KEY, RIOT_PLATFORM_REGION, RIOT_REGIONAL_ROUTING

PLATFORM_HOSTS = {
    "br1", "eun1", "euw1", "jp1", "kr", "la1", "la2",
    "me1", "na1", "oc1", "ru", "sg2", "tr1", "tw2", "vn2",
}
REGIONAL_HOSTS = {"americas", "asia", "europe", "sea"}


@dataclass(frozen=True)
class RiotRouting:
    platform: str = RIOT_PLATFORM_REGION or "ru"
    regional: str = RIOT_REGIONAL_ROUTING or "europe"

    def normalized(self) -> "RiotRouting":
        platform = (self.platform or "ru").lower()
        regional = (self.regional or "europe").lower()
        if platform not in PLATFORM_HOSTS:
            platform = "ru"
        if regional not in REGIONAL_HOSTS:
            regional = "europe"
        return RiotRouting(platform=platform, regional=regional)


class RiotApiError(RuntimeError):
    pass


class RiotClient:
    def __init__(self, api_key: str | None = None, routing: RiotRouting | None = None):
        self.api_key = (api_key or RIOT_API_KEY or "").strip()
        self.routing = (routing or RiotRouting()).normalized()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _get(self, host: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        if not self.api_key:
            raise RiotApiError("RIOT_API_KEY is not configured")
        url = f"https://{host}.api.riotgames.com{path}"
        headers = {"X-Riot-Token": self.api_key}
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params=params or {}) as resp:
                if resp.status == 429:
                    retry = int(float(resp.headers.get("Retry-After", "3")))
                    await asyncio.sleep(min(retry, 10))
                    async with session.get(url, headers=headers, params=params or {}) as retry_resp:
                        return await self._read_response(retry_resp)
                return await self._read_response(resp)

    async def _read_response(self, resp: aiohttp.ClientResponse) -> dict[str, Any] | list[Any]:
        if resp.status >= 400:
            text = await resp.text()
            raise RiotApiError(f"Riot API HTTP {resp.status}: {text[:300]}")
        return await resp.json()

    async def account_by_riot_id(self, game_name: str, tag_line: str) -> dict[str, Any]:
        game_name_q = quote(game_name.strip(), safe="")
        tag_q = quote(tag_line.strip().lstrip("#"), safe="")
        return await self._get(
            self.routing.regional,
            f"/riot/account/v1/accounts/by-riot-id/{game_name_q}/{tag_q}",
        )

    async def summoner_by_puuid(self, puuid: str) -> dict[str, Any]:
        return await self._get(
            self.routing.platform,
            f"/lol/summoner/v4/summoners/by-puuid/{quote(puuid, safe='')}",
        )

    async def ranked_entries_by_puuid(self, puuid: str) -> list[dict[str, Any]]:
        data = await self._get(
            self.routing.platform,
            f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}",
        )
        return data if isinstance(data, list) else []

    async def champion_mastery_top(self, puuid: str, count: int = 5) -> list[dict[str, Any]]:
        data = await self._get(
            self.routing.platform,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{quote(puuid, safe='')}/top",
            {"count": int(count)},
        )
        return data if isinstance(data, list) else []

    async def match_ids_by_puuid(self, puuid: str, count: int = 20, queue: int | None = None) -> list[str]:
        params: dict[str, Any] = {"start": 0, "count": max(1, min(int(count), 100))}
        if queue:
            params["queue"] = int(queue)
        data = await self._get(
            self.routing.regional,
            f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids",
            params,
        )
        return [str(x) for x in data] if isinstance(data, list) else []

    async def match(self, match_id: str) -> dict[str, Any]:
        data = await self._get(self.routing.regional, f"/lol/match/v5/matches/{quote(match_id, safe='')}")
        return data if isinstance(data, dict) else {}


def split_riot_id(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if "#" not in text:
        raise ValueError("Use Riot ID format: Name#TAG")
    name, tag = text.rsplit("#", 1)
    name = name.strip()
    tag = tag.strip()
    if not name or not tag:
        raise ValueError("Use Riot ID format: Name#TAG")
    return name, tag
