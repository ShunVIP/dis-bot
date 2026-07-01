# -*- coding: utf-8 -*-
# fun_slesh/lol_profile.py
import asyncio
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from core.game_profiles import (
    GAME_LOL,
    PROVIDER_RIOT,
    get_game_account,
    get_latest_lol_snapshot,
    get_player_model_profile,
    save_lol_match_features,
    save_lol_snapshot,
    save_player_model_profile,
    unlink_game_account,
    upsert_game_account,
)
from core.lol_player_model import classify_lol_player, extract_lol_match_features
from core.riot_client import RiotApiError, RiotClient, RiotRouting, split_riot_id
from config import RIOT_API_KEY, RIOT_PLATFORM_REGION, RIOT_REGIONAL_ROUTING


def _rank_line(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "Ранк не найден"
    priority = {"RANKED_SOLO_5x5": 0, "RANKED_FLEX_SR": 1}
    entries = sorted(entries, key=lambda x: priority.get(x.get("queueType", ""), 9))
    lines = []
    for item in entries[:2]:
        queue = "SoloQ" if item.get("queueType") == "RANKED_SOLO_5x5" else "Flex"
        tier = item.get("tier", "UNRANKED")
        rank = item.get("rank", "")
        lp = item.get("leaguePoints", 0)
        wins = int(item.get("wins") or 0)
        losses = int(item.get("losses") or 0)
        total = max(wins + losses, 1)
        wr = round(wins / total * 100, 1)
        lines.append(f"{queue}: {tier} {rank} {lp} LP, WR {wr}%")
    return "\n".join(lines)


def _mastery_line(mastery: list[dict[str, Any]]) -> str:
    if not mastery:
        return "Мастерство не загружено"
    return ", ".join(
        f"#{item.get('championId')} lvl {item.get('championLevel')} ({item.get('championPoints')} pts)"
        for item in mastery[:5]
    )


class LolProfile(commands.Cog):
    lol_group = app_commands.Group(name="lol", description="League of Legends профиль и тип игрока")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _client(self, platform: str | None = None, regional: str | None = None) -> RiotClient:
        routing = RiotRouting(
            platform=(platform or RIOT_PLATFORM_REGION or "ru"),
            regional=(regional or RIOT_REGIONAL_ROUTING or "europe"),
        )
        return RiotClient(RIOT_API_KEY, routing)

    @lol_group.command(name="привязать", description="Привязать Riot ID для League of Legends")
    @app_commands.describe(
        riot_id="Riot ID в формате Name#TAG",
        регион="Платформенный регион: ru/euw1/eun1/kr/na1",
        роутинг="Региональный роутинг матчей: europe/asia/americas/sea",
    )
    async def link(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        регион: str = RIOT_PLATFORM_REGION or "ru",
        роутинг: str = RIOT_REGIONAL_ROUTING or "europe",
    ):
        if not RIOT_API_KEY:
            await interaction.response.send_message(
                "Riot API key не настроен. Добавь строку `RIOT_API_KEY=твой_ключ` в `KGTD.env` и перезапусти бота.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            game_name, tag_line = split_riot_id(riot_id)
            client = self._client(регион, роутинг)
            account = await client.account_by_riot_id(game_name, tag_line)
            puuid = account.get("puuid")
            if not puuid:
                raise RiotApiError("Riot account response has no PUUID")
            display_name = f"{account.get('gameName', game_name)}#{account.get('tagLine', tag_line)}"
            upsert_game_account(
                interaction.user.id,
                GAME_LOL,
                PROVIDER_RIOT,
                puuid,
                display_name,
                region=client.routing.platform,
                verified=True,
            )
            await interaction.followup.send(
                f"Готово. Привязан LoL профиль **{display_name}** (`{client.routing.platform}/{client.routing.regional}`).\n"
                f"Теперь запусти `lol обновить`, чтобы собрать статистику.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Не удалось привязать Riot ID: {exc}", ephemeral=True)

    @lol_group.command(name="обновить", description="Обновить LoL статистику и пересчитать тип игрока")
    @app_commands.describe(матчей="Сколько последних матчей взять для анализа")
    async def refresh(self, interaction: discord.Interaction, матчей: app_commands.Range[int, 5, 50] = 20):
        if not RIOT_API_KEY:
            await interaction.response.send_message(
                "Riot API key не настроен. Добавь строку `RIOT_API_KEY=твой_ключ` в `KGTD.env` и перезапусти бота.",
                ephemeral=True,
            )
            return
        account = get_game_account(interaction.user.id, GAME_LOL, PROVIDER_RIOT)
        if not account:
            await interaction.response.send_message(
                "Сначала привяжи Riot ID через `lol привязать`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            client = self._client(account.get("region") or RIOT_PLATFORM_REGION, RIOT_REGIONAL_ROUTING)
            puuid = account["external_id"]

            summoner_task = client.summoner_by_puuid(puuid)
            ranked_task = client.ranked_entries_by_puuid(puuid)
            mastery_task = client.champion_mastery_top(puuid, 5)
            match_ids_task = client.match_ids_by_puuid(puuid, int(матчей))
            summoner, ranked, mastery, match_ids = await asyncio.gather(
                summoner_task, ranked_task, mastery_task, match_ids_task
            )

            match_features = []
            for match_id in match_ids:
                try:
                    match = await client.match(match_id)
                    item = extract_lol_match_features(match, puuid)
                    if item:
                        match_features.append(item)
                except Exception:
                    continue

            features, labels, explanation = classify_lol_player(match_features)
            save_lol_match_features(puuid, match_features)
            snapshot = {
                "account": account,
                "summoner": summoner,
                "ranked": ranked,
                "mastery": mastery,
                "features": features,
                "labels": labels,
                "explanation": explanation,
            }
            save_lol_snapshot(interaction.user.id, puuid, client.routing.platform, snapshot)
            save_player_model_profile(interaction.user.id, GAME_LOL, "lol_rules_v1", features, labels, explanation)

            await interaction.followup.send(
                f"Обновлено: **{account['display_name']}**\n"
                f"Тип: **{labels['primary']}** / {labels['secondary']}\n"
                f"{explanation}",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Не удалось обновить LoL профиль: {exc}", ephemeral=True)

    @lol_group.command(name="профиль", description="Показать LoL профиль и тип игрока")
    @app_commands.describe(пользователь="Чей профиль показать")
    async def profile(self, interaction: discord.Interaction, пользователь: discord.Member | None = None):
        target = пользователь or interaction.user
        account = get_game_account(target.id, GAME_LOL, PROVIDER_RIOT)
        if not account:
            await interaction.response.send_message(
                f"У {target.display_name} пока нет привязанного LoL профиля.",
                ephemeral=True,
            )
            return

        snapshot = get_latest_lol_snapshot(target.id) or {}
        model = get_player_model_profile(target.id, GAME_LOL) or {}
        labels = model.get("labels") or snapshot.get("labels") or {}
        features = model.get("features") or snapshot.get("features") or {}
        explanation = model.get("explanation") or snapshot.get("explanation") or "Статистика ещё не обновлялась."

        embed = discord.Embed(
            title=f"LoL профиль: {account['display_name']}",
            description=f"{target.mention}\n{explanation}",
            color=discord.Color.dark_green(),
        )
        embed.add_field(name="Тип игрока", value=f"**{labels.get('primary', 'нет данных')}**", inline=True)
        embed.add_field(name="Второй тип", value=labels.get("secondary", "нет данных"), inline=True)
        embed.add_field(name="Матчей в анализе", value=str(features.get("matches", 0)), inline=True)
        embed.add_field(name="Winrate", value=f"{features.get('winrate', 0)}%", inline=True)
        embed.add_field(name="KDA", value=str(features.get("avg_kda", 0)), inline=True)
        embed.add_field(name="Роль", value=str(features.get("role_main", "unknown")), inline=True)
        if snapshot:
            embed.add_field(name="Ранг", value=_rank_line(snapshot.get("ranked") or []), inline=False)
            embed.add_field(name="Мастерство", value=_mastery_line(snapshot.get("mastery") or []), inline=False)
        embed.set_footer(text="Источник: Riot API. Сторонние сайты подключать только через разрешённые API/ссылки.")
        await interaction.response.send_message(embed=embed)

    @lol_group.command(name="отвязать", description="Отвязать свой LoL/Riot профиль")
    async def unlink(self, interaction: discord.Interaction):
        deleted = unlink_game_account(interaction.user.id, GAME_LOL, PROVIDER_RIOT)
        text = "LoL профиль отвязан." if deleted else "У тебя не было привязанного LoL профиля."
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LolProfile(bot))
