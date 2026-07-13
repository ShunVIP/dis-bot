# -*- coding: utf-8 -*-
# fun_slesh/parody_engine.py  v4.0
"""
ML-движок пародии.

/пародия  — все модели в одной команде (параметр "модель"):
    🎲 Мем      — markovify state_size=2
    🧠 Разум    — markovify state_size=3 + фильтр осознанности
/батл, /коллаж, /эпоха, /тема, /мем_фраза — спецрежимы
/дообучить   — обучение Markov-моделей (админ)
/профилактика — сброс → сбор → дообучить (всё сразу, админ)
/список_пользователей, /модели_статус
"""

import re
import sys
import asyncio
import random
import ctypes
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# ─── Предотвращение сна Windows ───────────────────────────────────────────────
# ES_CONTINUOUS      = 0x80000000 — применять постоянно
# ES_SYSTEM_REQUIRED = 0x00000001 — система не спит
# ES_DISPLAY_REQUIRED = 0x00000002 — монитор не гасить (опционально)
_ES_CONTINUOUS       = 0x80000000
_ES_SYSTEM_REQUIRED  = 0x00000001

def _prevent_sleep():
    """Запрещает Windows засыпать. Вызывать перед долгой операцией."""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )

def _allow_sleep():
    """Разрешает Windows засыпать снова."""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)

import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fun_slesh.parody_channel_settings import filter_parody_channels
from fun_slesh.parody_filters import apply_word_filters, get_blocked_words, get_downranked_words
from core.runtime_policy import (
    DAILY_MARKOV_RETRAIN_HOUR,
    DAILY_MARKOV_RETRAIN_MINUTE,
    IS_SERVER_RUNTIME,
    is_daily_markov_collection_enabled,
    is_daily_markov_retrain_enabled,
    is_full_maintenance_allowed,
)
from core.parody_feedback_store import (
    ensure_feedback_tables as _ensure_ratings_db,
    get_bad_phrases,
    get_good_phrases,
    save_rating,
)
from core.parody_message_store import (
    get_available_years,
    get_user_messages_by_year,
    get_user_messages_between_years,
    merge_user_messages,
    reset_checkpoints,
)
from core.paths import MODELS_DIR
from core.parody_model_service import (
    DEFAULT_MODEL,
    MARKOV_OK,
    QUALITY_LEVELS,
    build_model as _build_model,
    generate_collage,
    generate_epoch,
    generate_phrase,
    generate_topic,
    load_model as _load_model,
    markov_model_exists,
    model_path as _model_path,
    remove_user_models,
    strip_roles as _strip_roles,
    train_all_users as train_markov_users,
    train_user,
    train_user_all_qualities,
)

from fun_slesh.parody_collector import (
    get_user_messages, get_user_stats, get_all_user_ids,
    collect_channel, _ensure_db as ensure_collector_db,
)

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

_MK_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _server_training_guard_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.orange())

# Дубли аккаунтов — объединяются при старте
KNOWN_DUPLICATES: dict[int, list[int]] = {
    379371451079327748: [311460575152439299],
    362869980414345218: [230653962670309376],
    245175948314542080: [294095352053628929],
    399304144944496651: [366540917261467648],
    225821802432167937: [302166549102592000],
    226003307338924032: [347707345998053377],
}

def model_exists(user_id: int, quality: str = DEFAULT_MODEL) -> bool:
    return quality in QUALITY_LEVELS and markov_model_exists(user_id, quality)

# ─── Объединение дублей ───────────────────────────────────────────────────────
def _merge_accounts(primary_id: int, secondary_id: int) -> int:
    moved = merge_user_messages(primary_id, secondary_id)
    if moved:
        remove_user_models(secondary_id)
    return moved

def apply_known_duplicates():
    for primary, secondaries in KNOWN_DUPLICATES.items():
        for sec in secondaries:
            moved = _merge_accounts(primary, sec)
            if moved > 0:
                print(f"[parody] 🔀 {primary} ← {sec} (+{moved} сообщ.)")

def _train_all_users_sync(min_messages: int = 50) -> dict:
    return train_markov_users(get_all_user_ids(), minimum_messages=min_messages)

async def train_all_users_async(min_messages: int = 50) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_MK_EXECUTOR, _train_all_users_sync, min_messages)

def train_all_users(min_messages: int = 50) -> dict:
    return _train_all_users_sync(min_messages)

# ─── Резолюция пользователя ───────────────────────────────────────────────────
def resolve_user(guild: discord.Guild, value: str):
    value = value.strip()
    if value.isdigit():
        s = get_user_stats(int(value))
        if s["count"] > 0:
            return int(value), s["username"]
    for uid in get_all_user_ids():
        s = get_user_stats(uid)
        if value.lower() in (s["username"] or "").lower():
            return uid, s["username"]
    return None, None

# ─── View: кнопки рейтинга ────────────────────────────────────────────────────
class RatingView(discord.ui.View):
    def __init__(self, user_id: int, quality: str, phrase: str):
        super().__init__(timeout=600)
        self.user_id  = user_id
        self.quality  = quality
        self.phrase   = phrase
        self.voted    = set()
        self.likes    = 0
        self.dislikes = 0

    async def _update_buttons(self, interaction: discord.Interaction):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label and child.label.startswith("👍"):
                    child.label = f"👍 {self.likes}" if self.likes else "👍"
                elif child.label and child.label.startswith("👎"):
                    child.label = f"👎 {self.dislikes}" if self.dislikes else "👎"
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success)
    async def thumbs_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voted:
            await interaction.response.send_message("Ты уже голосовал за эту фразу!", ephemeral=True)
            return
        self.voted.add(interaction.user.id)
        self.likes += 1
        save_rating(self.user_id, self.quality, self.phrase, +1, interaction.user.id)
        await interaction.response.send_message("👍 Хорошая фраза — попадёт в обучение!", ephemeral=True)
        await self._update_buttons(interaction)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger)
    async def thumbs_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voted:
            await interaction.response.send_message("Ты уже голосовал за эту фразу!", ephemeral=True)
            return
        self.voted.add(interaction.user.id)
        self.dislikes += 1
        save_rating(self.user_id, self.quality, self.phrase, -1, interaction.user.id)
        await interaction.response.send_message("👎 Отмечено — фраза исключится из модели.", ephemeral=True)
        await self._update_buttons(interaction)

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        self.stop()

# ─── Вспомогательная: полное дообучение всех моделей ─────────────────────────
async def _do_full_retrain(guild: discord.Guild, collect: bool = False) -> dict:
    """
    Запускает полный цикл: [сбор] → Markov.
    Возвращает статистику.
    """
    stats = {"collected": 0, "markovify": 0}

    collect_allowed = collect and (not IS_SERVER_RUNTIME or is_full_maintenance_allowed())

    # 1. Сбор (если нужно)
    if collect_allowed:
        channels = filter_parody_channels(
            guild,
            [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history],
        )
        for ch in channels:
            stats["collected"] += await collect_channel(ch, guild.id)

    # 2. Markovify в executor
    mk_results = await train_all_users_async(min_messages=50)
    stats["markovify"] = len(mk_results)

    return stats


async def _do_safe_markov_refresh(guild: discord.Guild, collect: bool = True) -> dict:
    """
    Безопасный ежедневный цикл для VPS:
    [сбор новых сообщений по чекпоинтам] -> [дообучение только markovify]
    """
    stats = {"collected": 0, "markovify": 0}

    if collect:
        channels = filter_parody_channels(
            guild,
            [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history],
        )
        for ch in channels:
            stats["collected"] += await collect_channel(ch, guild.id)

    mk_results = await train_all_users_async(min_messages=50)
    stats["markovify"] = len(mk_results)
    return stats

# ─── Безопасная отправка (для долгих команд) ─────────────────────────────────
async def _safe_send(interaction: discord.Interaction,
                     status_msg: discord.Message | None,
                     embed: discord.Embed | None = None,
                     channel: discord.TextChannel | None = None,
                     content: str | None = None,
                     view: discord.ui.View | None = None):
    """
    Пытается обновить статусное сообщение.
    Если webhook протух (>15 мин) — отправляет в канал напрямую.
    """
    # 1. Пробуем отредактировать статус
    if status_msg:
        try:
            await status_msg.edit(content=content, embed=embed, view=view)
            return
        except Exception:
            pass

    # 2. Пробуем followup
    try:
        await interaction.followup.send(content=content, embed=embed, view=view)
        return
    except Exception:
        pass

    # 3. Fallback — напрямую в канал (всегда работает)
    try:
        ch = channel or interaction.channel
        if ch:
            mention = interaction.user.mention if interaction.user else ""
            channel_content = " ".join(part for part in [mention, content] if part).strip() or None
            await ch.send(content=channel_content, embed=embed, view=view)
    except Exception as e:
        print(f"[parody] ❌ Не удалось отправить финальное сообщение: {e}")


# ─── Cog ──────────────────────────────────────────────────────────────────────
class ParodyEngine(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_collector_db()
        _ensure_ratings_db()
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        if not MARKOV_OK:
            print("[parody] ⚠️  markovify не установлен!")
        apply_known_duplicates()
        self._scheduler = AsyncIOScheduler(timezone=MSK)
        self._scheduler.add_job(self._weekly_retrain, "cron", day_of_week="sun", hour=3, minute=0)
        if is_daily_markov_retrain_enabled():
            self._scheduler.add_job(
                self._daily_safe_markov_retrain,
                "cron",
                hour=DAILY_MARKOV_RETRAIN_HOUR,
                minute=DAILY_MARKOV_RETRAIN_MINUTE,
            )
        self._scheduler.start()

    async def _weekly_retrain(self):
        print("[parody] 🔄 Еженедельное дообучение...")
        for guild in self.bot.guilds:
            stats = await _do_full_retrain(guild, collect=not IS_SERVER_RUNTIME or is_full_maintenance_allowed())
            print(f"[parody] {guild.name}: +{stats['collected']} сообщ | "
                  f"markov:{stats['markovify']}")
        print("[parody] ✅ Готово")

    async def _daily_safe_markov_retrain(self):
        print("[parody] 🌙 Ежедневный безопасный цикл Markov...")
        for guild in self.bot.guilds:
            stats = await _do_safe_markov_refresh(guild, collect=is_daily_markov_collection_enabled())
            print(
                f"[parody] {guild.name}: safe daily | +{stats['collected']} сообщ | "
                f"mk:{stats['markovify']}"
            )
        print("[parody] ✅ Safe daily Markov готово")

    # ── /пародия ──────────────────────────────────────────────────────────────
    @app_commands.command(name="пародия", description="Сгенерировать фразу в стиле пользователя")
    @app_commands.describe(
        пользователь="Участник сервера",
        ник_или_id="Ник или ID (для ушедших с сервера)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем — абсурдный рандом",       value="мем"),
        app_commands.Choice(name="🧠 Разум — максимум осознанности", value="разум"),
    ])
    async def пародия(self, interaction: discord.Interaction,
                      пользователь: discord.Member | None = None,
                      ник_или_id: str | None = None,
                      модель: str = DEFAULT_MODEL):

        # Резолюция пользователя
        target_id, display_name, avatar_url = None, None, None
        if пользователь:
            target_id, display_name, avatar_url = пользователь.id, пользователь.display_name, пользователь.display_avatar.url
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
            if not target_id:
                await interaction.response.send_message(
                    f"😕 **{ник_или_id}** не найден. Используй `/список_пользователей`.", ephemeral=True)
                return
        else:
            await interaction.response.send_message("❌ Укажи пользователя через @ или ник_или_id.", ephemeral=True)
            return

        # Сразу подтверждаем interaction, чтобы медленные I/O и генерация не роняли slash-команду.
        await interaction.response.defer(thinking=True)

        status_msg = None
        try:
            status_msg = await interaction.original_response()
        except Exception:
            pass

        # Для markovify проверяем ПОСЛЕ defer
        if модель in QUALITY_LEVELS and not await asyncio.to_thread(model_exists, target_id, модель):
            msgs = await asyncio.to_thread(get_user_messages, target_id)
            if len(msgs) < 50:
                await interaction.followup.send(
                    f"😕 Мало данных для **{display_name}**: {len(msgs)} сообщ. (нужно ≥50).", ephemeral=True)
                return

        # Генерация
        phrase, color, icon, footer = None, discord.Color.purple(), "💬", ""

        if not await asyncio.to_thread(model_exists, target_id, модель):
            msgs = await asyncio.to_thread(get_user_messages, target_id)
            trained = await asyncio.to_thread(train_user, target_id, msgs, модель)
            if not trained:
                await interaction.followup.send("❌ Не удалось обучить модель.")
                return
        phrase = await asyncio.to_thread(generate_phrase, target_id, модель)
        q = QUALITY_LEVELS[модель]
        color, icon, footer = discord.Color.purple(), "💬", f"{q['emoji']} {модель.capitalize()} · {q['desc']}"

        if not phrase:
            await interaction.followup.send("🤔 Не удалось сгенерировать. Попробуй ещё раз.")
            return

        emb = discord.Embed(description=f'*"{phrase}"*', color=color)
        if avatar_url:
            emb.set_author(name=f"{display_name} (пародия)", icon_url=avatar_url)
        else:
            emb.set_author(name=f"{display_name} (пародия)")
        emb.set_footer(text=f"{footer} · Оцени фразу!")
        await _safe_send(
            interaction,
            status_msg,
            embed=emb,
            view=RatingView(target_id, модель, phrase),
        )

    # ── /батл ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="батл", description="Батл фраз между двумя пользователями — 7 раундов")
    @app_commands.describe(
        пользователь1="Первый участник", пользователь2="Второй участник",
        ник_или_id1="Ник/ID первого (для ушедших)", ник_или_id2="Ник/ID второго (для ушедших)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
    ])
    async def батл(self, interaction: discord.Interaction,
                   пользователь1: discord.Member | None = None,
                   пользователь2: discord.Member | None = None,
                   ник_или_id1: str | None = None,
                   ник_или_id2: str | None = None,
                   модель: str = DEFAULT_MODEL):

        def rp(m, n):
            if m: return m.id, m.display_name
            if n: return resolve_user(interaction.guild, n)
            return None, None

        id1, name1 = rp(пользователь1, ник_или_id1)
        id2, name2 = rp(пользователь2, ник_или_id2)

        if not id1 or not id2:
            await interaction.response.send_message("❌ Укажи двух участников.", ephemeral=True)
            return
        if id1 == id2:
            await interaction.response.send_message("❌ Нельзя батл с самим собой.", ephemeral=True)
            return

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                msgs = get_user_messages(uid)
                if len(msgs) < 50:
                    await interaction.response.send_message(f"😕 Мало данных для **{name}**: {len(msgs)} сообщ.", ephemeral=True)
                    return

        await interaction.response.defer(thinking=True)

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                train_user(uid, get_user_messages(uid), модель)

        q = QUALITY_LEVELS[модель]
        rounds_text = ""
        context_word = None
        for i in range(7):
            uid, name = (id1, name1) if i % 2 == 0 else (id2, name2)
            phrase = generate_phrase(uid, модель, context_word=context_word) or generate_phrase(uid, модель)
            phrase = _strip_roles(phrase or "...")
            words = [w for w in phrase.split() if len(w) > 3]
            context_word = random.choice(words) if words else None
            prefix = "⚔️" if i % 2 == 0 else "🛡️"
            rounds_text += f"{prefix} **{name}:** {phrase}\n\n"

        emb = discord.Embed(title=f"⚔️ БАТЛ: {name1} vs {name2}", description=rounds_text, color=discord.Color.gold())
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · 7 раундов · Оцени батл!")
        await interaction.followup.send(embed=emb, view=RatingView(id1, модель, rounds_text[:500]))

    # ── /коллаж ───────────────────────────────────────────────────────────────
    @app_commands.command(name="коллаж", description="Смешать стиль двух пользователей в одну фразу")
    @app_commands.describe(
        пользователь1="Первый", пользователь2="Второй",
        ник_или_id1="Ник/ID первого", ник_или_id2="Ник/ID второго",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
    ])
    async def коллаж(self, interaction: discord.Interaction,
                     пользователь1: discord.Member | None = None,
                     пользователь2: discord.Member | None = None,
                     ник_или_id1: str | None = None,
                     ник_или_id2: str | None = None,
                     модель: str = DEFAULT_MODEL):

        def rp(m, n):
            if m: return m.id, m.display_name
            if n: return resolve_user(interaction.guild, n)
            return None, None

        id1, name1 = rp(пользователь1, ник_или_id1)
        id2, name2 = rp(пользователь2, ник_или_id2)

        if not id1 or not id2:
            await interaction.response.send_message("❌ Укажи двух пользователей.", ephemeral=True)
            return
        if id1 == id2:
            await interaction.response.send_message("❌ Нельзя смешать с самим собой.", ephemeral=True)
            return

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                msgs = get_user_messages(uid)
                if len(msgs) < 50:
                    await interaction.response.send_message(f"😕 Мало данных для **{name}**: {len(msgs)} сообщ.", ephemeral=True)
                    return

        await interaction.response.defer(thinking=True)

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                train_user(uid, get_user_messages(uid), модель)

        phrase = generate_collage(id1, id2, модель)
        if not phrase:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        q = QUALITY_LEVELS[модель]
        emb = discord.Embed(description=f'🔀 *"{phrase}"*', color=discord.Color.blurple())
        emb.set_author(name=f"{name1} × {name2} (коллаж)")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · смешано 50/50")
        await interaction.followup.send(embed=emb, view=RatingView(id1, модель, phrase))

    # ── /эпоха ────────────────────────────────────────────────────────────────
    @app_commands.command(name="эпоха", description="Фраза пользователя из конкретного года или промежутка")
    @app_commands.describe(
        год="Год начала (например 2020)",
        год_до="Год конца промежутка (например 2022, необязательно)",
        пользователь="Участник", ник_или_id="Ник/ID (для ушедших)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
    ])
    async def эпоха(self, interaction: discord.Interaction,
                    год: int,
                    год_до: int | None = None,
                    пользователь: discord.Member | None = None,
                    ник_или_id: str | None = None,
                    модель: str = DEFAULT_MODEL):
        target_id, display_name = None, None
        if пользователь:
            target_id, display_name = пользователь.id, пользователь.display_name
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        available = get_available_years(target_id)
        if год not in available:
            years_str = ", ".join(str(y) for y in available) or "нет данных"
            await interaction.response.send_message(
                f"😕 Мало сообщений за **{год}** у **{display_name}**.\nДоступные года: {years_str}", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Промежуток дат
        if год_до and год_до > год:
            period_msgs = get_user_messages_between_years(target_id, год, год_до)
            period_label = f"{год}–{год_до}"
        else:
            period_msgs = get_user_messages_by_year(target_id, год)
            period_label = str(год)

        if not period_msgs:
            await interaction.followup.send(f"😕 Нет сообщений за {period_label} год(а).")
            return

        phrase = generate_epoch(target_id, год, модель)

        if not phrase:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        phrase = _strip_roles(phrase)
        q = QUALITY_LEVELS[модель]
        emb = discord.Embed(description=f'📅 *"{phrase}"*', color=discord.Color.gold())
        emb.set_author(name=f"{display_name} · {period_label} год (эпоха)")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · сообщения {period_label} года")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /тема ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="тема", description="Фраза пользователя на конкретную тему")
    @app_commands.describe(
        ключевое_слово="Слово для фильтра",
        пользователь="Участник", ник_или_id="Ник/ID (для ушедших)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
    ])
    async def тема(self, interaction: discord.Interaction,
                   ключевое_слово: str,
                   пользователь: discord.Member | None = None,
                   ник_или_id: str | None = None,
                   модель: str = DEFAULT_MODEL):
        target_id, display_name = None, None
        if пользователь:
            target_id, display_name = пользователь.id, пользователь.display_name
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        filtered = [m for m in get_user_messages(target_id) if ключевое_слово.lower() in m.lower()]
        if len(filtered) < 15:
            await interaction.followup.send(
                f"😕 Слово **«{ключевое_слово}»** встречается у **{display_name}** только {len(filtered)} раз (нужно ≥15).")
            return

        phrase = generate_topic(target_id, ключевое_слово, модель)
        if not phrase:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        phrase = _strip_roles(phrase)
        q = QUALITY_LEVELS[модель]
        emb = discord.Embed(description=f'🎯 *"{phrase}"*', color=discord.Color.green())
        emb.set_author(name=f"{display_name} · тема «{ключевое_слово}»")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · {len(filtered)} сообщений по теме")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /мем_фраза ────────────────────────────────────────────────────────────
    @app_commands.command(name="мем_фраза", description="Короткая фраза ЗАГЛАВНЫМИ для мема — выбирает самую смешную")
    @app_commands.describe(
        пользователь="Участник", ник_или_id="Ник/ID (для ушедших)",
        модель="Источник фраз",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем — абсурдный рандом",       value="мем"),
        app_commands.Choice(name="🧠 Разум — осознанная фраза",      value="разум"),
    ])
    async def мем_фраза(self, interaction: discord.Interaction,
                        пользователь: discord.Member | None = None,
                        ник_или_id: str | None = None,
                        модель: str = "мем"):
        target_id, display_name, avatar_url = None, None, None
        if пользователь:
            target_id, display_name, avatar_url = пользователь.id, пользователь.display_name, пользователь.display_avatar.url
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        msgs = get_user_messages(target_id)
        if len(msgs) < 50:
            await interaction.response.send_message(f"😕 Мало данных: {len(msgs)} сообщ.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        _LOL_RE = _re.compile(r'(хах|ахах|лол|кек|лмао|хех|ору|ахаха|lmao|lol|xd|😂|🤣|💀)', _re.I)

        def _meme_score(text: str) -> float:
            words = text.split()
            length_score = max(0.0, 1.0 - len(words) / 15)
            lol_score    = len(_LOL_RE.findall(text)) * 0.3
            caps_score   = 0.2 if any(w.isupper() and len(w) > 2 for w in words) else 0
            punct_score  = 0.1 * text.count('!')
            return length_score + lol_score + caps_score + punct_score

        candidates = []
        if not model_exists(target_id, модель):
            train_user(target_id, msgs, модель)
        mk = _load_model(target_id, модель)
        if mk:
            for _ in range(12):
                p = mk.make_short_sentence(max_chars=100, tries=30)
                if p: candidates.append(p)
            if not candidates:
                p = generate_phrase(target_id, модель)
                if p: candidates.append(p)

        if not candidates:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        phrase = _strip_roles(max(candidates, key=_meme_score))
        q = QUALITY_LEVELS.get(модель, QUALITY_LEVELS["мем"])
        emb = discord.Embed(description=f"🤣 **{phrase.upper()}**", color=discord.Color.yellow())
        if avatar_url:
            emb.set_author(name=f"Мем: {display_name}", icon_url=avatar_url)
        else:
            emb.set_author(name=f"Мем: {display_name}")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · лучшая из {len(candidates)} фраз · Оцени!")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /профиль_стиля ────────────────────────────────────────────────────────
    @app_commands.command(name="профиль_стиля", description="Паспорт стиля речи пользователя")
    @app_commands.describe(пользователь="Участник", ник_или_id="Ник/ID (для ушедших)")
    async def профиль_стиля(self, interaction: discord.Interaction,
                             пользователь: discord.Member | None = None,
                             ник_или_id: str | None = None):
        target_id, display_name, avatar_url = None, None, None
        if пользователь:
            target_id, display_name, avatar_url = пользователь.id, пользователь.display_name, пользователь.display_avatar.url
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        msgs = get_user_messages(target_id)
        if not msgs:
            await interaction.followup.send("😕 Для пользователя ещё нет сообщений.")
            return

        tokenized = [re.findall(r"[a-zа-яё0-9']+", msg.lower()) for msg in msgs]
        lengths = [len(words) for words in tokenized]
        all_words = [word for words in tokenized for word in words]
        stop_words = {"и", "в", "во", "не", "на", "я", "что", "а", "но", "это", "как", "ты", "он", "она", "мы", "вы", "они", "у", "за", "с", "со", "к", "по", "из", "для", "же", "то"}
        blocked = get_blocked_words()
        downranked = get_downranked_words()
        word_counts = Counter(word for word in all_words if len(word) > 2 and word not in stop_words)
        characteristic = [
            word for word, score in sorted(
                ((word, apply_word_filters(word, count, blocked, downranked)) for word, count in word_counts.items()),
                key=lambda item: item[1],
                reverse=True,
            )
            if score > 0
        ][:15]
        avg_len = sum(lengths) / len(lengths) if lengths else 0.0
        short_pct = 100 * sum(length <= 3 for length in lengths) / len(lengths)
        long_pct = 100 * sum(length >= 15 for length in lengths) / len(lengths)
        question_pct = 100 * sum("?" in msg for msg in msgs) / len(msgs)
        exclamation_pct = 100 * sum("!" in msg for msg in msgs) / len(msgs)

        emb = discord.Embed(title=f"🎭 Паспорт стиля: {display_name}", color=discord.Color.og_blurple())
        if avatar_url:
            emb.set_thumbnail(url=avatar_url)
        emb.add_field(name="📏 Длина сообщений",
            value=f"Среднее: **{avg_len:.1f}** слов\n"
                  f"Короткие (≤3): **{short_pct:.0f}%**\n"
                  f"Длинные (≥15): **{long_pct:.0f}%**", inline=True)
        emb.add_field(name="💬 Интонация",
            value=f"Вопросы: **{question_pct:.0f}%**\nВосклицания: **{exclamation_pct:.0f}%**", inline=True)
        emb.add_field(name="🔤 Характерные слова",
            value=" · ".join(f"`{word}`" for word in characteristic) or "—", inline=False)
        emb.add_field(name="📚 Словарный запас",
            value=f"**{len(set(all_words)):,}** уникальных слов", inline=True)

        # Сырые данные из БД (было в /стиль_статистика)
        stats = get_user_stats(target_id)
        msg_count = stats.get("count", 0)
        first_msg = (stats.get("first") or "")[:10]
        last_msg  = (stats.get("last")  or "")[:10]
        status_parts = [
            f"{QUALITY_LEVELS[quality]['emoji']} {quality.capitalize()} {'✅' if model_exists(target_id, quality) else '⬜'}"
            for quality in ("мем", "разум")
        ]

        emb.add_field(
            name="📦 База данных",
            value=f"Сообщений: **{msg_count:,}**"
                  + (f"\nПериод: {first_msg} → {last_msg}" if first_msg else "")
                  + (f"\nГотовность: {'✅ Достаточно' if msg_count >= 200 else f'⚠️ Мало ({msg_count}/200)'}"),
            inline=False
        )
        emb.add_field(name="⚙️ Markov-модели", value="  ".join(status_parts), inline=False)
        emb.set_footer(text=f"Профиль по {len(msgs):,} сообщениям · /пародия чтобы попробовать")
        await interaction.followup.send(embed=emb)

    # ── /дообучить ────────────────────────────────────────────────────────────
    @app_commands.command(name="дообучить", description="(Админ) Обучить Markov-модели")
    @app_commands.describe(пользователь="Конкретный пользователь (или все если не указан)")
    @app_commands.checks.has_permissions(administrator=True)
    async def дообучить(self, interaction: discord.Interaction,
                        пользователь: discord.Member | None = None):
        await interaction.response.defer(thinking=True)
        _prevent_sleep()  # ПК не спит пока идёт обучение
        try:
            if пользователь:
                msgs = get_user_messages(пользователь.id)
                if len(msgs) < 50:
                    await interaction.followup.send(f"❌ Мало данных: {len(msgs)} сообщ.")
                    return
                status = await interaction.followup.send(f"⏳ Обучаю **{пользователь.display_name}**...", wait=True)
                results = train_user_all_qualities(пользователь.id, msgs)
                ready = ", ".join(quality for quality, ok in results.items() if ok) or "нет"
                emb = discord.Embed(
                    title=f"✅ {пользователь.display_name} — готово",
                    description=f"Markov-модели: **{ready}**\nСообщений: **{len(msgs)}**",
                    color=discord.Color.green(),
                )
            else:
                uids = get_all_user_ids()
                status = await interaction.followup.send(f"⏳ Обучаю Markov-модели для **{len(uids)}** пользователей...", wait=True)
                results = await train_all_users_async(min_messages=50)
                emb = discord.Embed(
                    title="🧠 Дообучение завершено",
                    description=f"Markov-модели обновлены для **{len(results)}** пользователей.\nСледующее автообучение: **воскресенье 03:00 МСК**",
                    color=discord.Color.green(),
                )
            await _safe_send(interaction, status, emb, interaction.channel)
        finally:
            _allow_sleep()

    # ── /профилактика ─────────────────────────────────────────────────────────
    @app_commands.command(name="профилактика",
        description="(Админ) Полный сброс и переобучение: чекпоинты → сбор → дообучить всё")
    @app_commands.checks.has_permissions(administrator=True)
    async def профилактика(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        if not is_full_maintenance_allowed():
            emb = _server_training_guard_embed(
                "🛡️ Профилактика на VPS заблокирована",
                "Полная профилактика включает массовый сбор сообщений и переобучение всех Markov-моделей, поэтому на сервере она выключена по умолчанию.\n\n"
                "Безопасный путь:\n"
                "1. Ежедневный сбор и лёгкое автообучение оставить на VPS.\n"
                "2. Полную профилактику запускать локально на ПК.\n"
                "3. После проверки синхронизировать Markov-артефакты на VPS.",
            )
            await interaction.followup.send(embed=emb, ephemeral=True)
            return

        _prevent_sleep()

        # Шаг 1: сброс чекпоинтов
        deleted = reset_checkpoints()

        status = await interaction.followup.send(
            embed=discord.Embed(
                title="🔧 Профилактика запущена",
                description=(
                    f"**Шаг 1/3:** ✅ Сброшено {deleted} чекпоинтов\n"
                    f"**Шаг 2/3:** ⏳ Сбор сообщений со всех каналов...\n"
                    f"**Шаг 3/3:** ⬜ Обучение Markov-моделей\n\n"
                    f"*Можешь идти отдыхать — бот всё сделает сам*"
                ),
                color=discord.Color.orange()
            ),
            wait=True
        )

        # Шаг 2: сбор сообщений
        guild = interaction.guild
        channels = filter_parody_channels(
            guild,
            [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history],
        )
        total_collected = 0
        for ch in channels:
            total_collected += await collect_channel(ch, guild.id)

        try:
            await status.edit(embed=discord.Embed(
                title="🔧 Профилактика — шаг 3/3",
                description=(
                    f"**Шаг 1/3:** ✅ Сброшено {deleted} чекпоинтов\n"
                    f"**Шаг 2/3:** ✅ Собрано +{total_collected} сообщений\n"
                    f"**Шаг 3/3:** ⏳ Обучение Markov-моделей...\n"
                ),
                color=discord.Color.orange()
            ))
        except Exception:
            pass

        # Шаг 3: Markov
        mk_results = await train_all_users_async(min_messages=50)

        # Финал
        uids = get_all_user_ids()
        final_emb = discord.Embed(
            title="✅ Профилактика завершена",
            description=(
                f"**Шаг 1/3:** ✅ Сброшено **{deleted}** чекпоинтов\n"
                f"**Шаг 2/3:** ✅ Собрано **+{total_collected}** сообщений\n"
                f"**Шаг 3/3:** ✅ Markov-модели: **{len(mk_results)}** пользователей\n\n"
                f"Пользователей в базе: **{len(uids)}**\n"
                f"Следующая авто-профилактика: **воскресенье 03:00 МСК**"
            ),
            color=discord.Color.green()
        )
        _allow_sleep()  # профилактика завершена — можно спать
        await _safe_send(interaction, status, final_emb, interaction.channel)

    # ── /список_пользователей ─────────────────────────────────────────────────
    @app_commands.command(name="список_пользователей", description="Все пользователи в базе пародии")
    async def список_пользователей(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uids = get_all_user_ids()
        if not uids:
            await interaction.followup.send("😶 База пуста.", ephemeral=True)
            return

        users_data = sorted(
            [(uid, (s := get_user_stats(uid))["username"] or str(uid), s["count"]) for uid in uids],
            key=lambda x: x[2], reverse=True
        )
        PAGE = 20
        total_pages = (len(users_data) + PAGE - 1) // PAGE
        current_page = 0

        def make_embed(page: int) -> discord.Embed:
            rows = []
            for uid, username, count in users_data[page * PAGE:(page + 1) * PAGE]:
                if model_exists(uid, "разум"):             badge = "🧠"
                elif model_exists(uid, "мем"):             badge = "🎲"
                else:                                      badge = "⬜"
                on_server = "" if interaction.guild.get_member(uid) else " *(ушёл)*"
                rows.append(f"{badge} **{username}**{on_server} — {count:,} сообщ.")
            emb = discord.Embed(
                title=f"👥 Пользователи в базе ({len(users_data)} чел.)",
                description="\n".join(rows), color=discord.Color.blurple()
            )
            emb.add_field(
                name="Команды",
                value="`/пародия` · `/батл` · `/коллаж` · `/эпоха` · `/тема`\nДля ушедших: `ник_или_id:ник`",
                inline=False
            )
            emb.set_footer(text=f"Стр. {page+1}/{total_pages} | 🧠разум 🎲мем ⬜нет модели")
            return emb

        def make_view(page: int) -> discord.ui.View:
            view = discord.ui.View(timeout=120)
            prev = discord.ui.Button(label="◀ Назад",  style=discord.ButtonStyle.secondary, disabled=page == 0)
            nxt  = discord.ui.Button(label="Вперёд ▶", style=discord.ButtonStyle.secondary, disabled=page >= total_pages - 1)
            async def prev_cb(bi: discord.Interaction):
                nonlocal current_page
                current_page -= 1
                await bi.response.edit_message(embed=make_embed(current_page), view=make_view(current_page))
            async def next_cb(bi: discord.Interaction):
                nonlocal current_page
                current_page += 1
                await bi.response.edit_message(embed=make_embed(current_page), view=make_view(current_page))
            prev.callback = prev_cb
            nxt.callback  = next_cb
            view.add_item(prev)
            view.add_item(nxt)
            return view

        await interaction.followup.send(embed=make_embed(0), view=make_view(0), ephemeral=True)

    # ── /модели_статус ────────────────────────────────────────────────────────
    @app_commands.command(name="модели_статус", description="(Админ) Статус обученных моделей")
    @app_commands.checks.has_permissions(administrator=True)
    async def модели_статус(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uids = get_all_user_ids()
        if not uids:
            await interaction.followup.send("😶 База пуста.", ephemeral=True)
            return
        lines = []
        for uid in uids:
            stats = get_user_stats(uid)
            mk = "".join(QUALITY_LEVELS[q]["emoji"] if model_exists(uid, q) else "·" for q in ["мем","разум"])
            good = len(get_good_phrases(uid, "разум"))
            bad  = len(get_bad_phrases(uid, "разум"))
            rating_str = f" 👍{good}/👎{bad}" if good or bad else ""
            lines.append(f"`{mk}` **{stats['username']}** — {stats['count']:,} сообщ.{rating_str}")
        lines.sort()
        emb = discord.Embed(
            title="🎭 Статус Markov-моделей",
            description="\n".join(lines[:25]),
            color=discord.Color.blurple()
        )
        emb.set_footer(text=f"🎲мем 🧠разум | · = не обучена | Всего: {len(uids)}")
        await interaction.followup.send(embed=emb, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ParodyEngine(bot))
