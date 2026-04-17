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
from core.economy import add_coins, get_balance

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
    # Настройки пассивных наград за активность
    """
    CREATE TABLE IF NOT EXISTS activity_rewards_config (
        guild_id          INTEGER PRIMARY KEY,
        msg_enabled       INTEGER NOT NULL DEFAULT 0,
        msg_per_n         INTEGER NOT NULL DEFAULT 10,
        msg_coins         INTEGER NOT NULL DEFAULT 2,
        msg_rep_per_n     INTEGER NOT NULL DEFAULT 50,
        msg_rep           INTEGER NOT NULL DEFAULT 1,
        voice_enabled     INTEGER NOT NULL DEFAULT 0,
        voice_per_min     INTEGER NOT NULL DEFAULT 5,
        voice_coins       INTEGER NOT NULL DEFAULT 1
    )
    """,
    # Счётчик сообщений для пассивных наград (сбрасывается при начислении)
    """
    CREATE TABLE IF NOT EXISTS activity_msg_counter (
        user_id   INTEGER NOT NULL,
        guild_id  INTEGER NOT NULL,
        count     INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, guild_id)
    )
    """,
    # Накопленные минуты голоса для наград
    """
    CREATE TABLE IF NOT EXISTS activity_voice_counter (
        user_id   INTEGER NOT NULL,
        guild_id  INTEGER NOT NULL,
        minutes   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, guild_id)
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

    # ========= ТЕКСТ: пассивные награды =========
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        guild_id = message.guild.id
        user_id  = message.author.id

        with sqlite3.connect(DB_PATH) as conn:
            cfg = conn.execute(
                "SELECT msg_enabled, msg_per_n, msg_coins, msg_rep_per_n, msg_rep"
                " FROM activity_rewards_config WHERE guild_id=?", (guild_id,)
            ).fetchone()
            if not cfg or not cfg[0]:
                return
            _, per_n, coins, rep_per_n, rep = cfg

            # Обновляем счётчик
            conn.execute(
                "INSERT INTO activity_msg_counter(user_id, guild_id, count) VALUES(?,?,1)"
                " ON CONFLICT(user_id, guild_id) DO UPDATE SET count = count + 1",
                (user_id, guild_id)
            )
            row = conn.execute(
                "SELECT count FROM activity_msg_counter WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            ).fetchone()
            count = row[0] if row else 0

            # Монеты за каждые per_n сообщений
            if count % per_n == 0:
                add_coins(user_id, coins, "msg_activity",
                          {"guild": guild_id, "count": count})

            # Репутация за каждые rep_per_n сообщений
            if rep_per_n > 0 and count % rep_per_n == 0:
                today = datetime.now(UTC).date().isoformat()
                conn.execute(
                    "INSERT INTO reputation(user_id, given_by, delta, date) VALUES(?,?,?,?)",
                    (user_id, 0, rep, today)
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
                    # Пассивные награды за голос
                    await self._award_voice(member.id, guild_id, seconds)
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


    async def _award_voice(self, user_id: int, guild_id: int, seconds: int):
        """Начисляет монеты за голос."""
        with sqlite3.connect(DB_PATH) as conn:
            cfg = conn.execute(
                "SELECT voice_enabled, voice_per_min, voice_coins"
                " FROM activity_rewards_config WHERE guild_id=?", (guild_id,)
            ).fetchone()
            if not cfg or not cfg[0]:
                return
            _, per_min, coins = cfg

            minutes = seconds // 60
            if minutes < 1:
                return

            conn.execute(
                "INSERT INTO activity_voice_counter(user_id, guild_id, minutes) VALUES(?,?,?)"
                " ON CONFLICT(user_id, guild_id) DO UPDATE SET minutes = minutes + ?",
                (user_id, guild_id, minutes, minutes)
            )
            row = conn.execute(
                "SELECT minutes FROM activity_voice_counter WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            ).fetchone()
            total_min = row[0] if row else 0

            # Начисляем за каждые per_min минут
            earned = (total_min // per_min) * coins
            already = ((total_min - minutes) // per_min) * coins
            delta = earned - already
            if delta > 0:
                add_coins(user_id, delta, "voice_activity",
                          {"guild": guild_id, "minutes": total_min})

    # ── /награды_настроить ────────────────────────────────────────────────────
    @app_commands.command(name="награды_настроить",
                          description="(Админ) Настроить пассивные награды за активность")
    @app_commands.describe(
        монеты_за_сообщения="Включить монеты за сообщения",
        монет_за_n_сообщений="Сколько монет за каждые N сообщений",
        каждые_n_сообщений="Каждые сколько сообщений давать монеты",
        репа_за_сообщения="Давать +1 репутации автоматически",
        репа_каждые_n="Репа каждые N сообщений (0 = выключено)",
        монеты_за_голос="Включить монеты за голос",
        монет_за_минут="Монет за каждые N минут в голосе",
        голос_каждые_мин="Каждые сколько минут давать монеты",
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
        with sqlite3.connect(DB_PATH) as conn:
            # Гарантируем строку
            conn.execute(
                "INSERT OR IGNORE INTO activity_rewards_config(guild_id) VALUES(?)",
                (guild_id,)
            )
            updates = []
            vals    = []
            mapping = {
                "msg_enabled":   монеты_за_сообщения,
                "msg_coins":     монет_за_n_сообщений,
                "msg_per_n":     каждые_n_сообщений,
                "msg_rep":       репа_за_сообщения,
                "msg_rep_per_n": репа_каждые_n,
                "voice_enabled": монеты_за_голос,
                "voice_coins":   монет_за_минут,
                "voice_per_min": голос_каждые_мин,
            }
            for col, val in mapping.items():
                if val is not None:
                    updates.append(f"{col}=?")
                    vals.append(int(val) if isinstance(val, bool) else val)
            if updates:
                conn.execute(
                    f"UPDATE activity_rewards_config SET {', '.join(updates)} WHERE guild_id=?",
                    vals + [guild_id]
                )
            cfg = conn.execute(
                "SELECT msg_enabled,msg_per_n,msg_coins,msg_rep_per_n,msg_rep,"
                "voice_enabled,voice_per_min,voice_coins"
                " FROM activity_rewards_config WHERE guild_id=?", (guild_id,)
            ).fetchone()

        me, mp, mc, mrp, mr, ve, vp, vc = cfg
        emb = discord.Embed(title="⚙️ Пассивные награды", color=discord.Color.teal())
        msg_st   = "✅ Включены" if me else "⛔ Выключены"
        rep_line = f"+{mr} репа каждые {mrp} сообщений" if mrp else "Репа: выключена"
        emb.add_field(
            name="💬 Сообщения",
            value=f"{msg_st}\n+{mc} монет каждые {mp} сообщений\n{rep_line}",
            inline=False
        )
        voice_st = "✅ Включены" if ve else "⛔ Выключены"
        emb.add_field(
            name="🎙️ Голос",
            value=f"{voice_st}\n+{vc} монет каждые {vp} минут",
            inline=False
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @app_commands.command(name="награды_статус",
                          description="Текущие настройки пассивных наград")
    async def награды_статус(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        with sqlite3.connect(DB_PATH) as conn:
            cfg = conn.execute(
                "SELECT msg_enabled,msg_per_n,msg_coins,msg_rep_per_n,msg_rep,"
                "voice_enabled,voice_per_min,voice_coins"
                " FROM activity_rewards_config WHERE guild_id=?", (guild_id,)
            ).fetchone()
        if not cfg:
            await interaction.response.send_message(
                "⚙️ Пассивные награды не настроены. Используй `/награды_настроить`.",
                ephemeral=True)
            return
        me, mp, mc, mrp, mr, ve, vp, vc = cfg
        emb = discord.Embed(title="⚙️ Пассивные награды", color=discord.Color.teal())
        msg_v = ("✅" if me else "⛔") + f"\n+{mc} монет / {mp} сообщений"
        if mrp:
            msg_v += f"\n+{mr} репа / {mrp} сообщений"
        emb.add_field(name="💬 Сообщения", value=msg_v, inline=True)
        voice_v = ("✅" if ve else "⛔") + f"\n+{vc} монет / {vp} минут"
        emb.add_field(name="🎙️ Голос", value=voice_v, inline=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageAndVoiceStats(bot))
