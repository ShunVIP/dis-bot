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
  /итог_недели         — показать еженедельный дайджест прямо сейчас
"""

import os, sqlite3, random, asyncio
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.logger import log as _base_log

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
MSG_DB  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "messages.db"))
UTC     = timezone.utc
MSK     = ZoneInfo("Europe/Moscow")

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
log = _base_log.bind(src="daily_summary")

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

            CREATE TABLE IF NOT EXISTS summary_post_log (
                guild_id     INTEGER NOT NULL,
                summary_type TEXT    NOT NULL,
                period_key   TEXT    NOT NULL,
                posted_at    TEXT    NOT NULL,
                PRIMARY KEY (guild_id, summary_type, period_key)
            );
        """)


# ── Сбор статистики за день ───────────────────────────────────────────────────
def _get_today_stats(guild_id: int) -> dict:
    today = datetime.now(MSK).date().isoformat()
    start_today_utc = datetime.combine(datetime.now(MSK).date(), datetime.min.time(), MSK).astimezone(UTC).isoformat()
    start_tomorrow_utc = (
        datetime.combine(datetime.now(MSK).date() + timedelta(days=1), datetime.min.time(), MSK)
        .astimezone(UTC)
        .isoformat()
    )

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
        top_games = conn.execute(
            """
            SELECT activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY activity_name
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (guild_id, start_today_utc, start_tomorrow_utc),
        ).fetchall() if _table_exists(conn, "activity_sessions") else []
        top_game_users = conn.execute(
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY user_id
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (guild_id, start_today_utc, start_tomorrow_utc),
        ).fetchall() if _table_exists(conn, "activity_sessions") else []
        total_game_s = conn.execute(
            """
            SELECT COALESCE(SUM(seconds), 0)
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            """,
            (guild_id, start_today_utc, start_tomorrow_utc),
        ).fetchone()[0] if _table_exists(conn, "activity_sessions") else 0

    return {
        "date":          today,
        "total_msgs":    total_msgs,
        "total_voice_s": total_voice,
        "total_game_s":  total_game_s,
        "top_chatters":  msg_rows,
        "top_voice":     voice_rows,
        "top_games":      top_games,
        "top_game_users": top_game_users,
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


def _week_bounds_msk() -> tuple[date, date]:
    today = datetime.now(MSK).date()
    start_current_week = today - timedelta(days=today.weekday())
    if today.weekday() == 6:
        return start_current_week, today + timedelta(days=1)
    return start_current_week - timedelta(days=7), start_current_week


def _top_rows(conn: sqlite3.Connection, query: str, params: tuple) -> list[tuple]:
    return conn.execute(query, params).fetchall()


def _get_weekly_stats(guild_id: int) -> dict:
    start_prev_week, start_this_week = _week_bounds_msk()
    since = start_prev_week.isoformat()
    until = start_this_week.isoformat()
    last_week_code = (start_this_week - timedelta(days=1)).strftime("%Y-W%W")

    with sqlite3.connect(DB_PATH) as conn:
        top_msgs = _top_rows(
            conn,
            """
            SELECT user_id, SUM(messages) AS total
            FROM msg_stats_daily
            WHERE guild_id=? AND date>=? AND date<?
            GROUP BY user_id
            ORDER BY total DESC LIMIT 5
            """,
            (guild_id, since, until),
        )
        top_words = _top_rows(
            conn,
            """
            SELECT user_id, SUM(words) AS total
            FROM msg_stats_daily
            WHERE guild_id=? AND date>=? AND date<?
            GROUP BY user_id
            ORDER BY total DESC LIMIT 5
            """,
            (guild_id, since, until),
        )
        top_emojis = _top_rows(
            conn,
            """
            SELECT user_id, SUM(emojis) AS total
            FROM msg_stats_daily
            WHERE guild_id=? AND date>=? AND date<?
            GROUP BY user_id
            ORDER BY total DESC LIMIT 5
            """,
            (guild_id, since, until),
        )
        total_msgs = conn.execute(
            "SELECT COALESCE(SUM(messages), 0) FROM msg_stats_daily WHERE guild_id=? AND date>=? AND date<?",
            (guild_id, since, until),
        ).fetchone()[0]
        top_voice = _top_rows(
            conn,
            """
            SELECT user_id, SUM(seconds) AS total
            FROM voice_totals_daily
            WHERE guild_id=? AND date>=? AND date<?
            GROUP BY user_id
            ORDER BY total DESC LIMIT 5
            """,
            (guild_id, since, until),
        )
        total_voice = conn.execute(
            "SELECT COALESCE(SUM(seconds), 0) FROM voice_totals_daily WHERE guild_id=? AND date>=? AND date<?",
            (guild_id, since, until),
        ).fetchone()[0]
        top_balance = _top_rows(
            conn,
            """
            SELECT user_id, balance
            FROM coins_wallet
            ORDER BY balance DESC LIMIT 5
            """,
            (),
        ) if _table_exists(conn, "coins_wallet") else []
        top_streaks = _top_rows(
            conn,
            """
            SELECT user_id, streak
            FROM daily_rewards
            ORDER BY streak DESC LIMIT 5
            """,
            (),
        ) if _table_exists(conn, "daily_rewards") else []
        top_rep = _top_rows(
            conn,
            """
            SELECT user_id, SUM(delta) AS total
            FROM reputation
            GROUP BY user_id
            HAVING total > 0
            ORDER BY total DESC LIMIT 5
            """,
            (),
        ) if _table_exists(conn, "reputation") else []
        top_toxic = _top_rows(
            conn,
            """
            SELECT user_id, count
            FROM toxicity_weekly
            WHERE guild_id=? AND week=?
            ORDER BY count DESC LIMIT 5
            """,
            (guild_id, last_week_code),
        ) if _table_exists(conn, "toxicity_weekly") else []
        top_heroes = _top_rows(
            conn,
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM heroes_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<?
            GROUP BY user_id
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (
                guild_id,
                datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
                datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
            ),
        ) if _table_exists(conn, "heroes_sessions") else []
        top_activities = _top_rows(
            conn,
            """
            SELECT activity_name, activity_type, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<?
            GROUP BY activity_name, activity_type
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (
                guild_id,
                datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
                datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
            ),
        ) if _table_exists(conn, "activity_sessions") else []
        top_games = _top_rows(
            conn,
            """
            SELECT activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY activity_name
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (
                guild_id,
                datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
                datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
            ),
        ) if _table_exists(conn, "activity_sessions") else []
        top_game_users = _top_rows(
            conn,
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY user_id
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (
                guild_id,
                datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
                datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
            ),
        ) if _table_exists(conn, "activity_sessions") else []
        top_other_activities = _top_rows(
            conn,
            """
            SELECT activity_name, activity_type, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type<>'game'
            GROUP BY activity_name, activity_type
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (
                guild_id,
                datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
                datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
            ),
        ) if _table_exists(conn, "activity_sessions") else []
        top_activity_users = _top_rows(
            conn,
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<?
            GROUP BY user_id
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 5
            """,
            (
                guild_id,
                datetime.combine(start_prev_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
                datetime.combine(start_this_week, datetime.min.time(), MSK).astimezone(UTC).isoformat(),
            ),
        ) if _table_exists(conn, "activity_sessions") else []

    return {
        "since": since,
        "until": until,
        "top_msgs": top_msgs,
        "top_words": top_words,
        "top_emojis": top_emojis,
        "top_voice": top_voice,
        "top_balance": top_balance,
        "top_streaks": top_streaks,
        "top_rep": top_rep,
        "top_toxic": top_toxic,
        "top_heroes": top_heroes,
        "top_activities": top_activities,
        "top_games": top_games,
        "top_game_users": top_game_users,
        "top_other_activities": top_other_activities,
        "top_activity_users": top_activity_users,
        "total_msgs": total_msgs,
        "total_voice_s": total_voice,
    }


def _member_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(int(user_id))
    if member:
        return member.display_name
    return f"участник {user_id}"


def _format_rank_lines(
    guild: discord.Guild,
    rows: list[tuple],
    suffix: str,
    *,
    cast_int: bool = True,
    value_formatter=None,
) -> str:
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (user_id, raw_value) in enumerate(rows[:5], start=1):
        prefix = medals[i - 1] if i <= 3 else f"**{i}.**"
        value = int(raw_value) if cast_int else raw_value
        shown = value_formatter(value) if value_formatter else f"{value} {suffix}"
        lines.append(f"{prefix} {_member_name(guild, int(user_id))} — **{shown}**")
    return "\n".join(lines) if lines else "Пока пусто."


def _winner_phrase(user_id: int) -> str | None:
    try:
        from fun_slesh.parody_engine import generate_phrase, model_exists
        for quality in ("разум", "мем"):
            if model_exists(user_id, quality):
                phrase = generate_phrase(user_id, quality)
                if phrase:
                    return phrase
    except Exception:
        pass
    return None


def _winner_haiku(display_name: str, categories: list[str]) -> str:
    joined = ", ".join(categories[:3])
    return (
        f"Корона недели.\n"
        f"{display_name} забрал: {joined}.\n"
        f"Сервер шлёт салют."
    )


def _build_winner_congrats(guild: discord.Guild, stats: dict) -> str:
    winners: dict[int, list[str]] = {}

    category_sources = [
        ("активность", stats["top_msgs"]),
        ("слова", stats["top_words"]),
        ("эмодзи", stats["top_emojis"]),
        ("войс", stats["top_voice"]),
        ("баланс", stats["top_balance"]),
        ("серии", stats["top_streaks"]),
        ("репа", stats["top_rep"]),
        ("герои", stats["top_heroes"]),
        ("игры", stats.get("top_game_users", [])),
        ("активности", stats.get("top_activity_users", [])),
    ]

    for label, rows in category_sources:
        if not rows:
            continue
        user_id = int(rows[0][0])
        winners.setdefault(user_id, []).append(label)

    if not winners:
        return "На прошлой неделе не нашлось чемпионов для поздравления."

    blocks = []
    for user_id, categories in list(winners.items())[:5]:
        display = _member_name(guild, user_id)
        haiku = _winner_haiku(display, categories)
        phrase = _winner_phrase(user_id)
        block = f"**{display}** — {', '.join(categories)}\n*{haiku}*"
        if phrase:
            block += f"\n> {phrase}"
        blocks.append(block)
    return "\n\n".join(blocks)


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
        if stats.get("total_game_s"):
            context_parts.append(f"Discord-активности игр заняли {_fmt_seconds(stats['total_game_s'])}")
        if stats.get("top_games"):
            games = ", ".join(name for name, _seconds in stats["top_games"][:3])
            context_parts.append(f"главные игры дня: {games}")

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
    if stats["total_msgs"] or stats["total_voice_s"] or stats.get("total_game_s"):
        stat_parts = []
        if stats["total_msgs"]:
            stat_parts.append(f"💬 {stats['total_msgs']} сообщений")
        if stats["total_voice_s"]:
            stat_parts.append(f"🎙️ {_fmt_seconds(stats['total_voice_s'])} в войсе")
        if stats.get("total_game_s"):
            stat_parts.append(f"🎮 {_fmt_seconds(stats['total_game_s'])} в играх")
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

    if stats.get("top_games"):
        lines = [
            f"**{i}.** {name} — **{_fmt_seconds(int(sec))}**"
            for i, (name, sec) in enumerate(stats["top_games"][:5], start=1)
        ]
        emb.add_field(name="🎮 Игры дня", value="\n".join(lines), inline=False)

    if stats.get("top_game_users"):
        lines = [
            f"{['🥇', '🥈', '🥉'][i - 1] if i <= 3 else f'**{i}.**'} {_member_name(guild, int(uid))} — **{_fmt_seconds(int(sec))}**"
            for i, (uid, sec) in enumerate(stats["top_game_users"][:5], start=1)
        ]
        winner_id, winner_seconds = stats["top_game_users"][0]
        emb.add_field(name="🕹️ Топ игроков дня", value="\n".join(lines), inline=False)
        emb.add_field(
            name="🏆 Игровой победитель дня",
            value=f"{_member_name(guild, int(winner_id))} — **{_fmt_seconds(int(winner_seconds))}**",
            inline=False,
        )

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
            await ch.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            log.bind(guild_id=guild_id, channel_id=ch_id).error(f"daily summary post failed: {e}")


def _weekly_period_key() -> str:
    start_prev_week, start_this_week = _week_bounds_msk()
    return f"{start_prev_week.isoformat()}_{start_this_week.isoformat()}"


def _mark_posted(guild_id: int, summary_type: str, period_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO summary_post_log(guild_id, summary_type, period_key, posted_at)
            VALUES(?,?,?,?)
            """,
            (guild_id, summary_type, period_key, datetime.now(UTC).isoformat()),
        )
        conn.commit()


def _was_posted(guild_id: int, summary_type: str, period_key: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM summary_post_log
            WHERE guild_id=? AND summary_type=? AND period_key=?
            """,
            (guild_id, summary_type, period_key),
        ).fetchone()
    return bool(row)


async def _build_weekly_embed(guild: discord.Guild, stats: dict) -> discord.Embed:
    start = datetime.fromisoformat(stats["since"]).strftime("%d.%m")
    end = (datetime.fromisoformat(stats["until"]) - timedelta(days=1)).strftime("%d.%m")

    emb = discord.Embed(
        title=f"🏆 Итоги недели — {start}–{end}",
        description=(
            "Автоматический еженедельный дайджест по главным топам сервера.\n"
            "Собрано за завершившуюся неделю, постится по воскресеньям в 23:59 MSK."
        ),
        color=discord.Color.gold(),
    )
    emb.add_field(
        name="За неделю",
        value=f"💬 {stats['total_msgs']} сообщений\n🎙️ {_fmt_seconds(int(stats['total_voice_s']))} в войсе",
        inline=False,
    )
    emb.add_field(name="🗣️ Топ активности", value=_format_rank_lines(guild, stats["top_msgs"], "сообщ."), inline=False)
    emb.add_field(name="📝 Топ слов", value=_format_rank_lines(guild, stats["top_words"], "слов"), inline=True)
    emb.add_field(name="😎 Топ эмодзи", value=_format_rank_lines(guild, stats["top_emojis"], "эмодзи"), inline=True)
    emb.add_field(
        name="🎙️ Топ войса",
        value=_format_rank_lines(guild, stats["top_voice"], "", value_formatter=_fmt_seconds),
        inline=False,
    )
    if stats["top_heroes"]:
        emb.add_field(
            name="🏰 Топ Heroes за неделю",
            value=_format_rank_lines(guild, stats["top_heroes"], "", value_formatter=_fmt_seconds),
            inline=False,
        )
    labels = {
        "game": "игра",
        "streaming": "стрим",
        "listening": "слушает",
        "watching": "смотрит",
        "competing": "соревнование",
    }
    if stats.get("top_games"):
        lines = [
            f"**{i}.** {name} — **{_fmt_seconds(int(seconds))}**"
            for i, (name, seconds) in enumerate(stats["top_games"], start=1)
        ]
        emb.add_field(name="🎮 Топ игр", value="\n".join(lines), inline=False)
    if stats.get("top_game_users"):
        emb.add_field(
            name="🕹️ Топ игроков по играм",
            value=_format_rank_lines(guild, stats["top_game_users"], "", value_formatter=_fmt_seconds),
            inline=False,
        )
    if stats.get("top_other_activities"):
        lines = []
        for i, (name, activity_type, seconds) in enumerate(stats["top_other_activities"], start=1):
            label = labels.get(activity_type, activity_type)
            lines.append(f"**{i}.** {name} ({label}) — **{_fmt_seconds(int(seconds))}**")
        emb.add_field(name="📡 Другие активности", value="\n".join(lines), inline=False)
    if stats.get("top_activity_users"):
        emb.add_field(
            name="📊 Все активности по участникам",
            value=_format_rank_lines(guild, stats["top_activity_users"], "", value_formatter=_fmt_seconds),
            inline=False,
        )
    emb.add_field(name="💰 Топ баланса", value=_format_rank_lines(guild, stats["top_balance"], "монет"), inline=True)
    emb.add_field(name="🔥 Топ серий", value=_format_rank_lines(guild, stats["top_streaks"], "дн."), inline=True)
    emb.add_field(name="⭐ Топ репы", value=_format_rank_lines(guild, stats["top_rep"], "репы"), inline=False)
    if stats["top_toxic"]:
        emb.add_field(name="☢️ Топ токсиков", value=_format_rank_lines(guild, stats["top_toxic"], "раз"), inline=False)
    emb.add_field(name="🎐 Поздравления чемпионам", value=_build_winner_congrats(guild, stats), inline=False)
    emb.set_footer(text="Если хочешь тише — можно вынести этот дайджест в отдельный канал.")
    return emb


async def _post_weekly_summary(bot: commands.Bot):
    period_key = _weekly_period_key()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT guild_id, channel_id FROM daily_summary_config WHERE enabled=1 AND channel_id IS NOT NULL"
        ).fetchall()

    for guild_id, ch_id in rows:
        if _was_posted(guild_id, "weekly", period_key):
            continue
        guild = bot.get_guild(guild_id)
        channel = bot.get_channel(ch_id)
        if not guild or not channel:
            continue
        try:
            stats = _get_weekly_stats(guild_id)
            emb = await _build_weekly_embed(guild, stats)
            await channel.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
            _mark_posted(guild_id, "weekly", period_key)
        except Exception as e:
            log.bind(guild_id=guild_id, channel_id=ch_id).error(f"weekly summary post failed: {e}")


async def _catch_up_weekly_summary(bot: commands.Bot):
    await bot.wait_until_ready()
    now_msk = datetime.now(MSK)
    start_current_week = now_msk.date() - timedelta(days=now_msk.date().weekday())
    scheduled_at = datetime.combine(start_current_week + timedelta(days=6), datetime.min.time(), MSK).replace(hour=23, minute=59)
    catchup_until = scheduled_at + timedelta(hours=48)
    if now_msk < scheduled_at or now_msk >= catchup_until:
        return
    await _post_weekly_summary(bot)


# ── Cog ───────────────────────────────────────────────────────────────────────
class DailySummary(commands.Cog):
    summary_group = app_commands.Group(
        name="итоги",
        description="Дневные и недельные итоги сервера"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._weekly_catchup_started = False
        _ensure_tables()
        if not scheduler.running:
            scheduler.start()
        # Каждый день в 23:59 МСК
        scheduler.add_job(
            _post_daily_summary, "cron",
            hour=23, minute=59, timezone=MSK,
            args=[bot], id="daily_summary", replace_existing=True, misfire_grace_time=3600
        )
        scheduler.add_job(
            _post_weekly_summary, "cron",
            day_of_week="sun", hour=23, minute=59, timezone=MSK,
            args=[bot], id="weekly_summary", replace_existing=True, misfire_grace_time=48 * 3600
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if self._weekly_catchup_started:
            return
        self._weekly_catchup_started = True
        asyncio.create_task(_catch_up_weekly_summary(self.bot))

    # ── /итог_дня ─────────────────────────────────────────────────────────────
    @summary_group.command(name="день",
                           description="Итог сегодняшнего дня с хокку")
    async def итог_дня(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        stats = _get_today_stats(interaction.guild.id)
        emb   = await _build_summary_embed(interaction.guild, stats)
        await interaction.followup.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())

    @summary_group.command(name="канал",
                           description="(Админ) Канал для авто-постинга итогов")
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
            f"✅ Итоги будут постить в {канал.mention}: день — в 23:59 MSK, неделя — по воскресеньям в 23:59 MSK.",
            ephemeral=True)

    @summary_group.command(name="вкл",
                           description="(Админ) Включить/выключить авто-постинг итогов")
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
            f"{status} авто-постинг итогов дня и недели.", ephemeral=True)

    @summary_group.command(name="неделя",
                           description="Еженедельный дайджест главных топов сервера")
    async def итог_недели(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        stats = _get_weekly_stats(interaction.guild.id)
        emb = await _build_weekly_embed(interaction.guild, stats)
        await interaction.followup.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())

async def setup(bot: commands.Bot):
    await bot.add_cog(DailySummary(bot))
