# -*- coding: utf-8 -*-
"""
Троллинг игроков Heroes of Might and Magic по Discord activity.

Срабатывает, когда участник запускает любую часть Heroes of Might and Magic,
включая Heroes of Might and Magic: Olden Era.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.heroes_service import (
    build_troll_message,
    EXIT_GRACE_SECONDS,
    extract_game_names as _extract_game_names,
    find_active_heroes as _find_active_heroes,
    format_duration,
)
from core.heroes_store import (
    ensure_heroes_storage,
    get_heroes_output_channel_id,
    get_last_week_heroes_top,
    heroes_troll_enabled,
    load_active_sessions,
    pop_active_session,
    remember_active_session,
    save_finished_session,
    set_heroes_output_channel,
)

UTC = timezone.utc


def _configured_channel(guild: discord.Guild) -> discord.TextChannel | None:
    channel_id = get_heroes_output_channel_id(guild.id)
    channel = guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


def _pick_channel(guild: discord.Guild) -> discord.TextChannel | None:
    configured = _configured_channel(guild)
    if configured:
        return configured
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
        self._pending_exits: dict[tuple[int, int], asyncio.Task] = {}
        self._reconcile_task: asyncio.Task | None = None
        ensure_heroes_storage()
        self._load_active_sessions()

    def _load_active_sessions(self):
        for row in load_active_sessions():
            self._active_sessions[(row["guild_id"], row["user_id"])] = {
                "game_name": row["game_name"],
                "started_at": row["started_at"],
            }

    def cog_unload(self):
        for task in self._pending_exits.values():
            task.cancel()
        self._pending_exits.clear()
        if self._reconcile_task:
            self._reconcile_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(self._reconcile_active_sessions())

    def _cancel_exit(self, key):
        task = self._pending_exits.pop(key, None)
        if task:
            task.cancel()

    def _schedule_exit(self, guild_id, user_id):
        key = (guild_id, user_id)
        if key not in self._pending_exits:
            # Preserve the first empty-presence time; repeated updates do not postpone it.
            absent_at = datetime.now(UTC)
            self._pending_exits[key] = asyncio.create_task(self._finish_after_absence(key, absent_at))

    async def _finish_after_absence(self, key, absent_at):
        try:
            await asyncio.sleep(EXIT_GRACE_SECONDS)
            guild_id, user_id = key
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            # Missing cache/member is not proof that a game was closed.
            if member is None or _extract_game_names(member.activities):
                return
            session = self._pop_session(guild_id, user_id)
            if session is None:
                return
            started_at = session["started_at"]
            game_name = str(session["game_name"])
            self._save_finished_session(guild_id, user_id, game_name, started_at, absent_at)
            duration = self._format_duration(max(0, int((absent_at-started_at).total_seconds())))
            await self._send_troll(guild, user_id, game_name, duration=duration, ended=True)
        finally:
            if self._pending_exits.get(key) is asyncio.current_task():
                self._pending_exits.pop(key, None)

    def _remember_session(self, guild_id: int, user_id: int, game_name: str):
        now = datetime.now(UTC)
        self._active_sessions[(guild_id, user_id)] = {
            "game_name": game_name,
            "started_at": now,
        }
        remember_active_session(guild_id, user_id, game_name, now)

    def _pop_session(self, guild_id: int, user_id: int):
        session = self._active_sessions.pop((guild_id, user_id), None)
        pop_active_session(guild_id, user_id)
        return session

    async def _send_troll(self, guild: discord.Guild, user_id: int, game_name: str, *, duration: str | None = None, ended: bool = False):
        if not heroes_troll_enabled(guild.id):
            return
        channel = _pick_channel(guild)
        if channel is None:
            return
        member = guild.get_member(user_id)
        display_name = member.display_name if member else f"участник {user_id}"
        message = build_troll_message(
            user_id, game_name, display_name, duration=duration, ended=ended
        )
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    @app_commands.command(name="герои_канал", description="(Админ) Настроить канал Heroes troll")
    @app_commands.describe(канал="Канал, куда бот будет писать Heroes troll")
    @app_commands.checks.has_permissions(administrator=True)
    async def герои_канал(self, interaction: discord.Interaction, канал: discord.TextChannel):
        set_heroes_output_channel(interaction.guild.id, канал.id)
        await interaction.response.send_message(f"✅ Heroes troll будет писать в {канал.mention}.", ephemeral=True)

    def _save_finished_session(self, guild_id: int, user_id: int, game_name: str, started_at: datetime, ended_at: datetime):
        save_finished_session(guild_id, user_id, game_name, started_at, ended_at)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        return format_duration(seconds)

    async def _reconcile_active_sessions(self):
        await self.bot.wait_until_ready()
        for (guild_id, user_id), session in list(self._active_sessions.items()):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            member = guild.get_member(user_id)
            if member is None:
                continue
            if _extract_game_names(member.activities):
                self._cancel_exit((guild_id, user_id))
                continue
            self._schedule_exit(guild_id, user_id)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        before_names = _extract_game_names(before.activities)
        after_names = _extract_game_names(after.activities)
        active_before = _find_active_heroes(before_names)
        active_after = _find_active_heroes(after_names)
        key = (after.guild.id, after.id)

        if active_after:
            self._cancel_exit(key)
            if key in self._active_sessions:
                return  # Alt-tab, activity order/name changes, or a temporary presence gap.
            self._remember_session(after.guild.id, after.id, active_after)
            if not active_before:
                await self._send_troll(after.guild, after.id, active_after)
            return
        if key not in self._active_sessions:
            return
        if after_names:
            # Discord may advertise only the foreground game. Another game does not
            # prove that Heroes stopped; keep the existing session without new posts.
            self._cancel_exit(key)
            return
        self._schedule_exit(after.guild.id, after.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(HeroesTroll(bot))
