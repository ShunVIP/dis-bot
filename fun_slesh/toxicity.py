# -*- coding: utf-8 -*-
# fun_slesh/toxicity.py
"""
Детектор токсичности + троллинг:
  - on_message: анализирует сообщения правилами и теневой ML-моделью
  - При обнаружении правилами: публично реагирует, пародирует через Markov, ведёт счётчик
  - Счётчик сбрасывается раз в неделю

Команды:
  /токсики          — топ токсичных участников за период
  /токсичность_вкл  — (Админ) включить/выключить систему
  /токсичность_порог — (Админ) настроить чувствительность (1-10)
  /токсичность_канал — (Админ) ограничить работу определёнными каналами
"""

import discord
from discord.ext import commands
from discord import app_commands
from core.toxicity_model_service import detect_toxicity
from core.toxicity_service import ToxicityCooldowns, build_troll_response, generate_markov_troll
from core.toxicity_store import (
    ensure_toxicity_storage,
    exclude_toxicity_channel,
    get_toxicity_config,
    get_toxicity_top,
    include_toxicity_channel,
    record_toxic_event,
    save_shadow_prediction,
    set_toxicity_allow_channels,
    set_toxicity_enabled,
    set_toxicity_threshold,
)

# ── Cog ───────────────────────────────────────────────────────────────────────
class Toxicity(commands.Cog):
    toxicity_group = app_commands.Group(
        name="токсичность",
        description="Детектор токсичности и его настройки"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_toxicity_storage()
        self._cooldowns = ToxicityCooldowns(seconds=24 * 3600)

    def _check_cooldown(self, guild_id: int, user_id: int) -> bool:
        """True = можно отвечать."""
        return self._cooldowns.allow(guild_id, user_id)

    async def _send_troll_reply(self, message: discord.Message, response: str):
        try:
            await message.reply(response, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not message.content:
            return

        guild_id = message.guild.id
        user_id  = message.author.id

        enabled, threshold, ch_filter, excluded_channels = get_toxicity_config(guild_id)
        if not enabled:
            return
        if message.channel.id in excluded_channels:
            return

        # Фильтр по каналам
        if ch_filter and message.channel.id not in ch_filter:
            return

        # Детектируем
        prediction = detect_toxicity(message.content)
        save_shadow_prediction(
            message_id=message.id,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            user_id=message.author.id,
            text=message.content,
            prediction=prediction,
        )
        level = prediction["effective_level"]
        if level < threshold:
            return

        # Кулдаун
        if not self._check_cooldown(guild_id, user_id):
            return

        count = record_toxic_event(guild_id, user_id, message.channel.id, level, message.content)
        parody = None

        # Пытаемся сгенерировать пародию (не блокируем основной поток)
        markov = generate_markov_troll(user_id)
        if markov:
            parody = markov
        response = build_troll_response(
            mention=message.author.mention,
            count=count,
            level=level,
            parody=parody
        )

        await self._send_troll_reply(message, response)

    # ── /токсики ──────────────────────────────────────────────────────────────
    @toxicity_group.command(name="топ",
                            description="Топ токсичных участников за неделю")
    @app_commands.describe(
        период="Неделя (по умолчанию текущая) или 'всё время'"
    )
    @app_commands.choices(период=[
        app_commands.Choice(name="Текущая неделя", value="week"),
        app_commands.Choice(name="Всё время",       value="all"),
    ])
    async def токсики(self, interaction: discord.Interaction,
                       период: str = "week"):
        guild_id = interaction.guild.id

        rows = get_toxicity_top(guild_id, период)
        title = "☢️ Топ токсиков этой недели" if период == "week" else "☢️ Топ токсиков за всё время"

        if not rows:
            await interaction.response.send_message(
                "✅ Токсиков не обнаружено. Сервер в порядке!", ephemeral=True)
            return

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, cnt) in enumerate(rows):
            medal = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{medal} <@{uid}> — **{cnt}** раз")

        emb = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=emb)

    # ── /токсичность_вкл ──────────────────────────────────────────────────────
    @toxicity_group.command(name="вкл",
                            description="(Админ) Включить/выключить детектор токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def токсичность_вкл(self, interaction: discord.Interaction,
                                включить: bool):
        set_toxicity_enabled(interaction.guild.id, включить)
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(
            f"{status} детектор токсичности. Настройка сохранена в admin feature settings.", ephemeral=True)

    # ── /токсичность_порог ────────────────────────────────────────────────────
    @toxicity_group.command(name="порог",
                            description="(Админ) Уровень чувствительности (1=мягко, 3=только жёсткое)")
    @app_commands.describe(уровень="1 — любая грубость, 2 — оскорбления, 3 — только жёсткое")
    @app_commands.choices(уровень=[
        app_commands.Choice(name="1 — любая грубость",         value=1),
        app_commands.Choice(name="2 — оскорбления",            value=2),
        app_commands.Choice(name="3 — только жёсткое",         value=3),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def токсичность_порог(self, interaction: discord.Interaction,
                                 уровень: int):
        set_toxicity_threshold(interaction.guild.id, уровень)
        labels = {1: "любая грубость", 2: "оскорбления", 3: "только жёсткое"}
        await interaction.response.send_message(
            f"✅ Порог установлен: **уровень {уровень}** ({labels[уровень]}). Настройка сохранена в admin feature settings.",
            ephemeral=True)

    # ── /токсичность_канал ────────────────────────────────────────────────────
    @toxicity_group.command(name="канал",
                            description="(Админ) Ограничить мониторинг каналами (пусто = все каналы)")
    @app_commands.describe(
        канал="Добавить/убрать канал из мониторинга",
        действие="Добавить или убрать"
    )
    @app_commands.choices(действие=[
        app_commands.Choice(name="Добавить", value="add"),
        app_commands.Choice(name="Убрать",   value="remove"),
        app_commands.Choice(name="Сбросить (все каналы)", value="reset"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def токсичность_канал(self, interaction: discord.Interaction,
                                 действие: str,
                                 канал: discord.TextChannel | None = None):
        guild_id = interaction.guild.id
        current = set(get_toxicity_config(guild_id)[2])

        if действие == "reset":
            current = set()
        elif канал:
            if действие == "add":
                current.add(канал.id)
            else:
                current.discard(канал.id)

        set_toxicity_allow_channels(guild_id, current)

        if not current:
            msg = "✅ Мониторинг ведётся во **всех каналах**."
        else:
            mentions = [f"<#{cid}>" for cid in current]
            msg = "✅ Мониторинг каналов: " + ", ".join(mentions)
        await interaction.response.send_message(msg, ephemeral=True)

    @toxicity_group.command(name="исключить", description="(Админ) Исключить канал из детектора токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def toxicity_exclude_channel(
        self,
        interaction: discord.Interaction,
        канал: discord.TextChannel,
        причина: str = "",
    ):
        exclude_toxicity_channel(interaction.guild.id, канал.id, причина)
        await interaction.response.send_message(
            f"✅ {канал.mention} исключён из детектора токсичности. Настройка сохранена в admin feature settings.", ephemeral=True
        )

    @toxicity_group.command(name="вернуть", description="(Админ) Вернуть канал в детектор токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def toxicity_include_channel(self, interaction: discord.Interaction, канал: discord.TextChannel):
        deleted = include_toxicity_channel(interaction.guild.id, канал.id)
        text = f"✅ {канал.mention} снова участвует в детекторе токсичности." if deleted else "ℹ️ Этого канала не было в исключениях."
        await interaction.response.send_message(text, ephemeral=True)

    @toxicity_group.command(name="исключения", description="(Админ) Показать каналы, исключённые из токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def toxicity_excluded_channels(self, interaction: discord.Interaction):
        ids = get_toxicity_config(interaction.guild.id)[3]
        text = ", ".join(f"<#{cid}>" for cid in sorted(ids)) if ids else "Исключений нет."
        await interaction.response.send_message(f"Каналы вне детектора токсичности: {text}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Toxicity(bot))
