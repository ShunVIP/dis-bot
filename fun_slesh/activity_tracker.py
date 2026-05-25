# -*- coding: utf-8 -*-
"""
General Discord activity tracker.

Tracks rich presence activities, stores finished sessions, and can post short
game haiku without real user mentions.
"""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
WIKI_HEADERS = {"User-Agent": "ViPikBot/1.0 (Discord bot; private server)"}

TRACKED_TYPES = {
    discord.ActivityType.playing: "game",
    discord.ActivityType.streaming: "streaming",
    discord.ActivityType.listening: "listening",
    discord.ActivityType.watching: "watching",
    discord.ActivityType.competing: "competing",
}

TYPE_LABELS = {
    "game": "игра",
    "streaming": "стрим",
    "listening": "слушает",
    "watching": "смотрит",
    "competing": "соревнование",
}

FALLBACK_GAME_HAIKU = [
    "{game} мерцает.\n{display_name} входит в вечер.\nКурсор как луна.",
    "Пиксели дышат.\n{display_name} запускает {game}.\nЧат на миг притих.",
    "Экран оживает.\n{display_name} берёт свой маршрут.\n{game} ждёт шагов.",
    "Ночь у монитора.\n{game} зовёт без слов.\n{display_name} отвечает.",
]


def _ensure_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activity_tracker_config (
                guild_id       INTEGER PRIMARY KEY,
                channel_id     INTEGER,
                enabled        INTEGER NOT NULL DEFAULT 1,
                notify_starts  INTEGER NOT NULL DEFAULT 1,
                notify_ends    INTEGER NOT NULL DEFAULT 0,
                article_lookup INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS activity_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                activity_type TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                ended_at      TEXT    NOT NULL,
                seconds       INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_active_sessions (
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                activity_type TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id, activity_name, activity_type)
            );

            CREATE TABLE IF NOT EXISTS activity_article_cache (
                activity_name TEXT NOT NULL,
                lang          TEXT NOT NULL,
                title         TEXT,
                extract       TEXT,
                url           TEXT,
                fetched_at    TEXT NOT NULL,
                PRIMARY KEY (activity_name, lang)
            );

            CREATE INDEX IF NOT EXISTS idx_activity_sessions_guild_started
                ON activity_sessions(guild_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_activity_sessions_guild_user
                ON activity_sessions(guild_id, user_id);
            """
        )
        conn.commit()


def _normalize_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def _activity_key(activity: discord.BaseActivity) -> tuple[str, str] | None:
    name = _normalize_name(getattr(activity, "name", "") or "")
    activity_type = TRACKED_TYPES.get(getattr(activity, "type", None))
    if not name or not activity_type:
        return None
    return name, activity_type


def _extract_activities(member: discord.Member) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for activity in member.activities or []:
        key = _activity_key(activity)
        if key:
            found.add(key)
    return found


def _fmt_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def _member_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(int(user_id))
    return member.display_name if member else f"участник {user_id}"


def _fallback_haiku(game_name: str, display_name: str, article: dict | None) -> str:
    seed = f"{game_name}:{display_name}:{datetime.now(MSK).date().isoformat()}"
    template = FALLBACK_GAME_HAIKU[hash(seed) % len(FALLBACK_GAME_HAIKU)]
    if article and article.get("title") and article["title"].lower() != game_name.lower():
        template = "Статья шепнула.\n{display_name} открыл {game}.\nЛор лёг на ладонь."
    return template.format(game=game_name, display_name=display_name)


async def _fetch_wiki_article(game_name: str) -> dict | None:
    cache_key = game_name.lower()
    fresh_after = datetime.now(UTC) - timedelta(days=14)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT title, extract, url, fetched_at
            FROM activity_article_cache
            WHERE activity_name=? AND lang='ru'
            """,
            (cache_key,),
        ).fetchone()
        if row:
            try:
                fetched_at = datetime.fromisoformat(row[3])
            except Exception:
                fetched_at = datetime.min.replace(tzinfo=UTC)
            if fetched_at >= fresh_after:
                return {"title": row[0], "extract": row[1], "url": row[2], "lang": "ru"}

    async def search(lang: str, query: str) -> dict | None:
        base = f"https://{lang}.wikipedia.org/w/rest.php/v1"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=WIKI_HEADERS) as session:
            async with session.get(f"{base}/search/page", params={"q": query, "limit": 3}) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
            pages = payload.get("pages") or []
            if not pages:
                return None
            best = pages[0]
            key = best.get("key") or (best.get("title") or "").replace(" ", "_")
            if not key:
                return None
            safe_key = urllib.parse.quote(key, safe="")
            async with session.get(f"{base}/page/{safe_key}/summary") as resp:
                if resp.status != 200:
                    return None
                summary = await resp.json()
        title = summary.get("title") or key.replace("_", " ")
        extract = summary.get("extract") or ""
        url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(key, safe='')}"
        return {"title": title, "extract": extract[:900], "url": url, "lang": lang}

    article = None
    for lang, query in (("ru", f"{game_name} игра"), ("en", f"{game_name} video game")):
        try:
            article = await search(lang, query)
        except Exception:
            article = None
        if article:
            break

    if article:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_article_cache(activity_name, lang, title, extract, url, fetched_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(activity_name, lang) DO UPDATE SET
                    title=excluded.title,
                    extract=excluded.extract,
                    url=excluded.url,
                    fetched_at=excluded.fetched_at
                """,
                (cache_key, "ru", article["title"], article["extract"], article["url"], datetime.now(UTC).isoformat()),
            )
            conn.commit()
    return article


def _call_gpt_haiku(prompt: str) -> str | None:
    try:
        import fun_slesh.parody_gpt as pgpt

        model, tokenizer = pgpt._load_model()
        if model is None:
            return None
        result = pgpt._generate(model, tokenizer, prompt, max_new_tokens=90)
        lines = [line.strip() for line in result.strip().splitlines() if line.strip()][:3]
        if len(lines) == 3:
            return "\n".join(lines)
    except Exception:
        return None
    return None


async def _generate_game_haiku(game_name: str, display_name: str, article: dict | None) -> str:
    context = ""
    if article and article.get("extract"):
        context = f" Связанная статья: {article['title']}: {article['extract'][:500]}"
    prompt = (
        "Напиши короткое русское хокку в три строки о том, что участник Discord "
        f"{display_name} запустил игру {game_name}.{context} "
        "Адаптируй образы под конкретную игру, без тегов, без пояснений, только три строки."
    )
    result = await asyncio.get_event_loop().run_in_executor(None, _call_gpt_haiku, prompt)
    return result or _fallback_haiku(game_name, display_name, article)


class ActivityTracker(commands.Cog):
    activity_group = app_commands.Group(
        name="активности",
        description="Трекинг Discord-активностей и игр",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active: dict[tuple[int, int, str, str], datetime] = {}
        _ensure_tables()
        self._load_active_sessions()

    def _load_active_sessions(self):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT guild_id, user_id, activity_name, activity_type, started_at FROM activity_active_sessions"
            ).fetchall()
        for guild_id, user_id, name, activity_type, started_at in rows:
            try:
                started_dt = datetime.fromisoformat(started_at)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=UTC)
            except Exception:
                started_dt = datetime.now(UTC)
            self._active[(int(guild_id), int(user_id), str(name), str(activity_type))] = started_dt

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self._reconcile_active_sessions())

    def _remember(self, guild_id: int, user_id: int, name: str, activity_type: str):
        now = datetime.now(UTC)
        key = (guild_id, user_id, name, activity_type)
        self._active[key] = now
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_active_sessions(guild_id, user_id, activity_name, activity_type, started_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id, user_id, activity_name, activity_type) DO UPDATE SET
                    started_at=excluded.started_at
                """,
                (guild_id, user_id, name, activity_type, now.isoformat()),
            )
            conn.commit()

    def _finish(self, guild_id: int, user_id: int, name: str, activity_type: str) -> int:
        key = (guild_id, user_id, name, activity_type)
        started_at = self._active.pop(key, None)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT started_at FROM activity_active_sessions
                WHERE guild_id=? AND user_id=? AND activity_name=? AND activity_type=?
                """,
                (guild_id, user_id, name, activity_type),
            ).fetchone()
            conn.execute(
                """
                DELETE FROM activity_active_sessions
                WHERE guild_id=? AND user_id=? AND activity_name=? AND activity_type=?
                """,
                (guild_id, user_id, name, activity_type),
            )
            if started_at is None and row:
                try:
                    started_at = datetime.fromisoformat(row[0])
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=UTC)
                except Exception:
                    started_at = None
            if started_at is None:
                conn.commit()
                return 0
            ended_at = datetime.now(UTC)
            seconds = int((ended_at - started_at).total_seconds())
            if seconds > 0:
                conn.execute(
                    """
                    INSERT INTO activity_sessions(guild_id, user_id, activity_name, activity_type, started_at, ended_at, seconds)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (guild_id, user_id, name, activity_type, started_at.isoformat(), ended_at.isoformat(), seconds),
                )
            conn.commit()
        return max(0, seconds)

    def _config(self, guild_id: int) -> dict:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR IGNORE INTO activity_tracker_config(guild_id) VALUES(?)", (guild_id,))
            row = conn.execute(
                """
                SELECT channel_id, enabled, notify_starts, notify_ends, article_lookup
                FROM activity_tracker_config WHERE guild_id=?
                """,
                (guild_id,),
            ).fetchone()
            conn.commit()
        return {
            "channel_id": row[0],
            "enabled": bool(row[1]),
            "notify_starts": bool(row[2]),
            "notify_ends": bool(row[3]),
            "article_lookup": bool(row[4]),
        }

    def _pick_channel(self, guild: discord.Guild, cfg: dict) -> discord.TextChannel | None:
        channel_id = cfg.get("channel_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT channel_id FROM daily_summary_config WHERE guild_id=? AND enabled=1 AND channel_id IS NOT NULL",
                (guild.id,),
            ).fetchone()
        channel = self.bot.get_channel(row[0]) if row else None
        if isinstance(channel, discord.TextChannel):
            return channel
        return guild.system_channel

    async def _send_start_notice(self, member: discord.Member, name: str, activity_type: str):
        cfg = self._config(member.guild.id)
        if not cfg["enabled"] or not cfg["notify_starts"] or activity_type != "game":
            return
        channel = self._pick_channel(member.guild, cfg)
        if channel is None:
            return
        article = await _fetch_wiki_article(name) if cfg["article_lookup"] else None
        haiku = await _generate_game_haiku(name, member.display_name, article)
        embed = discord.Embed(
            title=f"{member.display_name} запустил {name}",
            description=f"*{haiku}*",
            color=discord.Color.dark_teal(),
        )
        if article and article.get("url"):
            embed.add_field(
                name="Связанная статья",
                value=f"[{article.get('title') or name}]({article['url']})",
                inline=False,
            )
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    async def _send_end_notice(self, member: discord.Member, name: str, activity_type: str, seconds: int):
        cfg = self._config(member.guild.id)
        if not cfg["enabled"] or not cfg["notify_ends"]:
            return
        channel = self._pick_channel(member.guild, cfg)
        if channel is None:
            return
        label = TYPE_LABELS.get(activity_type, activity_type)
        try:
            await channel.send(
                f"{member.display_name} завершил {label} **{name}**: {_fmt_seconds(seconds)}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    async def _reconcile_active_sessions(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                for name, activity_type in _extract_activities(member):
                    key = (guild.id, member.id, name, activity_type)
                    if key not in self._active:
                        self._remember(guild.id, member.id, name, activity_type)

        for guild_id, user_id, name, activity_type in list(self._active):
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            if member is None:
                continue
            if (name, activity_type) not in _extract_activities(member):
                self._finish(guild_id, user_id, name, activity_type)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return
        before_items = _extract_activities(before)
        after_items = _extract_activities(after)

        for name, activity_type in sorted(before_items - after_items):
            seconds = self._finish(after.guild.id, after.id, name, activity_type)
            if seconds:
                await self._send_end_notice(after, name, activity_type, seconds)

        for name, activity_type in sorted(after_items - before_items):
            self._remember(after.guild.id, after.id, name, activity_type)
            await self._send_start_notice(after, name, activity_type)

    @activity_group.command(name="канал", description="(Админ) Канал для постов об игровых активностях")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_канал(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_tracker_config(guild_id, channel_id, enabled)
                VALUES(?,?,1)
                ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, enabled=1
                """,
                (interaction.guild.id, канал.id),
            )
            conn.commit()
        await interaction.response.send_message(
            f"✅ Игровые хокку и топы активностей будут идти в {канал.mention}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @activity_group.command(name="вкл", description="(Админ) Включить или выключить трекинг активностей")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_вкл(self, interaction: discord.Interaction, включить: bool):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_tracker_config(guild_id, enabled)
                VALUES(?,?)
                ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled
                """,
                (interaction.guild.id, int(включить)),
            )
            conn.commit()
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(f"{status} трекинг активностей.", ephemeral=True)

    @activity_group.command(name="посты", description="(Админ) Настроить посты о старте/конце активностей")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_посты(
        self,
        interaction: discord.Interaction,
        старты_игр: bool = True,
        окончания: bool = False,
        статьи: bool = True,
    ):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_tracker_config(guild_id, notify_starts, notify_ends, article_lookup)
                VALUES(?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    notify_starts=excluded.notify_starts,
                    notify_ends=excluded.notify_ends,
                    article_lookup=excluded.article_lookup
                """,
                (interaction.guild.id, int(старты_игр), int(окончания), int(статьи)),
            )
            conn.commit()
        await interaction.response.send_message("✅ Настройки постов активностей обновлены.", ephemeral=True)

    @activity_group.command(name="топ", description="Топ активностей за N дней")
    async def активности_топ(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 365] = 7):
        await interaction.response.defer()
        since = (datetime.now(UTC) - timedelta(days=int(дней))).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            top_activities = conn.execute(
                """
                SELECT activity_name, activity_type, SUM(seconds) AS total
                FROM activity_sessions
                WHERE guild_id=? AND started_at>=?
                GROUP BY activity_name, activity_type
                ORDER BY total DESC LIMIT 10
                """,
                (interaction.guild.id, since),
            ).fetchall()
            top_users = conn.execute(
                """
                SELECT user_id, SUM(seconds) AS total
                FROM activity_sessions
                WHERE guild_id=? AND started_at>=?
                GROUP BY user_id
                ORDER BY total DESC LIMIT 10
                """,
                (interaction.guild.id, since),
            ).fetchall()

        if not top_activities and not top_users:
            await interaction.followup.send("📭 Пока нет завершённых активностей за выбранный период.")
            return

        def activity_line(i: int, row: tuple) -> str:
            name, activity_type, total = row
            label = TYPE_LABELS.get(activity_type, activity_type)
            return f"**{i}.** {name} ({label}) — **{_fmt_seconds(int(total))}**"

        activity_lines = [activity_line(i, row) for i, row in enumerate(top_activities, start=1)]
        user_lines = [
            f"**{i}.** {_member_name(interaction.guild, int(user_id))} — **{_fmt_seconds(int(total))}**"
            for i, (user_id, total) in enumerate(top_users, start=1)
        ]
        embed = discord.Embed(title=f"🎮 Активности за {дней} дн.", color=discord.Color.teal())
        if activity_lines:
            embed.add_field(name="По активностям", value="\n".join(activity_lines), inline=False)
        if user_lines:
            embed.add_field(name="По участникам", value="\n".join(user_lines), inline=False)
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityTracker(bot))
