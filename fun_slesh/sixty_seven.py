# -*- coding: utf-8 -*-
"""
Реакции на мем "67 / six seven".

Если в сообщении встречается 67 или six seven, бот иногда кидает
случайную Giphy-ссылку по теме.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord.ext import commands

UTC = timezone.utc
GIPHY_EXPLORE_URL = "https://giphy.com/explore/67"
TRIGGER_RE = re.compile(r"(?<!\d)(67)(?!\d)|\bsix\s*seven\b", re.I)

FALLBACK_GIFS = [
    "https://giphy.com/explore/67",
]


def _extract_giphy_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("/gifs/"):
            continue
        full = "https://giphy.com" + href.split("?")[0]
        if full in seen:
            continue
        seen.add(full)
        found.append(full)
    return found


class SixtySeven(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._channel_cooldowns: dict[int, datetime] = {}
        self._gif_cache: list[str] = []
        self._gif_cache_fetched_at: datetime | None = None
        self._last_sent: dict[int, str] = {}

    def _channel_ready(self, channel_id: int) -> bool:
        now = datetime.now(UTC)
        last = self._channel_cooldowns.get(channel_id)
        if last and now - last < timedelta(minutes=2):
            return False
        self._channel_cooldowns[channel_id] = now
        return True

    async def _get_gif_pool(self) -> list[str]:
        now = datetime.now(UTC)
        if self._gif_cache and self._gif_cache_fetched_at and now - self._gif_cache_fetched_at < timedelta(hours=3):
            return self._gif_cache

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "en-US,en;q=0.9",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(GIPHY_EXPLORE_URL) as response:
                    if response.status == 200:
                        html = await response.text()
                        links = _extract_giphy_links(html)
                        if links:
                            self._gif_cache = links
                            self._gif_cache_fetched_at = now
                            return links
        except Exception:
            pass

        self._gif_cache = FALLBACK_GIFS[:]
        self._gif_cache_fetched_at = now
        return self._gif_cache

    def _pick_gif(self, channel_id: int, pool: list[str]) -> str | None:
        if not pool:
            return None
        last = self._last_sent.get(channel_id)
        candidates = [item for item in pool if item != last] or pool
        choice = random.choice(candidates)
        self._last_sent[channel_id] = choice
        return choice

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return
        if not TRIGGER_RE.search(message.content):
            return
        if not self._channel_ready(message.channel.id):
            return

        pool = await self._get_gif_pool()
        gif_url = self._pick_gif(message.channel.id, pool)
        if not gif_url:
            return

        try:
            await message.reply(gif_url, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SixtySeven(bot))
