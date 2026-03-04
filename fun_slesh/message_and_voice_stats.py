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

import os
import re
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone, date
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

# === Путь к общей БД проекта ===
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
UTC = timezone.utc

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

# -------------------------
# DB helpers / schema
# -------------------------
SCHEMA_SQL = [
    # суточные агрегаты по текстовым сообщениям
    """
    CREATE TABLE IF NOT EXISTS msg_stats_daily (
        user_id     INTEGER NOT NULL,
        guild_id    INTEGER NOT NULL,
        channel_id  INTEGER NOT NULL,
        date        TEXT    NOT NULL,  -- YYYY-MM-DD (UTC)
        messages    INTEGER NOT NULL DEFAULT 0,
        words       INTEGER NOT NULL DEFAULT 0,
        emojis      INTEGER NOT NULL DEFAULT 0,
        chars       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, guild_id, channel_id, date)
    )
    """,
    # чекпоинт по каждому текстовому каналу
    """
    CREATE TABLE IF NOT EXISTS msg_index_checkpoints (
        channel_id       INTEGER PRIMARY KEY,
        last_message_id  INTEGER
    )
    """,
    # сессии голосовых
    """
    CREATE TABLE IF NOT EXISTS voice_sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        guild_id   INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        started_at TEXT    NOT NULL,  -- ISO UTC
        ended_at   TEXT,              -- ISO UTC
        seconds    INTEGER NOT NULL DEFAULT 0
    )
    """,
    # суточные агрегаты по голосовым
    """
    CREATE TABLE IF NOT EXISTS voice_totals_daily (
        user_id  INTEGER NOT NULL,
        guild_id INTEGER NOT NULL,
        date     TEXT    NOT NULL,  -- YYYY-MM-DD (UTC)
        seconds  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, guild_id, date)
    )
    """,
]


def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        for sql in SCHEMA_SQL:
            cur.execute(sql)
        conn.commit()

# -------------------------
# Text utils
# -------------------------
_def_splitter = re.compile(r"\s+", re.UNICODE)
_emoji_unic = re.compile(
    r"[\U0001F300-\U0001F5FF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|"
    r"[\U0001F700-\U0001F77F]|[\U0001F780-\U0001F7FF]|[\U0001F800-\U0001F8FF]|"
    r"[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|"
    r"[\u2600-\u26FF]|[\u2700-\u27BF]",
    re.UNICODE,
)
_custom_emoji = re.compile(r"<a?:[A-Za-z0-9_~]+:[0-9]+>")


def _count_words(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    return len([t for t in _def_splitter.split(text) if t])


def _count_emojis(text: str) -> int:
    if not text:
        return 0
    return len(_emoji_unic.findall(text)) + len(_custom_emoji.findall(text))

# -------------------------
# Core aggregations
# -------------------------

def _utc_date_from_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).date().isoformat()


def _accumulate_msg_row(cur: sqlite3.Cursor, *, user_id: int, guild_id: int, channel_id: int,
                        d_iso: str, messages: int, words: int, emojis: int, chars: int) -> None:
    cur.execute(
        """
        INSERT INTO msg_stats_daily(user_id,guild_id,channel_id,date,messages,words,emojis,chars)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,guild_id,channel_id,date) DO UPDATE SET
            messages = messages + excluded.messages,
            words    = words    + excluded.words,
            emojis   = emojis   + excluded.emojis,
            chars    = chars    + excluded.chars
        """,
        (user_id, guild_id, channel_id, d_iso, messages, words, emojis, chars)
    )


def _accumulate_voice_row(cur: sqlite3.Cursor, *, user_id: int, guild_id: int, seconds: int, dt: Optional[date] = None) -> None:
    d_iso = (dt or datetime.now(UTC).date()).isoformat()
    cur.execute(
        """
        INSERT INTO voice_totals_daily(user_id,guild_id,date,seconds)
        VALUES (?,?,?,?)
        ON CONFLICT(user_id,guild_id,date) DO UPDATE SET
            seconds = seconds + excluded.seconds
        """,
        (user_id, guild_id, d_iso, int(seconds))
    )

# -------------------------
# Cog
# -------------------------
class MessageAndVoiceStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_db()
        self._voice_sessions: dict[tuple[int, int], tuple[int, datetime]] = {}

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
        limit_days = int(макс_дней)
        after_dt = None
        if limit_days > 0:
            after_dt = datetime.now(UTC) - timedelta(days=limit_days)

        BATCH = 1000
        processed = 0
        new_rows = 0
        last_processed_id: Optional[int] = None

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # загрузим чекпоинт (если есть)
            cur.execute("SELECT last_message_id FROM msg_index_checkpoints WHERE channel_id = ?", (channel.id,))
            row = cur.fetchone()
            checkpoint_last_id = int(row[0]) if row and row[0] else None

        # идём по истории
        async for message in channel.history(limit=None, oldest_first=True, after=after_dt):
            # скипаем уже проиндексированное
            if checkpoint_last_id is not None and message.id <= checkpoint_last_id:
                continue
            if message.author.bot:
                continue

            content = message.content or ""
            words = _count_words(content)
            emojis = _count_emojis(content)
            chars = len(content)
            d_iso = _utc_date_from_ts(message.created_at)

            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                _accumulate_msg_row(cur,
                                    user_id=message.author.id,
                                    guild_id=guild_id,
                                    channel_id=channel.id,
                                    d_iso=d_iso,
                                    messages=1, words=words, emojis=emojis, chars=chars)
                new_rows += 1
                processed += 1
                last_processed_id = message.id
                if processed % BATCH == 0:
                    cur.execute("REPLACE INTO msg_index_checkpoints(channel_id,last_message_id) VALUES (?,?)",
                                (channel.id, last_processed_id))
                conn.commit()

            if processed % BATCH == 0:
                await asyncio.sleep(1.0)

        # финальный чекпоинт (если что-то обработали)
        if last_processed_id is not None:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("REPLACE INTO msg_index_checkpoints(channel_id,last_message_id) VALUES (?,?)",
                            (channel.id, last_processed_id))
                conn.commit()

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
        since = (datetime.now(UTC).date() - timedelta(days=int(дней) - 1)).isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            if канал is None:
                cur.execute(
                    """
                    SELECT user_id, SUM(messages) AS total
                    FROM msg_stats_daily
                    WHERE guild_id = ? AND date >= ?
                    GROUP BY user_id
                    ORDER BY total DESC
                    LIMIT 15
                    """,
                    (guild_id, since)
                )
            else:
                cur.execute(
                    """
                    SELECT user_id, SUM(messages) AS total
                    FROM msg_stats_daily
                    WHERE guild_id = ? AND channel_id = ? AND date >= ?
                    GROUP BY user_id
                    ORDER BY total DESC
                    LIMIT 15
                    """,
                    (guild_id, канал.id, since)
                )
            rows = cur.fetchall()

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
        since = (datetime.now(UTC).date() - timedelta(days=int(дней) - 1)).isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            if канал is None:
                cur.execute(
                    "SELECT user_id, SUM(words) AS total FROM msg_stats_daily WHERE guild_id=? AND date>=? GROUP BY user_id ORDER BY total DESC LIMIT 15",
                    (guild_id, since))
            else:
                cur.execute(
                    "SELECT user_id, SUM(words) AS total FROM msg_stats_daily WHERE guild_id=? AND channel_id=? AND date>=? GROUP BY user_id ORDER BY total DESC LIMIT 15",
                    (guild_id, канал.id, since))
            rows = cur.fetchall()

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
        since = (datetime.now(UTC).date() - timedelta(days=int(дней) - 1)).isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            if канал is None:
                cur.execute(
                    "SELECT user_id, SUM(emojis) AS total FROM msg_stats_daily WHERE guild_id=? AND date>=? GROUP BY user_id ORDER BY total DESC LIMIT 15",
                    (guild_id, since))
            else:
                cur.execute(
                    "SELECT user_id, SUM(emojis) AS total FROM msg_stats_daily WHERE guild_id=? AND channel_id=? AND date>=? GROUP BY user_id ORDER BY total DESC LIMIT 15",
                    (guild_id, канал.id, since))
            rows = cur.fetchall()

        if not rows:
            await _safe_reply(interaction, content="📭 Нет данных за период.")
            return
        lines = [f"**{i}.** <@{uid}> — {total} эмодзи" for i, (uid, total) in enumerate(rows, start=1)]
        await _safe_reply(
            interaction,
            embed=discord.Embed(title=f"😎 Топ эмодзи за {дней} дн.", description="\n".join(lines), color=discord.Color.orange()),
        )

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
                seconds = int((now - started_at).total_seconds())
                if seconds > 0:
                    with sqlite3.connect(DB_PATH) as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO voice_sessions(user_id,guild_id,channel_id,started_at,ended_at,seconds) VALUES (?,?,?,?,?,?)",
                            (member.id, guild_id, ch_id, started_at.isoformat(), now.isoformat(), seconds)
                        )
                        _accumulate_voice_row(cur, user_id=member.id, guild_id=guild_id, seconds=seconds)
                        conn.commit()
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
                seconds = int((now - started_at).total_seconds())
                if seconds > 0:
                    with sqlite3.connect(DB_PATH) as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO voice_sessions(user_id,guild_id,channel_id,started_at,ended_at,seconds) VALUES (?,?,?,?,?,?)",
                            (member.id, guild_id, ch_id, started_at.isoformat(), now.isoformat(), seconds)
                        )
                        _accumulate_voice_row(cur, user_id=member.id, guild_id=guild_id, seconds=seconds)
                        conn.commit()
            self._voice_sessions[key] = (after.channel.id, now)

    @app_commands.command(name="voice_топ", description="Топ по времени в голосе за N дней")
    async def voice_top(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 3650] = 7):
        await _safe_defer(interaction)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(UTC).date() - timedelta(days=int(дней) - 1)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, SUM(seconds) AS s FROM voice_totals_daily WHERE guild_id=? AND date>=? GROUP BY user_id ORDER BY s DESC LIMIT 15",
                (guild_id, since))
            rows = cur.fetchall()
        if not rows:
            await _safe_reply(interaction, content="📭 Пока нет данных. Трекинг начнётся после загрузки этого cog.")
            return
        def fmt(sec: int) -> str:
            h = sec // 3600; m = (sec % 3600) // 60
            return f"{h}ч {m}м"
        lines = [f"**{i}.** <@{uid}> — {fmt(int(s))}" for i, (uid, s) in enumerate(rows, start=1)]
        await _safe_reply(interaction, embed=discord.Embed(title=f"🎙️ Топ войса за {дней} дн.", description="\n".join(lines), color=discord.Color.gold()))

    @app_commands.command(name="voice_я", description="Моя статистика по войсу за N дней")
    async def voice_me(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 3650] = 30):
        await _safe_defer(interaction, ephemeral=True)
        guild_id = interaction.guild_id
        assert guild_id is not None
        since = (datetime.now(UTC).date() - timedelta(days=int(дней) - 1)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT SUM(seconds) FROM voice_totals_daily WHERE guild_id=? AND user_id=? AND date>=?",
                (guild_id, interaction.user.id, since))
            row = cur.fetchone()
        total = int(row[0] or 0)
        h = total // 3600; m = (total % 3600) // 60
        await _safe_reply(interaction, content=f"За {дней} дн. ты провёл в войсе **{h}ч {m}м**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageAndVoiceStats(bot))
