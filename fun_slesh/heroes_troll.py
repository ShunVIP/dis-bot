# -*- coding: utf-8 -*-
"""
Троллинг игроков Heroes of Might and Magic по Discord activity.

Срабатывает, когда участник запускает любую часть Heroes of Might and Magic,
включая Heroes of Might and Magic: Olden Era.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

HEROES_PATTERNS = (
    "heroes of might and magic",
    "heroes of might & magic",
    "might and magic heroes",
    "might & magic heroes",
    "homm",
    "olden era",
    "heroes iii",
    "heroes iv",
    "heroes v",
    "heroes vi",
    "heroes vii",
    "heroes 3",
    "heroes 4",
    "heroes 5",
    "heroes 6",
    "heroes 7",
)

GENERIC_START_HAIKUS = [
    "Старые замки.\n<@{user_id}> снова в Heroes.\nDiscord всё видит.",
    "Ход начат тихо.\n<@{user_id}> ищет рудник.\nСессия открыта.",
    "Пыль на картах спит.\n<@{user_id}> будит скелетов.\nHeroes запущен.",
    "Башни ждут приказ.\n<@{user_id}> выбрал клетку.\nПошёл Discord-счёт.",
]

OLDEN_START_HAIKUS = [
    "Новая эра.\n<@{user_id}> снова проверит.\nDiscord начал счёт.",
    "Старый дух шуршит.\n<@{user_id}> в Olden Era.\nСессия открыта.",
    "Ностальгии звон.\n<@{user_id}> ищет чудо.\nТройка ждёт судей.",
]

GENERIC_END_HAIKUS = [
    "Ход окончен, тишь.\n<@{user_id}> вышел из Heroes.\nDiscord-сессия: {duration}.",
    "Замок опустел.\n<@{user_id}> вернулся к людям.\nHeroes открыт: {duration}.",
    "Последний мувпойнт.\n<@{user_id}> покинул карту.\nПо Discord: {duration}.",
]

OLDEN_END_HAIKUS = [
    "Эра стихает.\n<@{user_id}> вышел из споров.\nDiscord-сессия: {duration}.",
    "Новая эра.\n<@{user_id}> вернулся в чат.\nHeroes открыт: {duration}.",
]


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS heroes_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                seconds INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS heroes_active_sessions (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )
        conn.commit()


def get_last_week_heroes_top(guild_id: int) -> list[tuple[int, int]]:
    today = datetime.now(MSK).date()
    start_this_week = today - timedelta(days=today.weekday())
    start_prev_week = start_this_week - timedelta(days=7)
    start_prev_week_utc = datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat()
    start_this_week_utc = datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM heroes_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<?
            GROUP BY user_id
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC
            LIMIT 5
            """,
            (guild_id, start_prev_week_utc, start_this_week_utc),
        ).fetchall()
    return [(int(user_id), int(total_seconds)) for user_id, total_seconds in rows]


def _normalize_name(name: str) -> str:
    return " ".join((name or "").lower().replace(":", " ").replace("-", " ").split())


def _is_heroes_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return any(pattern in normalized for pattern in HEROES_PATTERNS)


def _is_olden_era(name: str) -> bool:
    return "olden era" in _normalize_name(name)


def _extract_game_names(activities: tuple[discord.BaseActivity, ...] | list[discord.BaseActivity]) -> set[str]:
    names: set[str] = set()
    for activity in activities or []:
        name = getattr(activity, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def _find_started_heroes(before_names: set[str], after_names: set[str]) -> str | None:
    for name in sorted(after_names):
        if name in before_names:
            continue
        if _is_heroes_name(name):
            return name
    return None


def _find_active_heroes(names: set[str]) -> str | None:
    for name in sorted(names):
        if _is_heroes_name(name):
            return name
    return None


def _pick_channel(guild: discord.Guild) -> discord.TextChannel | None:
    me = guild.me
    if guild.system_channel and me:
        perms = guild.system_channel.permissions_for(me)
        if perms.send_messages and perms.view_channel:
            return guild.system_channel

    for channel in guild.text_channels:
        if not me:
            continue
        perms = channel.permissions_for(me)
        if perms.send_messages and perms.view_channel:
            return channel
    return None


class HeroesTroll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_sessions: dict[tuple[int, int], dict[str, object]] = {}
        _ensure_tables()
        self._load_active_sessions()

    def _load_active_sessions(self):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT guild_id, user_id, game_name, started_at FROM heroes_active_sessions"
            ).fetchall()
        for guild_id, user_id, game_name, started_at in rows:
            try:
                started_dt = datetime.fromisoformat(started_at)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=UTC)
            except Exception:
                started_dt = datetime.now(UTC)
            self._active_sessions[(int(guild_id), int(user_id))] = {
                "game_name": str(game_name),
                "started_at": started_dt,
            }

    def cog_unload(self):
        pass

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self._reconcile_active_sessions())

    def _remember_session(self, guild_id: int, user_id: int, game_name: str):
        now = datetime.now(UTC)
        self._active_sessions[(guild_id, user_id)] = {
            "game_name": game_name,
            "started_at": now,
        }
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO heroes_active_sessions(guild_id, user_id, game_name, started_at)
                VALUES(?,?,?,?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    game_name=excluded.game_name,
                    started_at=excluded.started_at
                """,
                (guild_id, user_id, game_name, now.isoformat()),
            )
            conn.commit()

    def _pop_session(self, guild_id: int, user_id: int):
        session = self._active_sessions.pop((guild_id, user_id), None)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "DELETE FROM heroes_active_sessions WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            conn.commit()
        return session

    def _pick_message(self, user_id: int, game_name: str, pool: list[str], *, duration: str | None = None) -> str:
        base = pool[hash((user_id, game_name, datetime.now(UTC).hour, duration or "")) % len(pool)]
        payload = {"user_id": user_id, "duration": duration or "недолго"}
        return base.format(**payload)

    async def _send_troll(self, guild: discord.Guild, user_id: int, game_name: str, *, duration: str | None = None, ended: bool = False):
        channel = _pick_channel(guild)
        if channel is None:
            return
        olden = _is_olden_era(game_name)
        if ended:
            pool = OLDEN_END_HAIKUS if olden else GENERIC_END_HAIKUS
        else:
            pool = OLDEN_START_HAIKUS if olden else GENERIC_START_HAIKUS
        message = self._pick_message(user_id, game_name, pool, duration=duration)
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        except Exception:
            pass

    def _save_finished_session(self, guild_id: int, user_id: int, game_name: str, started_at: datetime, ended_at: datetime):
        seconds = int((ended_at - started_at).total_seconds())
        if seconds <= 0:
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO heroes_sessions(guild_id, user_id, game_name, started_at, ended_at, seconds)
                VALUES(?,?,?,?,?,?)
                """,
                (guild_id, user_id, game_name, started_at.isoformat(), ended_at.isoformat(), seconds),
            )
            conn.commit()

    @staticmethod
    def _format_duration(seconds: int) -> str:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours:
            return f"{hours}ч {minutes}м"
        return f"{minutes}м"

    async def _reconcile_active_sessions(self):
        await self.bot.wait_until_ready()
        stale: list[tuple[discord.Guild, int, dict[str, object]]] = []

        for (guild_id, user_id), session in list(self._active_sessions.items()):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            member = guild.get_member(user_id)
            if member is None:
                continue
            active_now = _find_active_heroes(_extract_game_names(member.activities))
            if active_now:
                continue
            stale.append((guild, user_id, session))

        for guild, user_id, session in stale:
            popped = self._pop_session(guild.id, user_id)
            if not popped:
                continue
            started_at = popped["started_at"]
            game_name = str(popped["game_name"])
            ended_at = datetime.now(UTC)
            self._save_finished_session(guild.id, user_id, game_name, started_at, ended_at)
            duration = self._format_duration(int((ended_at - started_at).total_seconds()))
            await self._send_troll(guild, user_id, game_name, duration=duration, ended=True)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        before_names = _extract_game_names(before.activities)
        after_names = _extract_game_names(after.activities)
        started = _find_started_heroes(before_names, after_names)
        active_before = _find_active_heroes(before_names)
        active_after = _find_active_heroes(after_names)
        key = (after.guild.id, after.id)

        if active_before and not active_after:
            session = self._pop_session(after.guild.id, after.id)
            if session:
                started_at = session["started_at"]
                game_name = str(session["game_name"])
                ended_at = datetime.now(UTC)
                self._save_finished_session(after.guild.id, after.id, game_name, started_at, ended_at)
                duration = self._format_duration(int((ended_at - started_at).total_seconds()))
                await self._send_troll(after.guild, after.id, game_name, duration=duration, ended=True)
            return

        if not started:
            if key not in self._active_sessions and active_after:
                self._remember_session(after.guild.id, after.id, active_after)
            return
        self._remember_session(after.guild.id, after.id, started)
        await self._send_troll(after.guild, after.id, started)


async def setup(bot: commands.Bot):
    await bot.add_cog(HeroesTroll(bot))
