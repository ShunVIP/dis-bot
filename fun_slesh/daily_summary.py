# -*- coding: utf-8 -*-
# fun_slesh/daily_summary.py
"""
Итог дня в полночь МСК:
  - Хокку о событиях дня (через GPT на основе статистики)
  - Авто-теги: о чём говорили (топ слов), кто играл (голосовые каналы)
  - Топ активных, топ войса

Команды:
  /итог_дня            — показать итог дня прямо сейчас
  /итог_дня_канал      — (Админ) канал для авто-постинга в полночь
  /итог_дня_вкл        — (Админ) включить/выключить авто-постинг
"""

import os, sqlite3, random, asyncio
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
MSG_DB  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "messages.db"))
UTC     = timezone.utc
MSK     = ZoneInfo("Europe/Moscow")

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ── Фолбэк хокку (если GPT недоступен) ───────────────────────────────────────
FALLBACK_HAIKU = [
    ("Слова летели —\n"
     "смех и споры в темноте.\n"
     "Сервер молчит."),
    ("Голоса стихли,\n"
     "только эхо в войс-чате.\n"
     "День прошёл — и всё."),
    ("Много сообщений,\n"
     "никто не сказал главного.\n"
     "Завтра попробуем."),
    ("Монеты звенят,\n"
     "репа растёт понемногу.\n"
     "Ночь накрыла всех."),
    ("Споры, смех, игры —\n"
     "обычный вечер дружбы.\n"
     "Тишина пришла."),
]


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_summary_config (
                guild_id  INTEGER PRIMARY KEY,
                channel_id INTEGER,
                enabled   INTEGER NOT NULL DEFAULT 1
            );
        """)


# ── Сбор статистики за день ───────────────────────────────────────────────────
def _get_today_stats(guild_id: int) -> dict:
    today = datetime.now(MSK).date().isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        # Топ активных по сообщениям
        msg_rows = conn.execute(
            "SELECT user_id, SUM(messages) as total"
            " FROM msg_stats_daily"
            " WHERE guild_id=? AND date=?"
            " GROUP BY user_id ORDER BY total DESC LIMIT 5",
            (guild_id, today)
        ).fetchall()

        # Топ слов
        word_rows = conn.execute(
            "SELECT user_id, SUM(words) as total"
            " FROM msg_stats_daily"
            " WHERE guild_id=? AND date=?"
            " GROUP BY user_id ORDER BY total DESC LIMIT 3",
            (guild_id, today)
        ).fetchall()

        # Всего сообщений
        total_msgs = conn.execute(
            "SELECT COALESCE(SUM(messages), 0) FROM msg_stats_daily"
            " WHERE guild_id=? AND date=?",
            (guild_id, today)
        ).fetchone()[0]

        # Топ голосовых
        voice_rows = conn.execute(
            "SELECT user_id, SUM(seconds) as total"
            " FROM voice_totals_daily"
            " WHERE guild_id=? AND date=?"
            " GROUP BY user_id ORDER BY total DESC LIMIT 3",
            (guild_id, today)
        ).fetchall()

        # Всего времени в войсе
        total_voice = conn.execute(
            "SELECT COALESCE(SUM(seconds), 0) FROM voice_totals_daily"
            " WHERE guild_id=? AND date=?",
            (guild_id, today)
        ).fetchone()[0]

        # Активные голосовые каналы (авто-теги)
        voice_channels = conn.execute(
            "SELECT DISTINCT channel_id FROM voice_sessions"
            " WHERE guild_id=? AND DATE(started_at)=?",
            (guild_id, today)
        ).fetchall()

        # Игровые события за день
        game_events = conn.execute(
            "SELECT COUNT(*) FROM toxicity_log WHERE guild_id=? AND DATE(logged_at)=?",
            (guild_id, today)
        ).fetchone()[0] if _table_exists(conn, "toxicity_log") else 0

        # Кто получил репу
        rep_events = conn.execute(
            "SELECT COUNT(*) FROM reputation WHERE date=?", (today,)
        ).fetchone()[0] if _table_exists(conn, "reputation") else 0

    return {
        "date":          today,
        "total_msgs":    total_msgs,
        "total_voice_s": total_voice,
        "top_chatters":  msg_rows,
        "top_voice":     voice_rows,
        "voice_channels": [r[0] for r in voice_channels],
        "toxic_count":   game_events,
        "rep_events":    rep_events,
    }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _fmt_seconds(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


# ── Генерация хокку ───────────────────────────────────────────────────────────
async def _generate_haiku(stats: dict, guild: discord.Guild) -> str:
    """Генерирует хокку через GPT или возвращает фолбэк."""
    try:
        from fun_slesh.parody_gpt import _load_model, _generate  # noqa
        # Формируем контекст для GPT
        context_parts = []
        if stats["total_msgs"]:
            context_parts.append(f"написано {stats['total_msgs']} сообщений")
        if stats["total_voice_s"]:
            context_parts.append(f"проведено в войсе {_fmt_seconds(stats['total_voice_s'])}")

        # Голосовые каналы → авто-теги
        game_tags = []
        for ch_id in stats["voice_channels"]:
            ch = guild.get_channel(ch_id)
            if ch:
                game_tags.append(ch.name)
        if game_tags:
            context_parts.append(f"играли в: {', '.join(set(game_tags))}")

        if stats["toxic_count"]:
            context_parts.append(f"было {stats['toxic_count']} токсичных сообщений")
        if stats["rep_events"]:
            context_parts.append(f"раздали {stats['rep_events']} репы")

        if not context_parts:
            return random.choice(FALLBACK_HAIKU)

        context = "; ".join(context_parts)
        prompt  = (
            f"Напиши японское хокку (три строки: 5-7-5 слогов) на русском языке "
            f"об этом игровом дне: {context}. "
            f"Хокку должно быть поэтичным, немного грустным или задумчивым, "
            f"с образами из геймерской жизни. Только три строки хокку, без пояснений."
        )

        import fun_slesh.parody_gpt as pgpt
        # Используем существующую инфраструктуру GPT
        result = await asyncio.get_event_loop().run_in_executor(
            None, _call_gpt_haiku, prompt
        )
        return result or random.choice(FALLBACK_HAIKU)
    except Exception:
        return random.choice(FALLBACK_HAIKU)


def _call_gpt_haiku(prompt: str) -> str | None:
    """Синхронный вызов GPT для хокку."""
    try:
        import fun_slesh.parody_gpt as pgpt
        model, tokenizer = pgpt._load_model()
        if model is None:
            return None
        result = pgpt._generate(model, tokenizer, prompt, max_new_tokens=80)
        # Берём первые три строки
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()][:3]
        return "\n".join(lines) if lines else None
    except Exception:
        return None


# ── Формируем embed итога дня ─────────────────────────────────────────────────
async def _build_summary_embed(guild: discord.Guild, stats: dict) -> discord.Embed:
    haiku = await _generate_haiku(stats, guild)
    date_fmt = datetime.fromisoformat(stats["date"]).strftime("%d.%m.%Y")

    emb = discord.Embed(
        title=f"🌙 Итог дня — {date_fmt}",
        description=f"*{haiku}*",
        color=discord.Color.dark_purple()
    )

    # Статистика
    if stats["total_msgs"] or stats["total_voice_s"]:
        stat_parts = []
        if stats["total_msgs"]:
            stat_parts.append(f"💬 {stats['total_msgs']} сообщений")
        if stats["total_voice_s"]:
            stat_parts.append(f"🎙️ {_fmt_seconds(stats['total_voice_s'])} в войсе")
        emb.add_field(name="За день", value=" · ".join(stat_parts), inline=False)

    # Авто-теги: кто во что играл
    game_tags = []
    for ch_id in stats["voice_channels"]:
        ch = guild.get_channel(ch_id)
        if ch:
            game_tags.append(f"🎮 {ch.name}")
    if game_tags:
        emb.add_field(name="Играли", value=" · ".join(set(game_tags)), inline=False)

    # Топ чаттеров
    if stats["top_chatters"]:
        lines = [f"<@{uid}> — {cnt} сообщ." for uid, cnt in stats["top_chatters"][:3]]
        emb.add_field(name="🗣️ Самые активные", value="\n".join(lines), inline=True)

    # Топ войса
    if stats["top_voice"]:
        lines = [f"<@{uid}> — {_fmt_seconds(int(sec))}" for uid, sec in stats["top_voice"][:3]]
        emb.add_field(name="🎙️ Топ войса", value="\n".join(lines), inline=True)

    # Мелочи дня
    misc = []
    if stats["toxic_count"]:
        misc.append(f"☢️ Токсичных сообщений: {stats['toxic_count']}")
    if stats["rep_events"]:
        misc.append(f"⭐ Репы выдано: {stats['rep_events']}")
    if misc:
        emb.add_field(name="Прочее", value="\n".join(misc), inline=False)

    emb.set_footer(text="Увидимся завтра 👋")
    return emb


# ── Авто-постинг ─────────────────────────────────────────────────────────────
async def _post_daily_summary(bot: commands.Bot):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT guild_id, channel_id FROM daily_summary_config"
            " WHERE enabled=1 AND channel_id IS NOT NULL"
        ).fetchall()

    for guild_id, ch_id in rows:
        guild = bot.get_guild(guild_id)
        ch    = bot.get_channel(ch_id)
        if not guild or not ch:
            continue
        stats = _get_today_stats(guild_id)
        emb   = await _build_summary_embed(guild, stats)
        try:
            await ch.send(embed=emb)
        except Exception:
            pass


# ── Cog ───────────────────────────────────────────────────────────────────────
class DailySummary(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        if not scheduler.running:
            scheduler.start()
        # Каждый день в 23:59 МСК
        scheduler.add_job(
            _post_daily_summary, "cron",
            hour=23, minute=59, timezone=MSK,
            args=[bot], id="daily_summary", replace_existing=True
        )

    # ── /итог_дня ─────────────────────────────────────────────────────────────
    @app_commands.command(name="итог_дня",
                          description="Итог сегодняшнего дня с хокку")
    async def итог_дня(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        stats = _get_today_stats(interaction.guild.id)
        emb   = await _build_summary_embed(interaction.guild, stats)
        await interaction.followup.send(embed=emb)

    # ── /итог_дня_канал ───────────────────────────────────────────────────────
    @app_commands.command(name="итог_дня_канал",
                          description="(Админ) Канал для авто-постинга итога дня в полночь")
    @app_commands.checks.has_permissions(administrator=True)
    async def итог_дня_канал(self, interaction: discord.Interaction,
                               канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO daily_summary_config(guild_id, channel_id, enabled)"
                " VALUES(?,?,1)"
                " ON CONFLICT(guild_id) DO UPDATE SET"
                " channel_id=excluded.channel_id, enabled=1",
                (interaction.guild.id, канал.id)
            )
        await interaction.response.send_message(
            f"✅ Итог дня будет постить в {канал.mention} каждый день в 23:59 МСК.",
            ephemeral=True)

    # ── /итог_дня_вкл ─────────────────────────────────────────────────────────
    @app_commands.command(name="итог_дня_вкл",
                          description="(Админ) Включить/выключить авто-постинг итога дня")
    @app_commands.checks.has_permissions(administrator=True)
    async def итог_дня_вкл(self, interaction: discord.Interaction,
                             включить: bool):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO daily_summary_config(guild_id, enabled) VALUES(?,?)"
                " ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled",
                (interaction.guild.id, int(включить))
            )
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(
            f"{status} авто-постинг итога дня.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DailySummary(bot))
