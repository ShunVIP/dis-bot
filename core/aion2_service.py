"""Free official Steam news, explicit region labels and Moscow event dates."""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
from core import aion2_store

APP_ID = 3393110
NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
OFFICIAL_SITE = "https://aion2.plaync.com/en-us"
CHARACTER_SEARCH = OFFICIAL_SITE + "/characters/index"
REGIONS = {"global": "Global / Европа", "kr": "Корея", "tw": "Тайвань"}
MSK = ZoneInfo("Europe/Moscow")
_news_lock = asyncio.Lock()


def clean_text(value, limit=900):
    value = re.sub(r"<[^>]*>|\[[^\]]*\]", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:limit]


def news_kind(title):
    title = title.lower()
    if any(word in title for word in ("maintenance", "service issue", "outage", "server down")):
        return "maintenance"
    if any(word in title for word in ("event", "trick or treat", "conquerors", "reward")):
        return "event"
    return "news"


def normalize_news(payload):
    result = []
    for item in payload.get("appnews", {}).get("newsitems", [])[:30]:
        if item.get("feedname") != "steam_community_announcements":
            continue
        gid = str(item.get("gid", ""))
        if not gid.isdigit():
            continue
        source_url = str(item.get("url", ""))
        parsed = urlparse(source_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "store.steampowered.com", "steamcommunity.com", "steamstore-a.akamaihd.net"
        } or parsed.username or parsed.password:
            source_url = f"https://store.steampowered.com/news/app/{APP_ID}"
        result.append({"id":gid,"title":clean_text(item.get("title"),200),
                       "text":clean_text(item.get("contents")),
                       "url":source_url,
                       "timestamp":int(item.get("date",0)),
                       "kind":news_kind(str(item.get("title", "")))})
    return result


async def get_news():
    async with _news_lock:
        items, fetched_at = await asyncio.to_thread(aion2_store.get_cache)
        if items and time.time() - fetched_at < 900:
            return items, False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                async with session.get(NEWS_API, params={"appid":APP_ID,"count":30,"maxlength":1400,"feeds":"steam_community_announcements"}) as response:
                    response.raise_for_status()
                    if response.content_length and response.content_length > 1_000_000:
                        raise ValueError("news response too large")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > 1_000_000:
                            raise ValueError("news response too large")
                    fresh = normalize_news(json.loads(raw))
                    if not fresh:
                        raise ValueError("empty official feed")
            await asyncio.to_thread(aion2_store.set_cache, fresh, time.time())
            return fresh, False
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            if items:
                return items, True
            raise RuntimeError("Официальные новости сейчас недоступны. Попробуй позже.") from None


def validate_member(nickname, region, server, class_name):
    values = [str(v).strip() for v in (nickname,region,server,class_name)]
    nickname,region,server,class_name = values
    if region not in REGIONS:
        raise ValueError("Регион: global, kr или tw.")
    if not all(1 <= len(v) <= 40 for v in (nickname,server,class_name)):
        raise ValueError("Ник, сервер и класс: от 1 до 40 символов.")
    return nickname,region,server,class_name


def parse_event(title, date_text, *, now=None):
    title = title.strip()
    if not 2 <= len(title) <= 100:
        raise ValueError("Название: от 2 до 100 символов.")
    try:
        starts = datetime.strptime(date_text.strip(),"%d.%m.%Y %H:%M").replace(tzinfo=MSK).astimezone(timezone.utc)
    except ValueError:
        raise ValueError("Дата по МСК: ДД.ММ.ГГГГ ЧЧ:ММ.") from None
    now = now or datetime.now(timezone.utc)
    if not now < starts <= now + timedelta(days=366):
        raise ValueError("Событие должно быть в будущем, в пределах года.")
    return title, starts.isoformat()
