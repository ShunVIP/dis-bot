# -*- coding: utf-8 -*-
"""Silent Discord presence tracking and on-demand activity statistics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.activity_service import is_activity_enabled, set_activity_enabled
from core.activity_store import (
    ensure_activity_tables,
    finish_activity_session,
    get_activity_top,
    load_active_sessions,
    remember_activity_start,
)


UTC = timezone.utc

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


def _normalize_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def _extract_activities(member: discord.Member) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for activity in member.activities:
        activity_type = TRACKED_TYPES.get(activity.type)
        if activity_type is None:
            continue
        name = _normalize_name(getattr(activity, "name", ""))
        if name and name.lower() not in {"custom status", "пользовательский статус"}:
            result.add((name, activity_type))
    return result


def _fmt_seconds(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _member_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member.display_name if member else f"участник {user_id}"


class ActivityTracker(commands.Cog):
    activity_group = app_commands.Group(
        name="активности",
        description="Тихий трекинг Discord-активностей и игр",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active: dict[tuple[int, int, str, str], datetime] = {}
        ensure_activity_tables()
        self._load_active_sessions()

    def _load_active_sessions(self) -> None:
        for guild_id, user_id, name, activity_type, started_at in load_active_sessions():
            try:
                started = datetime.fromisoformat(started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                started = datetime.now(UTC)
            self._active[(guild_id, user_id, name, activity_type)] = started

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self._reconcile_active_sessions())

    def _remember(self, guild_id: int, user_id: int, name: str, activity_type: str) -> None:
        started = datetime.now(UTC)
        self._active[(guild_id, user_id, name, activity_type)] = started
        remember_activity_start(
            guild_id,
            user_id,
            name,
            activity_type,
            started_at=started,
        )

    def _finish(self, guild_id: int, user_id: int, name: str, activity_type: str) -> int:
        started = self._active.pop((guild_id, user_id, name, activity_type), None)
        return finish_activity_session(
            guild_id,
            user_id,
            name,
            activity_type,
            cached_started_at=started,
        )

    async def _reconcile_active_sessions(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            if not is_activity_enabled(guild.id):
                continue
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
            if member is not None and (name, activity_type) not in _extract_activities(member):
                self._finish(guild_id, user_id, name, activity_type)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return
        if not is_activity_enabled(after.guild.id):
            for guild_id, user_id, name, activity_type in list(self._active):
                if guild_id == after.guild.id and user_id == after.id:
                    self._finish(guild_id, user_id, name, activity_type)
            return

        before_items = _extract_activities(before)
        after_items = _extract_activities(after)
        for name, activity_type in sorted(before_items - after_items):
            self._finish(after.guild.id, after.id, name, activity_type)
        for name, activity_type in sorted(after_items - before_items):
            self._remember(after.guild.id, after.id, name, activity_type)

    @activity_group.command(name="вкл", description="(Админ) Включить или выключить тихий трекинг")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_вкл(self, interaction: discord.Interaction, включить: bool):
        set_activity_enabled(interaction.guild.id, включить)
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(
            f"{status} тихий трекинг активностей.",
            ephemeral=True,
        )

    @activity_group.command(name="топ", description="Топ активностей за N дней")
    async def активности_топ(
        self,
        interaction: discord.Interaction,
        дней: app_commands.Range[int, 1, 365] = 7,
    ):
        await interaction.response.defer()
        since = (datetime.now(UTC) - timedelta(days=int(дней))).isoformat()
        stats = get_activity_top(interaction.guild.id, since)
        top_games = stats["top_games"]
        top_game_users = stats["top_game_users"]
        other_activities = stats["other_activities"]
        top_all_users = stats["top_all_users"]

        if not any((top_games, top_game_users, other_activities, top_all_users)):
            await interaction.followup.send(
                "📭 Пока нет завершённых активностей за выбранный период."
            )
            return

        game_lines = [
            f"**{index}.** {name} — **{_fmt_seconds(int(total))}**"
            for index, (name, total) in enumerate(top_games, start=1)
        ]
        game_user_lines = [
            f"**{index}.** {_member_name(interaction.guild, int(user_id))} — **{_fmt_seconds(int(total))}**"
            for index, (user_id, total) in enumerate(top_game_users, start=1)
        ]
        other_lines = [
            f"**{index}.** {name} ({TYPE_LABELS.get(activity_type, activity_type)}) — **{_fmt_seconds(int(total))}**"
            for index, (name, activity_type, total) in enumerate(other_activities, start=1)
        ]
        all_user_lines = [
            f"**{index}.** {_member_name(interaction.guild, int(user_id))} — **{_fmt_seconds(int(total))}**"
            for index, (user_id, total) in enumerate(top_all_users, start=1)
        ]

        embed = discord.Embed(
            title=f"🎮 Активности за {дней} дн.",
            color=discord.Color.teal(),
        )
        for title, lines in (
            ("Топ игр", game_lines),
            ("Топ игроков по играм", game_user_lines),
            ("Другие активности", other_lines),
            ("Все активности по участникам", all_user_lines),
        ):
            if lines:
                embed.add_field(name=title, value="\n".join(lines), inline=False)
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityTracker(bot))
