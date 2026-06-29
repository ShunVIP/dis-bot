# -*- coding: utf-8 -*-
"""
Реакции на мем "67 / six seven".

Если в сообщении встречается 67 или six seven, бот иногда кидает
случайную тематическую Giphy-гифку по мему 67.
"""

from __future__ import annotations

import html
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands

UTC = timezone.utc
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
GIPHY_SEARCH_URLS = (
    "https://giphy.com/search/67-meme",
    "https://giphy.com/search/67-brainrot",
    "https://giphy.com/search/six-seven",
    "https://giphy.com/search/67",
)
TRIGGER_RE = re.compile(r"(?<!\d)(67)(?!\d)|\bsix\s*seven\b", re.I)
MEDIA_RE = re.compile(r"https://media\d*\.giphy\.com/media/([A-Za-z0-9]+)/giphy(?:[-\w]*)?\.(?:gif|webp)")
HREF_ID_RE = re.compile(r"/gifs/[^\"' ]*?-([A-Za-z0-9]+)(?:[/?#]|$)")
JSON_ID_RE = re.compile(r'"id":"([A-Za-z0-9]+)"')
PAGE_HREF_RE = re.compile(r'href="(/gifs/[^"]+)"', re.I)
PAGE_PATH_RE = re.compile(r"^/gifs/[A-Za-z0-9\-]+$")
BAD_GIPHY_IDS = {"comscore", "giphy", "search"}
CURATED_67_MEMES = [
    "https://giphy.com/gifs/meme-gifzbox-six-seven-RVCJ3vwebUGDpoy7Tm",
    "https://giphy.com/gifs/raenaai-meme-brainrot-67-PXJ0gV0pE6CjRQqs9p",
    "https://giphy.com/gifs/67-six-seven-6-7-jOEF9Bwm5vrqYEK4Xr",
    "https://giphy.com/gifs/6-7-lospollos-los-and-wad-67-cfocwRJl99zkfd5w5s/",
    "https://giphy.com/gifs/67-six-seven-diddybludeinstein-h1fDrNk53BgshHSFkf",
    "https://giphy.com/gifs/digi995-67-6-7-six-seven-3K0JyaSZQJa6HIy3gU",
    "https://giphy.com/gifs/brainrot-67-hate-MKUOUJrFldIyi2hJyT",
    "https://giphy.com/gifs/67-6-7-meme-cMKVt2A8Eg9QQyWTto",
    "https://giphy.com/gifs/brainrot-67-spongeball-g2mQaLCGAm3k7OpIN9/",
    "https://giphy.com/gifs/six-67-6-7-FzfTeUt2qU30knozcq",
    "https://giphy.com/gifs/RespectiveCollective-cat-brainrot-67-emleA2iGk5UFLXrGoX",
]

FALLBACK_67_GIFS = [
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcXZiYWJqem05bm9qa3RqOWQ1eDZnMmdpNHJzMHcyNTR4OTdiOXp2diZlcD12MV9naWZzX3NlYXJjaCZjdD1n/l3q2K5jinAlChoCLS/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExdmpnZXQ5M2JsbDNjZXQ1eTR5Mm83aGdoemI4cnM5aWx2eG9oM3gxeCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/1zSz5MVw4zKg0/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExa2N0ZjRucnV0Zjcycm90eWNsN2NtaHFydDdhb2ZlNXRuMXYwYXd3YyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3orieLeZL5kyNqiLm/giphy.gif",
]
FALLBACK_67_PAGES = [
    "https://giphy.com/gifs/67-l3q2K5jinAlChoCLS",
    "https://giphy.com/gifs/67-1zSz5MVw4zKg0",
    "https://giphy.com/gifs/67-3orieLeZL5kyNqiLm",
]


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sixty_seven_excluded_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )
        conn.commit()


def _is_excluded(guild_id: int, channel_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM sixty_seven_excluded_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        ).fetchone()
    return bool(row)


def _normalize_media_url(gif_id: str) -> str:
    return f"https://media.giphy.com/media/{gif_id}/giphy.gif"


def _normalize_page_url(path: str) -> str:
    return f"https://giphy.com{path}"


def _valid_gif_id(gif_id: str) -> bool:
    gif_id = (gif_id or "").strip()
    if not gif_id or len(gif_id) < 6:
        return False
    return gif_id.lower() not in BAD_GIPHY_IDS


def _valid_page_path(path: str) -> bool:
    path = (path or "").strip()
    if not PAGE_PATH_RE.match(path):
        return False
    lowered = path.lower()
    if "/search" in lowered:
        return False
    return "67" in lowered or "six" in lowered


def _extract_giphy_links(page_html: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    for path in PAGE_HREF_RE.findall(page_html):
        if not _valid_page_path(path):
            continue
        url = _normalize_page_url(path)
        if url not in seen:
            seen.add(url)
            found.append(url)

    if found:
        return found

    for gif_id in MEDIA_RE.findall(page_html):
        if not _valid_gif_id(gif_id):
            continue
        url = _normalize_media_url(gif_id)
        if url not in seen:
            seen.add(url)
            found.append(url)

    if found:
        return found

    for gif_id in HREF_ID_RE.findall(page_html):
        if not _valid_gif_id(gif_id):
            continue
        url = _normalize_media_url(gif_id)
        if url not in seen:
            seen.add(url)
            found.append(url)

    for gif_id in JSON_ID_RE.findall(page_html):
        if not _valid_gif_id(gif_id):
            continue
        url = _normalize_media_url(gif_id)
        if url not in seen:
            seen.add(url)
            found.append(url)

    if found:
        return found

    soup = BeautifulSoup(page_html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        if _valid_page_path(href):
            url = _normalize_page_url(href)
            if url not in seen:
                seen.add(url)
                found.append(url)

    if found:
        return found

    for script in soup.find_all("script"):
        text = script.string or script.get_text(" ", strip=False)
        if not text:
            continue
        text = html.unescape(text)
        for gif_id in MEDIA_RE.findall(text):
            if not _valid_gif_id(gif_id):
                continue
            url = _normalize_media_url(gif_id)
            if url not in seen:
                seen.add(url)
                found.append(url)
        for gif_id in HREF_ID_RE.findall(text):
            if not _valid_gif_id(gif_id):
                continue
            url = _normalize_media_url(gif_id)
            if url not in seen:
                seen.add(url)
                found.append(url)
        for gif_id in JSON_ID_RE.findall(text):
            if not _valid_gif_id(gif_id):
                continue
            url = _normalize_media_url(gif_id)
            if url not in seen:
                seen.add(url)
                found.append(url)

    return found


class SixtySeven(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._gif_cache: list[str] = []
        self._gif_cache_fetched_at: datetime | None = None
        self._last_sent: dict[int, str] = {}
        _ensure_tables()

    async def _get_gif_pool(self) -> list[str]:
        now = datetime.now(UTC)
        if self._gif_cache and self._gif_cache_fetched_at and now - self._gif_cache_fetched_at < timedelta(hours=3):
            return self._gif_cache

        self._gif_cache = CURATED_67_MEMES[:]
        random.shuffle(self._gif_cache)
        self._gif_cache_fetched_at = now

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "en-US,en;q=0.9",
            }
            found: list[str] = []
            seen: set[str] = set()
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for url in GIPHY_SEARCH_URLS:
                    async with session.get(url) as response:
                        if response.status != 200:
                            continue
                        html = await response.text()
                        links = _extract_giphy_links(html)
                        for link in links:
                            if link not in seen:
                                seen.add(link)
                                found.append(link)
            if found:
                # Curated pool stays primary; scraped items are emergency fallback only.
                self._gif_cache = CURATED_67_MEMES[:] + [item for item in found if item not in CURATED_67_MEMES][:12]
                random.shuffle(self._gif_cache)
                self._gif_cache_fetched_at = now
                return self._gif_cache
        except Exception:
            pass

        if self._gif_cache:
            return self._gif_cache

        self._gif_cache = FALLBACK_67_PAGES[:] + FALLBACK_67_GIFS[:]
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
        if _is_excluded(message.guild.id, message.channel.id):
            return
        if not TRIGGER_RE.search(message.content):
            return

        pool = await self._get_gif_pool()
        gif_url = self._pick_gif(message.channel.id, pool)
        if not gif_url:
            return

        try:
            await message.reply(
                content=f"67\n{gif_url}",
                mention_author=False,
                suppress_embeds=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    @app_commands.command(name="мем67_исключить", description="(Админ) Исключить канал из 67-мем-триггера")
    @app_commands.describe(канал="Канал, где 67 не должен отвечать", причина="Короткая пометка для админов")
    @app_commands.checks.has_permissions(administrator=True)
    async def мем67_исключить(self, interaction: discord.Interaction, канал: discord.TextChannel, причина: str = ""):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO sixty_seven_excluded_channels(guild_id, channel_id, reason)
                VALUES(?,?,?)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET reason=excluded.reason
                """,
                (interaction.guild.id, канал.id, причина.strip()[:120]),
            )
            conn.commit()
        await interaction.response.send_message(f"✅ {канал.mention} исключен из 67-триггера.", ephemeral=True)

    @app_commands.command(name="мем67_вернуть", description="(Админ) Вернуть канал в 67-мем-триггер")
    @app_commands.describe(канал="Канал, где 67 снова может отвечать")
    @app_commands.checks.has_permissions(administrator=True)
    async def мем67_вернуть(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "DELETE FROM sixty_seven_excluded_channels WHERE guild_id=? AND channel_id=?",
                (interaction.guild.id, канал.id),
            )
            conn.commit()
        await interaction.response.send_message(f"✅ {канал.mention} снова участвует в 67-триггере.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SixtySeven(bot))
