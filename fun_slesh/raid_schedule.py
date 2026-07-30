"""Discord UI for the shared Lucy/Raimi raid calendar."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from core.raid_schedule_service import (
    available_raid_days,
    build_raid_week,
    format_raid_day,
    parse_reference_date,
    week_bounds,
)


MSK = ZoneInfo("Europe/Moscow")


class RaidSchedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidSchedule(bot))
