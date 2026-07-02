# -*- coding: utf-8 -*-
"""
Лёгкая разговорная болтовня бота.

Что умеет:
- иногда отвечает людям не только на токсичность;
- реагирует на упоминание бота, ответ на сообщение бота, приветствия и вопросы;
- не спамит: есть кулдаун по каналу и пользователю;
- умеет редко и адресно подтроллить человека по его модели, если давно не трогал;
- настраивается через slash-команды.
"""

from __future__ import annotations

import random
import re
import sqlite3
import asyncio
import io
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from core.paths import SOCIAL_DB
from core.settings_store import (
    clear_feature_channel,
    clear_feature_channels,
    get_feature_policy,
    has_feature_setting,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except Exception:
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None

DB_PATH = SOCIAL_DB
FEATURE_SOCIAL_CHAT = "social_chat"
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
CHAOS_RE = re.compile(r"(!{2,}|[?]{2,}|чзх|wtf|пиздец|ебать|жесть|сдох|умер|легенда|разъеб)", re.I)

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

TROLL_PREFIXES = [
    "Ловлю тебя на слове:",
    "Стенограмма твоей души такая:",
    "Если убрать приличия, получается вот это:",
    "Ты это сказал, а модель услышала вот так:",
]

TROLL_FALLBACKS = [
    "Это сообщение заслуживает отдельного расследования.",
    "Я ничего не добавлю, тут человек сам себя переиграл.",
    "Сильный заход. Пахнет легендарной ошибкой.",
    "Вижу сообщение, вижу хаос, вижу характер.",
]

MEME_CAPTIONS = [
    "Когда чат снова пошёл не по плану",
    "Когда человек сказал это вслух и теперь поздно отступать",
    "Когда сервер коллективно принял очень сомнительное решение",
    "Когда идея звучит ужасно, но все уже согласились",
]

MEME_REACTIONS = [
    "Это надо было увековечить.",
    "Скриншот морали сделан.",
    "Чат снова дал материал для искусства.",
    "Исторический момент, зафиксировано.",
]

CARD_COLORS = [
    ((38, 13, 58), (121, 53, 165), (248, 216, 79)),
    ((17, 34, 64), (35, 105, 185), (255, 134, 75)),
    ((31, 21, 21), (172, 52, 75), (255, 226, 133)),
    ((18, 42, 39), (32, 122, 106), (202, 247, 213)),
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_chat_excluded_channels (
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                reason     TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )
        conn.commit()


def _get_legacy_config(guild_id: int) -> tuple[bool, int, bool, set[int]]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT enabled, chance_percent, mention_only, channel_ids FROM social_chat_config WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    if not row:
        return True, 12, False, set()
    channel_ids = set(int(x) for x in (row[3] or "").split(",") if x.strip().isdigit())
    return bool(row[0]), int(row[1]), bool(row[2]), channel_ids


def _get_config(guild_id: int) -> tuple[bool, int, bool, set[int], set[int]]:
    legacy_enabled, legacy_chance, legacy_mention_only, legacy_channel_ids = _get_legacy_config(guild_id)
    policy = get_feature_policy(guild_id, FEATURE_SOCIAL_CHAT)
    payload = policy.extra or {}
    configured = has_feature_setting(guild_id, FEATURE_SOCIAL_CHAT) or bool(
        policy.allowed_channel_ids or policy.excluded_channel_ids or payload
    )

    enabled = policy.enabled if configured else legacy_enabled
    try:
        chance_percent = int(payload.get("chance_percent", legacy_chance))
    except (TypeError, ValueError):
        chance_percent = legacy_chance
    chance_percent = max(0, min(100, chance_percent))
    mention_only = bool(payload.get("mention_only", legacy_mention_only))
    allowed_channel_ids = set(policy.allowed_channel_ids) if configured else legacy_channel_ids
    excluded_channel_ids = set(policy.excluded_channel_ids) if configured else _social_excluded_channel_ids(guild_id)
    return enabled, chance_percent, mention_only, allowed_channel_ids, excluded_channel_ids


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
    chance_percent = max(0, min(100, int(chance_percent)))
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


def _social_excluded_channel_ids(guild_id: int) -> set[int]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT channel_id FROM social_chat_excluded_channels WHERE guild_id=?",
            (guild_id,),
        ).fetchall()
    return {int(row[0]) for row in rows}


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


def _pick_meme_lines(text: str, kind: str) -> tuple[str, str]:
    topic = _extract_topic(text)
    top = random.choice(MEME_CAPTIONS)
    if topic:
        bottom = f"ТЕМА: {topic[:56].upper()}"
    elif kind == "chaos":
        bottom = "УРОВЕНЬ СПОКОЙСТВИЯ: ОТСУТСТВУЕТ"
    elif kind == "question":
        bottom = "МЫСЛЬ ЕСТЬ. ПЛАНА НЕТ."
    else:
        bottom = "ЧАТ ОПЯТЬ ВЫБРАЛ ПРИКЛЮЧЕНИЯ"
    return top, bottom


def _get_font(size: int):
    if ImageFont is None:
        return None
    candidates = (
        "arialbd.ttf",
        "arial.ttf",
        "segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int):
    font = _get_font(start_size)
    if font is None:
        return None
    size = start_size
    while size > 20:
        font = _get_font(size)
        box = draw.multiline_textbbox((0, 0), text, font=font, spacing=8, align="center")
        width = box[2] - box[0]
        if width <= max_width:
            return font
        size -= 4
    return _get_font(20)


def _draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font, fill):
    left, top, right, bottom = box
    text_box = draw.multiline_textbbox((0, 0), text, font=font, spacing=8, align="center")
    width = text_box[2] - text_box[0]
    height = text_box[3] - text_box[1]
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2
    draw.multiline_text((x, y), text, font=font, fill=fill, spacing=8, align="center")


async def _render_meme_card(message: discord.Message, kind: str) -> discord.File | None:
    if Image is None or ImageDraw is None or ImageFont is None:
        return None

    width, height = 1100, 760
    bg, accent, text_color = random.choice(CARD_COLORS)
    image = Image.new("RGB", (width, height), color=bg)
    draw = ImageDraw.Draw(image)

    for i in range(height):
        blend = i / max(height - 1, 1)
        row = (
            int(bg[0] * (1 - blend) + accent[0] * blend),
            int(bg[1] * (1 - blend) + accent[1] * blend),
            int(bg[2] * (1 - blend) + accent[2] * blend),
        )
        draw.line((0, i, width, i), fill=row)

    draw.rounded_rectangle((32, 32, width - 32, height - 32), radius=36, outline=(255, 255, 255), width=3)

    try:
        avatar_bytes = await message.author.display_avatar.replace(size=256, static_format="png").read()
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((180, 180))
        mask = Image.new("L", (180, 180), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 179, 179), fill=255)
        avatar = ImageOps.fit(avatar, (180, 180))
        image.paste(avatar, (60, 72), mask)
    except Exception:
        pass

    top_text, bottom_text = _pick_meme_lines(message.content, kind)
    quote_text = message.content.strip()
    if len(quote_text) > 170:
        quote_text = quote_text[:167] + "..."
    quote_text = f"«{quote_text}»"

    top_font = _fit_text(draw, top_text, 760, 56)
    quote_font = _fit_text(draw, quote_text, width - 140, 52)
    bottom_font = _fit_text(draw, bottom_text, width - 140, 34)
    small_font = _get_font(24)

    _draw_centered_text(draw, (280, 78, width - 60, 222), top_text, top_font, fill=(255, 255, 255))
    _draw_centered_text(draw, (70, 270, width - 70, 500), quote_text, quote_font, fill=text_color)
    _draw_centered_text(draw, (70, 540, width - 70, 630), bottom_text, bottom_font, fill=(255, 255, 255))

    footer = f"@{message.author.display_name} • ViPik meme response"
    draw.text((70, height - 92), footer, fill=(230, 230, 230), font=small_font)
    draw.text((width - 250, height - 92), datetime.now(MSK).strftime("%d.%m %H:%M"), fill=(230, 230, 230), font=small_font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=f"vipik_meme_{message.id}.png")


class SocialChat(commands.Cog):
    chat_group = app_commands.Group(name="болтовня", description="Настройки разговорчивости бота")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        self._channel_cooldowns: dict[tuple[int, int], datetime] = {}
        self._user_cooldowns: dict[tuple[int, int], datetime] = {}
        self._image_cooldowns: dict[tuple[int, int], datetime] = {}
        self._troll_cooldowns: dict[tuple[int, int], datetime] = {}

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

    async def _build_model_troll_reply(self, message: discord.Message) -> str | None:
        user_id = message.author.id
        try:
            from fun_slesh.parody_engine import generate_phrase, model_exists
            from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists

            phrase = None
            if model_exists(user_id, "разум"):
                phrase = await asyncio.to_thread(generate_phrase, user_id, "разум")
            elif model_exists(user_id, "мем"):
                phrase = await asyncio.to_thread(generate_phrase, user_id, "мем")
            elif model_exists(user_id, "автор"):
                phrase = await asyncio.to_thread(generate_author_phrase, user_id)
            elif GPT_OK and gpt_model_exists(user_id):
                phrase = await asyncio.to_thread(generate_neuro_phrase, user_id)

            if phrase:
                return f"{random.choice(TROLL_PREFIXES)} *{phrase}*"
        except Exception:
            pass
        return random.choice(TROLL_FALLBACKS)

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

    def _image_ready(self, guild_id: int, channel_id: int) -> bool:
        key = (guild_id, channel_id)
        now = datetime.now(UTC)
        last = self._image_cooldowns.get(key)
        if last and now - last < timedelta(minutes=18):
            return False
        self._image_cooldowns[key] = now
        return True

    def _troll_hours_silent(self, guild_id: int, user_id: int) -> float:
        key = (guild_id, user_id)
        now = datetime.now(UTC)
        last = self._troll_cooldowns.get(key)
        if not last:
            return 999.0
        return (now - last).total_seconds() / 3600.0

    def _mark_troll(self, guild_id: int, user_id: int):
        self._troll_cooldowns[(guild_id, user_id)] = datetime.now(UTC)

    async def _send_troll_later(self, message: discord.Message, reply: str):
        await asyncio.sleep(random.randint(5 * 60, 60 * 60))
        try:
            await message.reply(
                reply,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    async def _maybe_build_meme(self, message: discord.Message, kind: str) -> discord.File | None:
        if kind not in {"chaos", "ambient", "talk", "direct", "question"}:
            return None
        if len((message.content or "").strip()) < 10:
            return None
        if not self._image_ready(message.guild.id, message.channel.id):
            return None

        chance = {
            "chaos": 0.30,
            "direct": 0.16,
            "question": 0.10,
            "ambient": 0.12,
            "talk": 0.14,
        }.get(kind, 0.0)
        if random.random() > chance:
            return None
        return await _render_meme_card(message, kind)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return
        if message.content.startswith("/") or message.content.startswith("!"):
            return

        guild_id = message.guild.id
        enabled, chance_percent, mention_only, channel_ids, excluded_channel_ids = _get_config(guild_id)
        if not enabled:
            return
        if message.channel.id in excluded_channel_ids:
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
                hours_silent = self._troll_hours_silent(guild_id, message.author.id)
                should_troll = False
                if len(message.content.split()) >= 4:
                    if hours_silent >= 24:
                        should_troll = True

                if should_troll:
                    try:
                        troll_reply = await self._build_model_troll_reply(message)
                        self._mark_troll(guild_id, message.author.id)
                        asyncio.create_task(self._send_troll_later(message, troll_reply))
                    except Exception:
                        pass
                return

        reply = await self._build_fun_reply(message, kind)
        meme_file = await self._maybe_build_meme(message, kind)
        try:
            if meme_file is not None:
                await message.reply(
                    random.choice(MEME_REACTIONS),
                    file=meme_file,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            await message.reply(reply, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    @chat_group.command(name="статус", description="Показать текущие настройки болтовни")
    async def статус(self, interaction: discord.Interaction):
        enabled, chance_percent, mention_only, channel_ids, excluded_channel_ids = _get_config(interaction.guild.id)
        channels = ", ".join(f"<#{cid}>" for cid in sorted(channel_ids)) if channel_ids else "все каналы"
        excluded = ", ".join(f"<#{cid}>" for cid in sorted(excluded_channel_ids)) if excluded_channel_ids else "нет"
        embed = discord.Embed(title="💬 Болтовня бота", color=discord.Color.blurple())
        embed.add_field(name="Включено", value="да" if enabled else "нет", inline=True)
        embed.add_field(name="Шанс автоответа", value=f"{chance_percent}%", inline=True)
        embed.add_field(name="Только при обращении", value="да" if mention_only else "нет", inline=True)
        embed.add_field(name="Каналы", value=channels, inline=False)
        embed.add_field(name="Исключения", value=excluded, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @chat_group.command(name="вкл", description="(Админ) Включить или выключить разговорчивость")
    @app_commands.checks.has_permissions(administrator=True)
    async def вкл(self, interaction: discord.Interaction, включить: bool):
        _set_enabled(interaction.guild.id, включить)
        set_feature_enabled(interaction.guild.id, FEATURE_SOCIAL_CHAT, включить)
        await interaction.response.send_message(
            f"{'✅' if включить else '⛔'} Болтовня бота {'включена' if включить else 'выключена'}.",
            ephemeral=True,
        )

    @chat_group.command(name="шанс", description="(Админ) Шанс случайного ответа вне прямого обращения")
    @app_commands.checks.has_permissions(administrator=True)
    async def шанс(self, interaction: discord.Interaction, процент: app_commands.Range[int, 0, 100]):
        _set_chance(interaction.guild.id, int(процент))
        set_feature_payload(interaction.guild.id, FEATURE_SOCIAL_CHAT, {"chance_percent": int(процент)})
        await interaction.response.send_message(
            f"✅ Новый шанс случайного ответа: **{процент}%**.",
            ephemeral=True,
        )

    @chat_group.command(name="режим", description="(Админ) Только по обращению к боту или и обычная болтовня тоже")
    @app_commands.checks.has_permissions(administrator=True)
    async def режим(self, interaction: discord.Interaction, только_по_обращению: bool):
        _set_mention_only(interaction.guild.id, только_по_обращению)
        set_feature_payload(interaction.guild.id, FEATURE_SOCIAL_CHAT, {"mention_only": bool(только_по_обращению)})
        await interaction.response.send_message(
            "✅ Режим обновлён: "
            + ("бот отвечает только при обращении к нему." if только_по_обращению else "бот может иногда влезать и сам."),
            ephemeral=True,
        )

    @chat_group.command(name="канал", description="(Админ) Разрешить или запретить болтовню в канале")
    @app_commands.checks.has_permissions(administrator=True)
    async def канал(self, interaction: discord.Interaction, канал: discord.TextChannel, включить: bool):
        _, _, _, current, _ = _get_config(interaction.guild.id)
        if включить:
            current.add(канал.id)
            set_feature_channel(interaction.guild.id, FEATURE_SOCIAL_CHAT, канал.id, "allow", "Discord command")
        else:
            current.discard(канал.id)
            clear_feature_channel(interaction.guild.id, FEATURE_SOCIAL_CHAT, канал.id, "allow")
            if not current:
                clear_feature_channels(interaction.guild.id, FEATURE_SOCIAL_CHAT, "allow")
        _set_channels(interaction.guild.id, current)
        if current:
            mentions = ", ".join(f"<#{cid}>" for cid in sorted(current))
            text = f"✅ Болтовня разрешена только в: {mentions}"
        else:
            text = "✅ Ограничение по каналам снято. Болтовня может работать во всех каналах."
        await interaction.response.send_message(text, ephemeral=True)


    @chat_group.command(name="исключить", description="(Админ) Исключить канал из болтовни бота")
    @app_commands.checks.has_permissions(administrator=True)
    async def chat_exclude_channel(
        self,
        interaction: discord.Interaction,
        канал: discord.TextChannel,
        причина: str = "",
    ):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO social_chat_excluded_channels(guild_id, channel_id, reason)
                VALUES(?, ?, ?)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET reason=excluded.reason
                """,
                (interaction.guild.id, канал.id, причина[:200]),
            )
        set_feature_channel(interaction.guild.id, FEATURE_SOCIAL_CHAT, канал.id, "exclude", причина[:200])
        await interaction.response.send_message(
            f"✅ {канал.mention} исключён из болтовни бота.", ephemeral=True
        )

    @chat_group.command(name="вернуть", description="(Админ) Вернуть канал в болтовню бота")
    @app_commands.checks.has_permissions(administrator=True)
    async def chat_include_channel(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                "DELETE FROM social_chat_excluded_channels WHERE guild_id=? AND channel_id=?",
                (interaction.guild.id, канал.id),
            )
        removed = cur.rowcount + clear_feature_channel(interaction.guild.id, FEATURE_SOCIAL_CHAT, канал.id, "exclude")
        text = f"✅ {канал.mention} снова доступен для болтовни." if removed else "ℹ️ Этого канала не было в исключениях."
        await interaction.response.send_message(text, ephemeral=True)

    @chat_group.command(name="исключения", description="(Админ) Показать каналы, где болтовня отключена")
    @app_commands.checks.has_permissions(administrator=True)
    async def chat_excluded_channels(self, interaction: discord.Interaction):
        _, _, _, _, ids = _get_config(interaction.guild.id)
        text = ", ".join(f"<#{cid}>" for cid in sorted(ids)) if ids else "Исключений нет."
        await interaction.response.send_message(f"Каналы без болтовни: {text}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SocialChat(bot))
