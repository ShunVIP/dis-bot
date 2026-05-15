# -*- coding: utf-8 -*-
"""
Лёгкая разговорная болтовня бота.

Что умеет:
- иногда отвечает людям не только на токсичность;
- реагирует на упоминание бота, ответ на сообщение бота, приветствия и вопросы;
- не спамит: есть кулдаун по каналу и пользователю;
- настраивается через slash-команды.
"""

from __future__ import annotations

import os
import random
import re
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")

BOT_ALIASES = (
    "vipik",
    "vi pik",
    "випик",
    "випик бот",
    "випикбот",
)

GREETING_RE = re.compile(r"\b(привет|хай|хелло|здорово|салют|ку|доброе утро|добрый вечер)\b", re.I)
THANKS_RE = re.compile(r"\b(спасибо|спс|благодарю|пасиб|сенкс)\b", re.I)
HOW_ARE_YOU_RE = re.compile(r"\b(как дела|как жизнь|ч[её] как|как сам|как ты)\b", re.I)
QUESTION_RE = re.compile(r"\?$")
TALK_RE = re.compile(r"\b(поговори|скажи что|че скажешь|что думаешь|есть мнение|расскажи)\b", re.I)
LAUGH_RE = re.compile(r"(ахах|хаха|ор[уюа]|ору|лол|kekw|azaza|азааза|угар|ржу)", re.I)
CHAOS_RE = re.compile(r"(!{2,}|\\?{2,}|чзх|wtf|пиздец|ебать|жесть|сдох|умер|легенда|разъеб)", re.I)

GREETINGS = [
    "Привет. Я тут, слежу за порядком и иногда влезаю в разговоры.",
    "Йо. На месте.",
    "Привет-привет. Что обсуждаете?",
    "Я на связи. Кого сегодня спасать от скуки?",
]

THANKS = [
    "Пожалуйста.",
    "Всегда пожалуйста.",
    "Обращайся.",
    "Нормально, для этого и стою тут.",
]

HOW_ARE_YOU = [
    "Живой. Логи читаются, сервис дышит, значит всё неплохо.",
    "Нормально. Пока никто не уронил VPS, жизнь хороша.",
    "Пойдёт. Если на сервере тихо, я вообще счастлив.",
    "Бодро. Особенно когда меня не заставляют чинить прод ночью.",
]

SHORT_QUESTIONS = [
    "Я бы начал с простого варианта и уже потом усложнял.",
    "Если коротко: зависит от контекста, но идея звучит рабочей.",
    "Сомнительно без деталей, но можно раскрутить.",
    "Я бы проверил это на маленьком примере, а потом уже тащил дальше.",
]

SMALL_TALK = [
    "У вас тут снова движ.",
    "Я делаю вид, что молчу, но вообще всё вижу.",
    "Иногда этот чат звучит как тест на выживание.",
    "Продолжаем разговор, я записываю лучшие моменты.",
]

ROFL_FALLBACKS = [
    "Сильное сообщение. Я бы сохранил это как улику.",
    "Чат снова выбрал путь хаоса, я уважаю.",
    "Это звучит как начало очень плохой, но великой идеи.",
    "Я не осуждаю. Я просто записываю это в золотой фонд.",
    "Сюда бы драматичную музыку и можно не продолжать.",
]

PARODY_PREFIXES = [
    "Перевожу с вашего языка:",
    "Если уж совсем по-честному, это звучит так:",
    "В версии без фильтров это выглядело бы так:",
    "Беру микрофон и читаю это как надо:",
]


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_chat_config (
                guild_id         INTEGER PRIMARY KEY,
                enabled          INTEGER NOT NULL DEFAULT 1,
                chance_percent   INTEGER NOT NULL DEFAULT 12,
                mention_only     INTEGER NOT NULL DEFAULT 0,
                channel_ids      TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()


def _get_config(guild_id: int) -> tuple[bool, int, bool, set[int]]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT enabled, chance_percent, mention_only, channel_ids FROM social_chat_config WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    if not row:
        return True, 12, False, set()
    channel_ids = set(int(x) for x in (row[3] or "").split(",") if x.strip().isdigit())
    return bool(row[0]), int(row[1]), bool(row[2]), channel_ids


def _set_enabled(guild_id: int, enabled: bool):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO social_chat_config(guild_id, enabled)
            VALUES(?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled
            """,
            (guild_id, int(enabled)),
        )
        conn.commit()


def _set_chance(guild_id: int, chance_percent: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO social_chat_config(guild_id, chance_percent)
            VALUES(?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET chance_percent=excluded.chance_percent
            """,
            (guild_id, int(chance_percent)),
        )
        conn.commit()


def _set_mention_only(guild_id: int, mention_only: bool):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO social_chat_config(guild_id, mention_only)
            VALUES(?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET mention_only=excluded.mention_only
            """,
            (guild_id, int(mention_only)),
        )
        conn.commit()


def _set_channels(guild_id: int, channel_ids: set[int]):
    raw = ",".join(str(x) for x in sorted(channel_ids))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO social_chat_config(guild_id, channel_ids)
            VALUES(?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET channel_ids=excluded.channel_ids
            """,
            (guild_id, raw),
        )
        conn.commit()


def _normalize_text(text: str) -> str:
    cleaned = re.sub(r"<@!?\d+>", " ", text.lower())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _contains_bot_alias(text: str) -> bool:
    return any(alias in text for alias in BOT_ALIASES)


def _extract_topic(text: str) -> str:
    cleaned = _normalize_text(text)
    for alias in BOT_ALIASES:
        cleaned = cleaned.replace(alias, " ")
    cleaned = re.sub(r"[^\w\sа-яё-]", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = [w for w in cleaned.split() if len(w) > 2][:6]
    return " ".join(words)


def _classify_message(message: discord.Message, bot_user: discord.ClientUser | discord.Member | None) -> str | None:
    text = _normalize_text(message.content or "")
    if not text:
        return None

    direct_mention = bool(bot_user and bot_user in message.mentions)
    reply_to_bot = bool(
        message.reference
        and message.reference.resolved
        and isinstance(message.reference.resolved, discord.Message)
        and bot_user
        and message.reference.resolved.author.id == bot_user.id
    )

    if direct_mention or reply_to_bot:
        if THANKS_RE.search(text):
            return "thanks"
        if HOW_ARE_YOU_RE.search(text):
            return "how_are_you"
        if GREETING_RE.search(text):
            return "greeting"
        return "direct"

    if _contains_bot_alias(text):
        if THANKS_RE.search(text):
            return "thanks"
        if HOW_ARE_YOU_RE.search(text):
            return "how_are_you"
        if GREETING_RE.search(text):
            return "greeting"
        return "direct"

    if HOW_ARE_YOU_RE.search(text):
        return "how_are_you"
    if GREETING_RE.search(text):
        return "greeting"
    if LAUGH_RE.search(text) or CHAOS_RE.search(text):
        return "chaos"
    if TALK_RE.search(text):
        return "talk"
    if QUESTION_RE.search(text):
        return "question"
    if len(text.split()) >= 5:
        return "ambient"
    return None


def _build_reply(kind: str, text: str) -> str:
    topic = _extract_topic(text)
    if kind == "greeting":
        return random.choice(GREETINGS)
    if kind == "thanks":
        return random.choice(THANKS)
    if kind == "how_are_you":
        return random.choice(HOW_ARE_YOU)
    if kind == "talk":
        if topic:
            return f"Если про **{topic}**, то я бы послушал, к чему вы ведёте."
        return random.choice(SMALL_TALK)
    if kind == "chaos":
        return random.choice(ROFL_FALLBACKS)
    if kind in {"question", "direct"}:
        base = random.choice(SHORT_QUESTIONS)
        if topic:
            return f"{base} Если речь про **{topic}**, можешь докинуть деталей."
        return base
    return random.choice(SMALL_TALK)


class SocialChat(commands.Cog):
    chat_group = app_commands.Group(name="болтовня", description="Настройки разговорчивости бота")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        self._channel_cooldowns: dict[tuple[int, int], datetime] = {}
        self._user_cooldowns: dict[tuple[int, int], datetime] = {}

    async def _build_fun_reply(self, message: discord.Message, kind: str) -> str:
        user_id = message.author.id

        try:
            from fun_slesh.parody_engine import generate_phrase, model_exists
            from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists

            # Для неожиданных рофлов сначала пробуем самые смешные локальные модели.
            if kind in {"chaos", "ambient", "talk", "question", "direct"}:
                if model_exists(user_id, "мем"):
                    phrase = await asyncio.to_thread(generate_phrase, user_id, "мем")
                    if phrase:
                        return f"{random.choice(PARODY_PREFIXES)} *{phrase}*"

                if model_exists(user_id, "разум") and random.random() < 0.7:
                    phrase = await asyncio.to_thread(generate_phrase, user_id, "разум")
                    if phrase:
                        return f"{random.choice(PARODY_PREFIXES)} *{phrase}*"

                if model_exists(user_id, "автор") and random.random() < 0.45:
                    phrase = await asyncio.to_thread(generate_author_phrase, user_id)
                    if phrase:
                        return f"{random.choice(PARODY_PREFIXES)} *{phrase}*"

                if GPT_OK and gpt_model_exists(user_id) and random.random() < 0.2:
                    phrase = await asyncio.to_thread(generate_neuro_phrase, user_id)
                    if phrase:
                        return f"{random.choice(PARODY_PREFIXES)} *{phrase}*"
        except Exception:
            pass

        return _build_reply(kind, message.content)

    def _channel_ready(self, guild_id: int, channel_id: int) -> bool:
        key = (guild_id, channel_id)
        now = datetime.now(UTC)
        last = self._channel_cooldowns.get(key)
        if last and now - last < timedelta(minutes=8):
            return False
        self._channel_cooldowns[key] = now
        return True

    def _user_ready(self, guild_id: int, user_id: int) -> bool:
        key = (guild_id, user_id)
        now = datetime.now(UTC)
        last = self._user_cooldowns.get(key)
        if last and now - last < timedelta(minutes=4):
            return False
        self._user_cooldowns[key] = now
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return
        if message.content.startswith("/") or message.content.startswith("!"):
            return

        guild_id = message.guild.id
        enabled, chance_percent, mention_only, channel_ids = _get_config(guild_id)
        if not enabled:
            return
        if channel_ids and message.channel.id not in channel_ids:
            return

        kind = _classify_message(message, self.bot.user)
        if not kind:
            return

        direct_kind = kind in {"direct", "greeting", "thanks", "how_are_you"}
        if mention_only and not direct_kind:
            return

        if not self._channel_ready(guild_id, message.channel.id):
            return
        if not self._user_ready(guild_id, message.author.id):
            return

        if not direct_kind:
            roll = random.randint(1, 100)
            if roll > chance_percent:
                return

        reply = await self._build_fun_reply(message, kind)
        try:
            await message.reply(reply, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    @chat_group.command(name="статус", description="Показать текущие настройки болтовни")
    async def статус(self, interaction: discord.Interaction):
        enabled, chance_percent, mention_only, channel_ids = _get_config(interaction.guild.id)
        channels = ", ".join(f"<#{cid}>" for cid in sorted(channel_ids)) if channel_ids else "все каналы"
        embed = discord.Embed(title="💬 Болтовня бота", color=discord.Color.blurple())
        embed.add_field(name="Включено", value="да" if enabled else "нет", inline=True)
        embed.add_field(name="Шанс автоответа", value=f"{chance_percent}%", inline=True)
        embed.add_field(name="Только при обращении", value="да" if mention_only else "нет", inline=True)
        embed.add_field(name="Каналы", value=channels, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @chat_group.command(name="вкл", description="(Админ) Включить или выключить разговорчивость")
    @app_commands.checks.has_permissions(administrator=True)
    async def вкл(self, interaction: discord.Interaction, включить: bool):
        _set_enabled(interaction.guild.id, включить)
        await interaction.response.send_message(
            f"{'✅' if включить else '⛔'} Болтовня бота {'включена' if включить else 'выключена'}.",
            ephemeral=True,
        )

    @chat_group.command(name="шанс", description="(Админ) Шанс случайного ответа вне прямого обращения")
    @app_commands.checks.has_permissions(administrator=True)
    async def шанс(self, interaction: discord.Interaction, процент: app_commands.Range[int, 0, 100]):
        _set_chance(interaction.guild.id, int(процент))
        await interaction.response.send_message(
            f"✅ Новый шанс случайного ответа: **{процент}%**.",
            ephemeral=True,
        )

    @chat_group.command(name="режим", description="(Админ) Только по обращению к боту или и обычная болтовня тоже")
    @app_commands.checks.has_permissions(administrator=True)
    async def режим(self, interaction: discord.Interaction, только_по_обращению: bool):
        _set_mention_only(interaction.guild.id, только_по_обращению)
        await interaction.response.send_message(
            "✅ Режим обновлён: "
            + ("бот отвечает только при обращении к нему." if только_по_обращению else "бот может иногда влезать и сам."),
            ephemeral=True,
        )

    @chat_group.command(name="канал", description="(Админ) Разрешить или запретить болтовню в канале")
    @app_commands.checks.has_permissions(administrator=True)
    async def канал(self, interaction: discord.Interaction, канал: discord.TextChannel, включить: bool):
        _, _, _, current = _get_config(interaction.guild.id)
        if включить:
            current.add(канал.id)
        else:
            current.discard(канал.id)
        _set_channels(interaction.guild.id, current)
        if current:
            mentions = ", ".join(f"<#{cid}>" for cid in sorted(current))
            text = f"✅ Болтовня разрешена только в: {mentions}"
        else:
            text = "✅ Ограничение по каналам снято. Болтовня может работать во всех каналах."
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SocialChat(bot))
