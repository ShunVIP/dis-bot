# -*- coding: utf-8 -*-
"""
Cog: message_and_voice_stats (fixed)

Правки:
- Добавлены _safe_defer/_safe_reply для защиты от Unknown interaction (10062)
- Все ответы переведены на безопасные хелперы
- Чекпоинт индексации не падает, если в канале нет сообщений
- Небольшие косметические правки

Команды:
- /индекс_сообщений [канал] [макс_дней]
- /топ_актив [дней] [канал]
- /топ_слова [дней] [канал]
- /топ_эмодзи [дней] [канал]
- /voice_топ [дней]
- /voice_я [дней]

⚠ Требуется intents.message_content=True для слов/эмодзи.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
from core.activity_rewards_service import reward_message, reward_voice, update_reward_settings
from core.activity_rewards_store import (
    ensure_activity_rewards_storage,
    exclude_activity_channel,
    get_activity_reward_config,
    has_activity_reward_config,
    include_activity_channel,
    is_activity_channel_excluded as _is_activity_channel_excluded,
    list_activity_channel_exclusions,
)
from core import message_stats_service, message_stats_store

UTC = message_stats_service.UTC
MSK = message_stats_service.MSK

# -------------------------
# Безопасные ответы для slash-команд
# -------------------------
async def _safe_defer(inter: discord.Interaction, *, ephemeral: bool = False, thinking: bool = False):
    try:
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=ephemeral, thinking=thinking)
    except (discord.NotFound, discord.InteractionResponded):
        # токен протух или уже отвечали
        pass

async def _safe_reply(
    inter: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    ephemeral: bool = False,
):
    try:
        if inter.response.is_done():
            await inter.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        else:
            await inter.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
    except discord.NotFound:
        # Интеракция уже недействительна. Для публичных сообщений попробуем в канал.
        if not ephemeral and isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
            try:
                await inter.channel.send(content=content, embed=embed)
            except Exception:
                pass

def _ensure_db():
    message_stats_store.ensure_tables()
    ensure_activity_rewards_storage()


def _excluded_channel_ids(guild_id: int) -> list[int]:
    return [int(row["channel_id"]) for row in list_activity_channel_exclusions(guild_id)]

# -------------------------
# Cog
# -------------------------
class MessageAndVoiceStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_db()
        self._voice_sessions: dict[tuple[int, int], tuple[int, datetime]] = {}

    async def _finish_voice_segment(
        self,
        user_id: int,
        guild_id: int,
        channel_id: int,
        started_at: datetime,
        ended_at: datetime,
    ) -> int:
        seconds = message_stats_store.record_voice_session(
            user_id,
            guild_id,
            channel_id,
            started_at,
            ended_at,
        )
        if seconds:
            await self._award_voice(user_id, guild_id, seconds)
        return seconds

    # ========= ТЕКСТ: индексация истории =========
    @app_commands.command(name="индекс_сообщений", description="Индексирует историю канала батчами и пишет суточные агрегаты")
    @app_commands.describe(
        канал="Если не задан — берётся текущий канал",
        макс_дней="Сколько последних дней смотреть (например 180). 0 = вся доступная история"
    )
    async def индекс_сообщений(self, interaction: discord.Interaction,
                               канал: Optional[discord.TextChannel] = None,
                               макс_дней: app_commands.Range[int, 0, 3650] = 180):
        await _safe_defer(interaction, thinking=True)
        channel = канал or interaction.channel  # type: ignore
        if not isinstance(channel, discord.TextChannel):
            await _safe_reply(interaction, content="❌ Эта команда работает только в текстовых каналах.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        assert guild_id is not None
        if _is_activity_channel_excluded(guild_id, channel.id):
            await _safe_reply(interaction, content=f"⛔ Канал {channel.mention} исключен из статистики и пассивных наград.", ephemeral=True)
            return
        limit_days = int(макс_дней)
        after_dt = None
        if limit_days > 0:
            after_dt = datetime.now(UTC) - timedelta(days=limit_days)

        BATCH = 1000
        processed = 0
        new_rows = 0
        last_processed_id: Optional[int] = None
        pending: list[dict[str, object]] = []
        checkpoint_last_id = message_stats_store.get_checkpoint(channel.id)

        # идём по истории
        async for message in channel.history(limit=None, oldest_first=True, after=after_dt):
            # скипаем уже проиндексированное
            if checkpoint_last_id is not None and message.id <= checkpoint_last_id:
                continue
            if message.author.bot:
                continue

            pending.append(
                message_stats_service.build_message_record(
                    message_id=message.id,
                    user_id=message.author.id,
                    guild_id=guild_id,
                    channel_id=channel.id,
                    content=message.content or "",
                    created_at=message.created_at,
                )
            )
            processed += 1
            last_processed_id = message.id
            if len(pending) >= BATCH:
                new_rows += message_stats_store.record_message_batch(
                    pending,
                    checkpoint_channel_id=channel.id,
                    checkpoint_message_id=last_processed_id,
                )
                pending.clear()
                await asyncio.sleep(1.0)

        if pending and last_processed_id is not None:
            new_rows += message_stats_store.record_message_batch(
                pending,
                checkpoint_channel_id=channel.id,
                checkpoint_message_id=last_processed_id,
            )

        await _safe_reply(
            interaction,
            content=(
                f"✅ Индексация завершена. Канал: <#{channel.id}>\n"
                f"Сообщений обработано: {processed}\nНовых записей/агрегатов: {new_rows}"
            ),
        )

    # ========= ТЕКСТ: отчёты =========
    @app_commands.command(name="топ_актив", description="Топ по количеству сообщений за N дней (по серверу или каналу)")
    @app_commands.describe(дней="Сколько дней (например 7)", канал="Если указан — фильтр по каналу")
    async def топ_актив(self, interaction: discord.Interaction,
                        дней: app_commands.Range[int, 1, 3650] = 7,
                        канал: Optional[discord.TextChannel] = None):
        await _safe_defer(interaction)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(MSK).date() - timedelta(days=int(дней) - 1)).isoformat()

        rows = message_stats_store.list_user_metric(
            guild_id,
            since,
            "messages",
            channel_id=канал.id if канал else None,
            excluded_channel_ids=_excluded_channel_ids(guild_id) if канал is None else (),
        )

        if not rows:
            await _safe_reply(interaction, content="📭 Нет данных за выбранный период. Сначала проиндексируй историю.")
            return

        lines = []
        for i, (uid, total) in enumerate(rows, start=1):
            name = f"<@{uid}>"
            lines.append(f"**{i}.** {name} — {total} сообщений")
        emb = discord.Embed(title=f"📊 Топ активных за {дней} дн.", description="\n".join(lines), color=discord.Color.blurple())
        await _safe_reply(interaction, embed=emb)

    @app_commands.command(name="топ_слова", description="Топ по количеству слов за N дней")
    async def топ_слова(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 3650] = 7,
                         канал: Optional[discord.TextChannel] = None):
        await _safe_defer(interaction)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(MSK).date() - timedelta(days=int(дней) - 1)).isoformat()

        rows = message_stats_store.list_user_metric(
            guild_id,
            since,
            "words",
            channel_id=канал.id if канал else None,
            excluded_channel_ids=_excluded_channel_ids(guild_id) if канал is None else (),
        )

        if not rows:
            await _safe_reply(interaction, content="📭 Нет данных. Убедись, что включён message_content и выполнена индексация.")
            return
        lines = [f"**{i}.** <@{uid}> — {total} слов" for i, (uid, total) in enumerate(rows, start=1)]
        await _safe_reply(
            interaction,
            embed=discord.Embed(title=f"📝 Топ слов за {дней} дн.", description="\n".join(lines), color=discord.Color.green()),
        )

    @app_commands.command(name="топ_эмодзи", description="Топ по использованию эмодзи за N дней")
    async def топ_эмодзи(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 3650] = 7,
                          канал: Optional[discord.TextChannel] = None):
        await _safe_defer(interaction)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(MSK).date() - timedelta(days=int(дней) - 1)).isoformat()

        rows = message_stats_store.list_user_metric(
            guild_id,
            since,
            "emojis",
            channel_id=канал.id if канал else None,
            excluded_channel_ids=_excluded_channel_ids(guild_id) if канал is None else (),
        )

        if not rows:
            await _safe_reply(interaction, content="📭 Нет данных за период.")
            return
        lines = [f"**{i}.** <@{uid}> — {total} эмодзи" for i, (uid, total) in enumerate(rows, start=1)]
        await _safe_reply(
            interaction,
            embed=discord.Embed(title=f"😎 Топ эмодзи за {дней} дн.", description="\n".join(lines), color=discord.Color.orange()),
        )

    @app_commands.command(name="стат_исключить", description="(Админ) Исключить канал из статистики и пассивных наград")
    @app_commands.describe(
        канал="Канал, который не должен учитываться",
        причина="Короткая пометка для админов"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def стат_исключить(self, interaction: discord.Interaction, канал: discord.TextChannel, причина: str = ""):
        exclude_activity_channel(interaction.guild.id, канал.id, причина)
        await interaction.response.send_message(f"✅ {канал.mention} исключен из статистики и пассивных наград.", ephemeral=True)

    @app_commands.command(name="стат_вернуть", description="(Админ) Вернуть канал в статистику и пассивные награды")
    @app_commands.describe(канал="Канал, который снова нужно учитывать")
    @app_commands.checks.has_permissions(administrator=True)
    async def стат_вернуть(self, interaction: discord.Interaction, канал: discord.TextChannel):
        include_activity_channel(interaction.guild.id, канал.id)
        await interaction.response.send_message(f"✅ {канал.mention} снова учитывается.", ephemeral=True)

    @app_commands.command(name="стат_исключения", description="(Админ) Показать исключенные из статистики каналы")
    @app_commands.checks.has_permissions(administrator=True)
    async def стат_исключения(self, interaction: discord.Interaction):
        rows = list_activity_channel_exclusions(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("✅ Исключенных каналов нет.", ephemeral=True)
            return
        lines = []
        for row in rows:
            channel_id, reason = row["channel_id"], row["reason"]
            note = f" — {reason}" if reason else ""
            lines.append(f"<#{channel_id}>{note}")
        await interaction.response.send_message("Исключенные каналы:\n" + "\n".join(lines), ephemeral=True)

    # ========= ТЕКСТ: пассивные награды =========
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        guild_id = message.guild.id
        user_id  = message.author.id
        channel_id = message.channel.id
        if _is_activity_channel_excluded(guild_id, channel_id):
            return

        recorded = message_stats_store.record_message(
            message_stats_service.build_message_record(
                message_id=message.id,
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                content=message.content or "",
                created_at=message.created_at,
            )
        )
        if recorded:
            reward_message(user_id, guild_id)

    # ========= ВОЙС: онлайн‑трекер =========
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        guild_id = member.guild.id
        key = (guild_id, member.id)
        now = datetime.now(UTC)

        # вышел из войса
        if before.channel and not after.channel:
            sess = self._voice_sessions.pop(key, None)
            if sess:
                ch_id, started_at = sess
                await self._finish_voice_segment(
                    member.id, guild_id, ch_id, started_at, now
                )
            return

        # вошёл в войс
        if after.channel and not before.channel:
            self._voice_sessions[key] = (after.channel.id, now)
            return

        # перемещение
        if before.channel and after.channel and before.channel.id != after.channel.id:
            sess = self._voice_sessions.pop(key, None)
            if sess:
                ch_id, started_at = sess
                await self._finish_voice_segment(
                    member.id, guild_id, ch_id, started_at, now
                )
            self._voice_sessions[key] = (after.channel.id, now)

    @app_commands.command(name="voice_топ", description="Топ по времени в голосе за N дней")
    async def voice_top(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 3650] = 7):
        await _safe_defer(interaction)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(MSK).date() - timedelta(days=int(дней) - 1)).isoformat()
        rows = message_stats_store.list_voice_totals(guild_id, since)
        if not rows:
            await _safe_reply(interaction, content="📭 Пока нет данных. Трекинг начнётся после загрузки этого cog.")
            return
        lines = [
            f"**{i}.** <@{uid}> — {message_stats_service.format_duration(seconds)}"
            for i, (uid, seconds) in enumerate(rows, start=1)
        ]
        await _safe_reply(interaction, embed=discord.Embed(title=f"🎙️ Топ войса за {дней} дн.", description="\n".join(lines), color=discord.Color.gold()))

    @app_commands.command(name="voice_я", description="Моя статистика по войсу за N дней")
    async def voice_me(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 3650] = 30):
        await _safe_defer(interaction, ephemeral=True)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(MSK).date() - timedelta(days=int(дней) - 1)).isoformat()
        total = message_stats_store.get_user_voice_total(
            guild_id, interaction.user.id, since
        )
        await _safe_reply(
            interaction,
            content=(
                f"За {дней} дн. ты провёл в войсе "
                f"**{message_stats_service.format_duration(total)}**"
            ),
            ephemeral=True,
        )


    async def _award_voice(self, user_id: int, guild_id: int, seconds: int):
        """Начисляет Сиськи за голос."""
        reward_voice(user_id, guild_id, seconds)

    # ── /награды_настроить ────────────────────────────────────────────────────
    @app_commands.command(name="награды_настроить",
                          description="(Админ) Настроить пассивные награды за активность")
    @app_commands.describe(
        монеты_за_сообщения="Включить Сиськи за сообщения",
        монет_за_n_сообщений="Сколько Сисек за каждые N сообщений",
        каждые_n_сообщений="Каждые сколько сообщений давать Сиськи",
        репа_за_сообщения="Давать +1 Размера автоматически",
        репа_каждые_n="Размер каждые N сообщений (0 = выключено)",
        монеты_за_голос="Включить Сиськи за голос",
        монет_за_минут="Сисек за каждые N минут в голосе",
        голос_каждые_мин="Каждые сколько минут давать Сиськи",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def награды_настроить(
        self, interaction: discord.Interaction,
        монеты_за_сообщения: bool = None,
        монет_за_n_сообщений: app_commands.Range[int, 1, 100] = None,
        каждые_n_сообщений: app_commands.Range[int, 1, 1000] = None,
        репа_за_сообщения: app_commands.Range[int, 0, 5] = None,
        репа_каждые_n: app_commands.Range[int, 0, 1000] = None,
        монеты_за_голос: bool = None,
        монет_за_минут: app_commands.Range[int, 1, 100] = None,
        голос_каждые_мин: app_commands.Range[int, 1, 120] = None,
    ):
        guild_id = interaction.guild.id
        cfg = update_reward_settings(guild_id, {
            "msg_enabled": монеты_за_сообщения,
            "msg_coins": монет_за_n_сообщений,
            "msg_per_n": каждые_n_сообщений,
            "msg_rep": репа_за_сообщения,
            "msg_rep_per_n": репа_каждые_n,
            "voice_enabled": монеты_за_голос,
            "voice_coins": монет_за_минут,
            "voice_per_min": голос_каждые_мин,
        })
        me, mp, mc, mrp, mr, ve, vp, vc = (
            cfg["msg_enabled"], cfg["msg_per_n"], cfg["msg_coins"], cfg["msg_rep_per_n"],
            cfg["msg_rep"], cfg["voice_enabled"], cfg["voice_per_min"], cfg["voice_coins"],
        )
        emb = discord.Embed(title="⚙️ Пассивные награды", color=discord.Color.teal())
        msg_st   = "✅ Включены" if me else "⛔ Выключены"
        rep_line = f"+{mr} Размер каждые {mrp} сообщений" if mrp else "Размер: выключена"
        emb.add_field(
            name="💬 Сообщения",
            value=f"{msg_st}\n+{mc} Сисек каждые {mp} сообщений\n{rep_line}",
            inline=False
        )
        voice_st = "✅ Включены" if ve else "⛔ Выключены"
        emb.add_field(
            name="🎙️ Голос",
            value=f"{voice_st}\n+{vc} Сисек каждые {vp} минут",
            inline=False
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @app_commands.command(name="награды_статус",
                          description="Текущие настройки пассивных наград")
    async def награды_статус(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if not has_activity_reward_config(guild_id):
            await interaction.response.send_message(
                "⚙️ Пассивные награды не настроены. Используй `/награды_настроить`.",
                ephemeral=True)
            return
        cfg_data = get_activity_reward_config(guild_id)
        cfg = (
            cfg_data["msg_enabled"], cfg_data["msg_per_n"], cfg_data["msg_coins"],
            cfg_data["msg_rep_per_n"], cfg_data["msg_rep"], cfg_data["voice_enabled"],
            cfg_data["voice_per_min"], cfg_data["voice_coins"],
        )
        me, mp, mc, mrp, mr, ve, vp, vc = cfg
        emb = discord.Embed(title="⚙️ Пассивные награды", color=discord.Color.teal())
        msg_v = ("✅" if me else "⛔") + f"\n+{mc} Сисек / {mp} сообщений"
        if mrp:
            msg_v += f"\n+{mr} Размер / {mrp} сообщений"
        emb.add_field(name="💬 Сообщения", value=msg_v, inline=True)
        voice_v = ("✅" if ve else "⛔") + f"\n+{vc} Сисек / {vp} минут"
        emb.add_field(name="🎙️ Голос", value=voice_v, inline=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageAndVoiceStats(bot))
