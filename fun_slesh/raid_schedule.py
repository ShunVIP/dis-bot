"""Discord UI for the shared Lucy/Raimi raid calendar."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.raid_schedule_service import (
    available_raid_days,
    build_raid_post_state,
    build_raid_week,
    format_raid_day,
    parse_reference_date,
    raid_post_is_due,
    week_bounds,
)
from core.settings_store import (
    get_feature_policy,
    get_feature_runtime_state,
    set_feature_runtime_state,
)
from utils.logger import log as _base_log


MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc
FEATURE_RAID_SCHEDULE = "raid_schedule"
log = _base_log.bind(src="raid_schedule")


def build_raid_embed(reference: date) -> discord.Embed:
    days = build_raid_week(reference)
    possible = available_raid_days(reference)
    monday, sunday = week_bounds(reference)
    embed = discord.Embed(
        title=f"🗓️ Рейды · {monday:%d.%m}–{sunday:%d.%m.%Y}",
        description="\n".join(format_raid_day(item) for item in days),
        color=discord.Color.green() if possible else discord.Color.orange(),
    )
    if possible:
        embed.add_field(
            name="🎯 Возможные даты",
            value="\n".join(
                f"**{item.day:%d.%m} ({item.availability_note})**"
                for item in possible
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="🎯 Возможные даты",
            value="На этой неделе одновременного окна нет.",
            inline=False,
        )
    embed.set_footer(
        text=(
            "Цикл 4 дня от 30.07.2026 · "
            "Рейми: смена / выходной / выходной / отсыпной · "
            "Люси: выходной / офис / удалёнка / удалёнка"
        )
    )
    return embed


class RaidSchedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._post_lock = asyncio.Lock()
        self._startup_task: asyncio.Task | None = None
        self.weekly_raid_post.start()

    def cog_unload(self):
        self.weekly_raid_post.cancel()
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    async def _post_current_week(self, *, trigger: str) -> None:
        reference = datetime.now(MSK).date()
        async with self._post_lock:
            for guild in self.bot.guilds:
                policy = get_feature_policy(guild.id, FEATURE_RAID_SCHEDULE)
                if not policy.enabled or not policy.output_channel_id:
                    continue
                channel_id = int(policy.output_channel_id)
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    log.warning(
                        "Raid channel unavailable guild_id={} channel_id={}",
                        guild.id,
                        channel_id,
                    )
                    continue
                state = get_feature_runtime_state(guild.id, FEATURE_RAID_SCHEDULE)
                if not raid_post_is_due(state, reference, channel_id):
                    continue
                try:
                    message = await channel.send(
                        embed=build_raid_embed(reference),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception as exc:
                    log.error(
                        "Raid schedule post failed guild_id={} channel_id={}: {}",
                        guild.id,
                        channel_id,
                        exc,
                    )
                    continue
                set_feature_runtime_state(
                    guild.id,
                    FEATURE_RAID_SCHEDULE,
                    build_raid_post_state(
                        reference,
                        channel_id,
                        message.id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                log.info(
                    "Raid schedule posted guild_id={} channel_id={} message_id={} trigger={}",
                    guild.id,
                    channel_id,
                    message.id,
                    trigger,
                )

    @commands.Cog.listener()
    async def on_ready(self):
        if self._startup_task is None or self._startup_task.done():
            self._startup_task = asyncio.create_task(
                self._post_current_week(trigger="startup")
            )

    @tasks.loop(time=time(hour=9, minute=0, tzinfo=MSK))
    async def weekly_raid_post(self):
        if datetime.now(MSK).weekday() == 0:
            await self._post_current_week(trigger="monday")

    @weekly_raid_post.before_loop
    async def before_weekly_raid_post(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="рейд",
        description="Расписание Люси и Рейми и возможные даты рейда на неделю",
    )
    @app_commands.describe(
        дата="Любая дата нужной недели: ДД.ММ, ДД.ММ.ГГГГ или ГГГГ-ММ-ДД",
    )
    async def рейд(self, interaction: discord.Interaction, дата: str | None = None):
        try:
            reference = parse_reference_date(
                дата,
                today=datetime.now(MSK).date(),
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(embed=build_raid_embed(reference))


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidSchedule(bot))
