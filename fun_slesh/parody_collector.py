# -*- coding: utf-8 -*-
# fun_slesh/parody_collector.py
"""
Сбор сообщений со всех каналов сервера для обучения модели пародии.
- Хранит сообщения в datebase/messages.db
- Чекпоинт по дате — повторный запуск добирает только новые
- Фильтрует мусор: команды, ссылки, упоминания, короткие сообщения, боты
"""

import re
import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from core.parody_message_store import (
    DB_PATH,
    ensure_message_tables,
    get_all_user_ids,
    get_checkpoint as store_get_checkpoint,
    get_user_messages,
    get_user_stats,
    reset_checkpoints,
    save_messages as store_save_messages,
    update_checkpoint as store_update_checkpoint,
    upsert_user as store_upsert_user,
)
from fun_slesh.parody_channel_settings import (
    clear_parody_channel_excluded,
    filter_parody_channels,
    get_parody_excluded_channel_ids,
    set_parody_channel_excluded,
)
from fun_slesh.parody_filters import get_ignored_channels

UTC = timezone.utc

# ─── Фильтры ──────────────────────────────────────────────────────────────────
_RE_URL     = re.compile(r"https?://\S+", re.IGNORECASE)
_RE_MENTION = re.compile(r"<[@#!&][^>]+>")
_RE_EMOJI   = re.compile(r"<a?:[A-Za-z0-9_]+:[0-9]+>")
_RE_CMD     = re.compile(r"^/")

def _clean(text: str) -> str:
    """Возвращаем текст как есть — храним оригинал."""
    return text.strip()

def _is_valid(text: str) -> bool:
    """True если сообщение стоит сохранять.

    Фильтруем: команды бота (/команда), пустые строки.
    Оставляем: обычные сообщения, ссылки, упоминания — всё остальное.
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    # slash-команды бота: начинаются с / и дальше латиница/кириллица (не URL)
    if _RE_CMD.match(stripped):
        return False
    # дополнительная защита: короткие "/" без пробела — почти наверняка команда
    if stripped.startswith('/') and ' ' not in stripped and len(stripped) < 40:
        return False
    return True

# ─── БД ───────────────────────────────────────────────────────────────────────
def _ensure_db():
    ensure_message_tables()

def _save_messages(rows: list[tuple]) -> int:
    """Пакетная запись. Возвращает кол-во реально вставленных строк."""
    return store_save_messages(rows)

def _update_checkpoint(channel_id: int, last_msg_id: int):
    store_update_checkpoint(channel_id, last_msg_id)

def _get_checkpoint(channel_id: int) -> Optional[int]:
    return store_get_checkpoint(channel_id)

def _upsert_user(user_id: int, username: str):
    store_upsert_user(user_id, username)

# ─── Сборщик ──────────────────────────────────────────────────────────────────
# ─── Rate-limit настройки ─────────────────────────────────────────────────────
# Discord разрешает ~5 запросов/сек на channel.history.
# Каждые HISTORY_CHUNK сообщений делаем паузу SLEEP_BETWEEN_CHUNKS секунд.
# При получении 429 — спим RETRY_ON_429 секунд и повторяем.
HISTORY_CHUNK      = 100    # сообщений между паузами
SLEEP_BETWEEN_CHUNKS = 0.5  # сек — пауза после каждых 100 сообщений
SLEEP_BETWEEN_CHANNELS = 1.5  # сек — пауза между каналами


async def collect_channel(
    channel: discord.TextChannel,
    guild_id: int,
    progress_cb=None,
    batch_size: int = 200,
) -> int:
    """
    Собирает сообщения из одного канала с соблюдением rate limits.
    Использует чекпоинт — пропускает уже собранные.
    Возвращает кол-во новых сохранённых сообщений.
    """
    if channel.name.lower() in get_ignored_channels():
        return 0

    checkpoint = _get_checkpoint(channel.id)
    after_snowflake = discord.Object(id=checkpoint) if checkpoint else None

    batch       = []
    total_saved = 0
    last_id     = checkpoint
    msg_counter = 0
    user_cache: dict = {}

    async def _flush():
        nonlocal total_saved
        for uid, uname in user_cache.items():
            _upsert_user(uid, uname)
        user_cache.clear()
        saved = _save_messages(batch)
        total_saved += saved
        batch.clear()
        if progress_cb:
            await progress_cb(total_saved)

    try:
        async for msg in channel.history(
            limit=None,
            oldest_first=True,
            after=after_snowflake,
        ):
            if msg.author.bot:
                continue

            cleaned = _clean(msg.content or "")
            if not _is_valid(cleaned):
                continue

            ts = msg.created_at.replace(tzinfo=UTC).isoformat()
            batch.append((
                msg.author.id, str(msg.author),
                guild_id, channel.id, msg.id,
                cleaned, ts,
            ))
            user_cache[msg.author.id] = str(msg.author)
            last_id    = msg.id
            msg_counter += 1

            # Пауза каждые HISTORY_CHUNK сообщений — даём Discord передохнуть
            if msg_counter % HISTORY_CHUNK == 0:
                await asyncio.sleep(SLEEP_BETWEEN_CHUNKS)

            if len(batch) >= batch_size:
                await _flush()
                # Дополнительная пауза после записи батча
                await asyncio.sleep(SLEEP_BETWEEN_CHUNKS)

    except discord.errors.HTTPException as e:
        if e.status == 429:
            retry = float(e.response.headers.get("Retry-After", 5))
            print(f"[parody] ⏳ Rate limit #{channel.name} — жду {retry:.1f}с")
            await asyncio.sleep(retry + 0.5)
        else:
            print(f"[parody] ⚠️ HTTP {e.status} в #{channel.name}: {e.text}")
    except discord.Forbidden:
        print(f"[parody] ⛔ Нет доступа к #{channel.name}")
    except Exception as e:
        print(f"[parody] ❌ #{channel.name}: {type(e).__name__}: {e}")

    if batch:
        await _flush()

    if last_id and last_id != checkpoint:
        _update_checkpoint(channel.id, last_id)

    return total_saved


# ─── Cog ──────────────────────────────────────────────────────────────────────
class ParodyCollector(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_db()

    @app_commands.command(
        name="собрать_сообщения",
        description="(Админ) Собрать сообщения со всех каналов для обучения модели пародии"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def собрать_сообщения(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        guild = interaction.guild
        all_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history]
        _ignored = get_ignored_channels()
        allowed_by_name = [ch for ch in all_channels if ch.name.lower() not in _ignored]
        channels = filter_parody_channels(guild, allowed_by_name)
        excluded_ids = get_parody_excluded_channel_ids(guild.id)
        skipped_names = [ch.name for ch in all_channels if ch.name.lower() in _ignored or ch.id in excluded_ids]

        if not channels:
            await interaction.followup.send("❌ Нет доступных текстовых каналов.")
            return

        total_channels = len(channels)
        total_saved = 0
        results = []
        start_time = datetime.now(UTC)
        last_edit = start_time

        def _progress_bar(current: int, total: int, width: int = 14) -> str:
            filled = int(width * current / max(total, 1))
            return "█" * filled + "░" * (width - filled)

        def _elapsed(since: datetime) -> str:
            secs = int((datetime.now(UTC) - since).total_seconds())
            m, s = divmod(secs, 60)
            return f"{m}м {s}с" if m else f"{s}с"

        def _speed(total: int, since: datetime) -> str:
            secs = max((datetime.now(UTC) - since).total_seconds(), 1)
            return f"{int(total / secs * 60)} сообщ/мин"

        status_msg = await interaction.followup.send(
            embed=discord.Embed(
                title="⏳ Сбор сообщений...",
                description=f"Каналов найдено: **{total_channels}**\n░░░░░░░░░░░░░░ 0%\n\nИдёт подготовка...",
                color=discord.Color.orange()
            ),
            wait=True
        )

        for i, ch in enumerate(channels, 1):
            saved = await collect_channel(ch, guild.id)
            total_saved += saved
            results.append((ch.name, saved))

            # Пауза между каналами — снижает риск rate limit
            if i < total_channels:
                await asyncio.sleep(SLEEP_BETWEEN_CHANNELS)

            # Обновляем не чаще раза в 3 секунды чтобы не флудить API
            now = datetime.now(UTC)
            if (now - last_edit).total_seconds() >= 3 or i == total_channels:
                last_edit = now
                bar = _progress_bar(i, total_channels)
                pct = int(i / total_channels * 100)
                elapsed = _elapsed(start_time)
                speed = _speed(total_saved, start_time)

                # Последние 5 обработанных каналов
                recent = results[-5:]
                recent_lines = "\n".join(
                    f"  `#{name}` +{cnt}" for name, cnt in recent
                )

                emb = discord.Embed(
                    title="⏳ Сбор сообщений..." if i < total_channels else "✅ Сбор завершён!",
                    color=discord.Color.orange() if i < total_channels else discord.Color.green()
                )
                emb.add_field(
                    name="Прогресс",
                    value=f"{bar} **{pct}%**\nКанал **{i}** из **{total_channels}**",
                    inline=False
                )
                emb.add_field(name="Собрано сообщений", value=f"**{total_saved}**", inline=True)
                emb.add_field(name="Скорость", value=speed, inline=True)
                emb.add_field(name="Прошло времени", value=elapsed, inline=True)
                emb.add_field(
                    name="Последние каналы",
                    value=recent_lines or "—",
                    inline=False
                )
                if i == total_channels:
                    uids = get_all_user_ids()
                    emb.add_field(
                        name="Итог",
                        value=f"Уникальных пользователей в базе: **{len(uids)}**",
                        inline=False
                    )
                    if skipped_names:
                        emb.add_field(
                            name="⛔ Пропущено (стоп-лист)",
                            value=", ".join(f"`#{n}`" for n in skipped_names),
                            inline=False
                        )
                    emb.set_footer(text=f"Общее время: {elapsed}")

                try:
                    await status_msg.edit(content=None, embed=emb)
                except Exception:
                    pass


    @app_commands.command(
        name="сбросить_чекпоинты",
        description="(Админ) Сбросить чекпоинты — следующий сбор перечитает все каналы с начала"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def сбросить_чекпоинты(self, interaction: discord.Interaction):
        deleted = reset_checkpoints()
        await interaction.response.send_message(
            f"✅ Сброшено **{deleted}** чекпоинтов. "
            f"Следующий `/собрать_сообщения` перечитает все каналы с самого начала.",
            ephemeral=True
        )


    @app_commands.command(name="пародия_исключить_канал", description="(Админ) Не собирать сообщения из канала для пародий")
    @app_commands.checks.has_permissions(administrator=True)
    async def parody_exclude_channel(
        self,
        interaction: discord.Interaction,
        канал: discord.TextChannel,
        причина: str = "",
    ):
        set_parody_channel_excluded(interaction.guild.id, канал.id, причина)
        await interaction.response.send_message(
            f"✅ {канал.mention} исключён из обучения пародий.", ephemeral=True
        )

    @app_commands.command(name="пародия_вернуть_канал", description="(Админ) Снова собирать сообщения из канала для пародий")
    @app_commands.checks.has_permissions(administrator=True)
    async def parody_include_channel(self, interaction: discord.Interaction, канал: discord.TextChannel):
        deleted = clear_parody_channel_excluded(interaction.guild.id, канал.id)
        text = f"✅ {канал.mention} снова участвует в обучении пародий." if deleted else "ℹ️ Этого канала не было в исключениях."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="пародия_исключения", description="(Админ) Показать каналы, исключённые из обучения пародий")
    @app_commands.checks.has_permissions(administrator=True)
    async def parody_exclusions(self, interaction: discord.Interaction):
        ids = get_parody_excluded_channel_ids(interaction.guild.id)
        text = ", ".join(f"<#{cid}>" for cid in sorted(ids)) if ids else "Исключений нет."
        await interaction.response.send_message(f"Каналы вне обучения пародий: {text}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ParodyCollector(bot))
