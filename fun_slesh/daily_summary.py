# -*- coding: utf-8 -*-
# fun_slesh/daily_summary.py
"""
Итог дня в полночь МСК:
  - Хокку о событиях дня на основе проверяемой статистики
  - Авто-теги: о чём говорили (топ слов), кто играл (голосовые каналы)
  - Топ активных, топ войса

Команды:
  /итог_дня            — показать итог дня прямо сейчас
  /итог_дня_канал      — (Админ) канал для авто-постинга в полночь
  /итог_дня_вкл        — (Админ) включить/выключить авто-постинг
  /итог_недели         — показать еженедельный дайджест прямо сейчас
"""

import sqlite3, random, asyncio
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.paths import MESSAGES_DB, SOCIAL_DB
from core.summary_service import (
    DEFAULT_SUMMARY_TEXTS,
    SUMMARY_THEME_COLORS,
    block_enabled as _summary_block_enabled,
    block_title as _summary_block_title,
    bounded_int as _summary_int,
    merge_summary_settings,
    render_summary_template as _render_summary_template,
    truthy_setting as _truthy_payload,
)
from core.settings_store import (
    get_feature_payload,
    get_feature_policy,
    has_feature_setting,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)
from utils.logger import log as _base_log

DB_PATH = SOCIAL_DB
MSG_DB  = MESSAGES_DB
UTC     = timezone.utc
MSK     = ZoneInfo("Europe/Moscow")
FEATURE_DAILY_SUMMARY = "daily_summary"
SUMMARY_CUSTOM_ID_PREFIX = "vipik:summary"

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
log = _base_log.bind(src="daily_summary")

HAS_COMPONENTS_V2 = all(
    hasattr(discord.ui, name)
    for name in ("LayoutView", "Container", "TextDisplay")
)
HAS_MODAL_LABEL = hasattr(discord.ui, "Label")


def _summary_texts(guild_id: int) -> dict[str, str]:
    payload = get_feature_payload(guild_id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
    return merge_summary_settings(payload)


def _summary_limit(payload: dict, key: str, default: int) -> int:
    return _summary_int(payload, f"{key}_limit", default)


def _summary_color(payload: dict) -> discord.Color:
    raw = str(payload.get("summary_accent_color") or "").strip().lstrip("#")
    if len(raw) == 6:
        try:
            return discord.Color(int(raw, 16))
        except ValueError:
            pass
    theme = str(payload.get("summary_theme") or "neon").strip().lower()
    return discord.Color(SUMMARY_THEME_COLORS.get(theme, SUMMARY_THEME_COLORS["neon"]))


def _summary_buttons_enabled(payload: dict) -> bool:
    if "summary_buttons_enabled" not in payload:
        return True
    return _truthy_payload(payload.get("summary_buttons_enabled"))


def _summary_compact(payload: dict) -> bool:
    return _truthy_payload(payload.get("summary_compact_mode"))


def _summary_selected_game(payload: dict) -> str:
    return str(payload.get("game_spotlight_game") or "").strip()


def _summary_filter_game_rows(payload: dict, rows: list[tuple]) -> list[tuple]:
    if str(payload.get("game_filter_mode") or "all").strip() != "only_selected":
        return rows
    selected = _summary_selected_game(payload).casefold()
    if not selected:
        return rows
    result = []
    for row in rows:
        if len(row) >= 2 and str(row[1]).strip().casefold() == selected:
            result.append(row)
        elif row and str(row[0]).strip().casefold() == selected:
            result.append(row)
    return result


def _summary_selected_game_user_rows(payload: dict, stats: dict) -> list[tuple[int, int]]:
    selected = _summary_selected_game(payload).casefold()
    if str(payload.get("game_filter_mode") or "all").strip() != "only_selected" or not selected:
        return list(stats.get("top_game_users") or [])
    rows = []
    for user_id, activity_name, seconds in stats.get("top_user_games", []):
        if str(activity_name).strip().casefold() == selected:
            rows.append((int(user_id), int(seconds)))
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


def _apply_summary_branding(emb: discord.Embed, guild: discord.Guild, payload: dict):
    thumbnail_url = str(payload.get("summary_thumbnail_url") or "").strip()
    if thumbnail_url.startswith(("http://", "https://")):
        emb.set_thumbnail(url=thumbnail_url)
    icon_url = getattr(getattr(guild, "icon", None), "url", None)
    if icon_url:
        emb.set_author(name=f"ViPik • {guild.name}", icon_url=icon_url)
    else:
        emb.set_author(name=f"ViPik • {guild.name}")

# ── Шаблоны хокку ─────────────────────────────────────────────────────────────
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
    ("Сиськи звенят,\n"
     "Размер растёт понемногу.\n"
     "Ночь накрыла всех."),
    ("Споры, смех, игры —\n"
     "обычный вечер дружбы.\n"
     "Тишина пришла."),
]

REIMI_HAIKU_REQUEST = (
    "Рейми, напиши новое хокку.\n"
    "Данных сегодня почти нет.\n"
    "Сервер ждёт строки."
)


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS summary_post_log (
                guild_id     INTEGER NOT NULL,
                summary_type TEXT    NOT NULL,
                period_key   TEXT    NOT NULL,
                posted_at    TEXT    NOT NULL,
                PRIMARY KEY (guild_id, summary_type, period_key)
            );
        """)


def _summary_targets(bot: commands.Bot) -> list[tuple[int, int]]:
    targets = {}
    for guild in bot.guilds:
        policy = get_feature_policy(guild.id, FEATURE_DAILY_SUMMARY)
        if policy.enabled and policy.output_channel_id:
            targets[guild.id] = int(policy.output_channel_id)
    return sorted(targets.items())


def _save_summary_channel(guild_id: int, channel_id: int):
    set_feature_enabled(guild_id, FEATURE_DAILY_SUMMARY, True)
    set_feature_channel(guild_id, FEATURE_DAILY_SUMMARY, channel_id, "output", "Discord admin command")


def _save_summary_enabled(guild_id: int, enabled: bool):
    set_feature_enabled(guild_id, FEATURE_DAILY_SUMMARY, enabled)


# ── Сбор статистики за день ───────────────────────────────────────────────────
def _get_today_stats(guild_id: int) -> dict:
    today_date = datetime.now(MSK).date()
    today = today_date.isoformat()
    start_today_utc = datetime.combine(today_date, datetime.min.time(), MSK).astimezone(UTC).isoformat()
    start_tomorrow_utc = (
        datetime.combine(today_date + timedelta(days=1), datetime.min.time(), MSK)
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

        top_words = conn.execute(
            """
            SELECT word, SUM(count) AS total
            FROM msg_word_freq_daily
            WHERE guild_id=? AND date=?
            GROUP BY word
            ORDER BY total DESC, word ASC LIMIT 3
            """,
            (guild_id, today),
        ).fetchall() if _table_exists(conn, "msg_word_freq_daily") else []

        top_emojis = conn.execute(
            """
            SELECT emoji, SUM(count) AS total
            FROM msg_emoji_freq_daily
            WHERE guild_id=? AND date=?
            GROUP BY emoji
            ORDER BY total DESC, emoji ASC LIMIT 3
            """,
            (guild_id, today),
        ).fetchall() if _table_exists(conn, "msg_emoji_freq_daily") else []

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

        toxic_count = conn.execute(
            "SELECT COUNT(*) FROM toxicity_log WHERE guild_id=? AND logged_at>=? AND logged_at<?",
            (guild_id, start_today_utc, start_tomorrow_utc),
        ).fetchone()[0] if _table_exists(conn, "toxicity_log") else 0
        toxic_leader = conn.execute(
            """
            SELECT user_id, COUNT(*) AS total
            FROM toxicity_log
            WHERE guild_id=? AND logged_at>=? AND logged_at<?
            GROUP BY user_id
            ORDER BY total DESC LIMIT 1
            """,
            (guild_id, start_today_utc, start_tomorrow_utc),
        ).fetchone() if _table_exists(conn, "toxicity_log") else None
        toxic_quote = None
        if toxic_leader:
            toxic_quote = conn.execute(
                """
                SELECT msg_snippet
                FROM toxicity_log
                WHERE guild_id=? AND user_id=? AND logged_at>=? AND logged_at<?
                ORDER BY level DESC, logged_at DESC LIMIT 1
                """,
                (guild_id, int(toxic_leader[0]), start_today_utc, start_tomorrow_utc),
            ).fetchone()

        # Кто получил Размер
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
            ORDER BY total_seconds DESC LIMIT 10
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
            ORDER BY total_seconds DESC LIMIT 10
            """,
            (guild_id, start_today_utc, start_tomorrow_utc),
        ).fetchall() if _table_exists(conn, "activity_sessions") else []
        top_user_games = conn.execute(
            """
            SELECT user_id, activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY user_id, activity_name
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 200
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
        "top_words":     top_words,
        "top_emojis":    top_emojis,
        "top_games":      top_games,
        "top_game_users": top_game_users,
        "top_user_games": top_user_games,
        "voice_channels": [r[0] for r in voice_channels],
        "toxic_count":   toxic_count,
        "toxic_leader":  toxic_leader,
        "toxic_quote":   toxic_quote[0] if toxic_quote else None,
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


def _month_bounds_msk() -> tuple[date, date]:
    today = datetime.now(MSK).date()
    start_this_month = today.replace(day=1)
    if today.month == 12:
        start_next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        start_next_month = today.replace(month=today.month + 1, day=1)
    return start_this_month, start_next_month


def _top_rows(conn: sqlite3.Connection, query: str, params: tuple) -> list[tuple]:
    return conn.execute(query, params).fetchall()


def _get_period_stats(guild_id: int, start_period: date, end_period: date) -> dict:
    since = start_period.isoformat()
    until = end_period.isoformat()
    toxicity_week_code = (end_period - timedelta(days=1)).strftime("%Y-W%W") if (end_period - start_period).days <= 8 else ""
    start_period_utc = datetime.combine(start_period, datetime.min.time(), MSK).astimezone(UTC).isoformat()
    end_period_utc = datetime.combine(end_period, datetime.min.time(), MSK).astimezone(UTC).isoformat()

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
        top_word_terms = _top_rows(
            conn,
            """
            SELECT word, SUM(count) AS total
            FROM msg_word_freq_daily
            WHERE guild_id=? AND date>=? AND date<?
            GROUP BY word
            ORDER BY total DESC, word ASC LIMIT 3
            """,
            (guild_id, since, until),
        ) if _table_exists(conn, "msg_word_freq_daily") else []
        top_emoji_terms = _top_rows(
            conn,
            """
            SELECT emoji, SUM(count) AS total
            FROM msg_emoji_freq_daily
            WHERE guild_id=? AND date>=? AND date<?
            GROUP BY emoji
            ORDER BY total DESC, emoji ASC LIMIT 3
            """,
            (guild_id, since, until),
        ) if _table_exists(conn, "msg_emoji_freq_daily") else []
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
            (guild_id, toxicity_week_code),
        ) if toxicity_week_code and _table_exists(conn, "toxicity_weekly") else []
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
                start_period_utc,
                end_period_utc,
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
                start_period_utc,
                end_period_utc,
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
            ORDER BY total_seconds DESC LIMIT 10
            """,
            (
                guild_id,
                start_period_utc,
                end_period_utc,
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
            ORDER BY total_seconds DESC LIMIT 10
            """,
            (
                guild_id,
                start_period_utc,
                end_period_utc,
            ),
        ) if _table_exists(conn, "activity_sessions") else []
        top_user_games = _top_rows(
            conn,
            """
            SELECT user_id, activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY user_id, activity_name
            HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 200
            """,
            (guild_id, start_period_utc, end_period_utc),
        ) if _table_exists(conn, "activity_sessions") else []
        total_game_s = conn.execute(
            """
            SELECT COALESCE(SUM(seconds), 0)
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            """,
            (guild_id, start_period_utc, end_period_utc),
        ).fetchone()[0] if _table_exists(conn, "activity_sessions") else 0
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
                start_period_utc,
                end_period_utc,
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
                start_period_utc,
                end_period_utc,
            ),
        ) if _table_exists(conn, "activity_sessions") else []
        toxic_leader = _top_rows(
            conn,
            """
            SELECT user_id, COUNT(*) AS total
            FROM toxicity_log
            WHERE guild_id=? AND logged_at>=? AND logged_at<?
            GROUP BY user_id
            ORDER BY total DESC LIMIT 1
            """,
            (guild_id, start_period_utc, end_period_utc),
        ) if _table_exists(conn, "toxicity_log") else []
        toxic_quote = None
        if toxic_leader:
            quote_row = conn.execute(
                """
                SELECT msg_snippet
                FROM toxicity_log
                WHERE guild_id=? AND user_id=? AND logged_at>=? AND logged_at<?
                ORDER BY level DESC, logged_at DESC LIMIT 1
                """,
                (guild_id, int(toxic_leader[0][0]), start_period_utc, end_period_utc),
            ).fetchone()
            toxic_quote = quote_row[0] if quote_row else None

    return {
        "since": since,
        "until": until,
        "top_msgs": top_msgs,
        "top_words": top_words,
        "top_emojis": top_emojis,
        "top_word_terms": top_word_terms,
        "top_emoji_terms": top_emoji_terms,
        "top_voice": top_voice,
        "top_balance": top_balance,
        "top_streaks": top_streaks,
        "top_rep": top_rep,
        "top_toxic": top_toxic or toxic_leader,
        "top_heroes": top_heroes,
        "top_activities": top_activities,
        "top_games": top_games,
        "top_game_users": top_game_users,
        "top_user_games": top_user_games,
        "top_other_activities": top_other_activities,
        "top_activity_users": top_activity_users,
        "toxic_leader": toxic_leader[0] if toxic_leader else None,
        "toxic_quote": toxic_quote,
        "total_msgs": total_msgs,
        "total_voice_s": total_voice,
        "total_game_s": total_game_s,
    }


def _get_weekly_stats(guild_id: int) -> dict:
    start_prev_week, start_this_week = _week_bounds_msk()
    return _get_period_stats(guild_id, start_prev_week, start_this_week)


def _get_monthly_stats(guild_id: int) -> dict:
    start_month, start_next_month = _month_bounds_msk()
    return _get_period_stats(guild_id, start_month, start_next_month)


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
    limit: int = 5,
) -> str:
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (user_id, raw_value) in enumerate(rows[:limit], start=1):
        prefix = medals[i - 1] if i <= 3 else f"**{i}.**"
        value = int(raw_value) if cast_int else raw_value
        shown = value_formatter(value) if value_formatter else f"{value} {suffix}"
        lines.append(f"{prefix} {_member_name(guild, int(user_id))} — **{shown}**")
    return "\n".join(lines) if lines else "Пока пусто."


def _format_term_lines(rows: list[tuple]) -> str:
    if not rows:
        return "Пока пусто."
    return "\n".join(f"**{i}.** {term} — **{int(count)}**" for i, (term, count) in enumerate(rows[:3], start=1))


def _format_named_duration_lines(rows: list[tuple], limit: int = 5) -> str:
    if not rows:
        return "Пока пусто."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, seconds) in enumerate(rows[:limit], start=1):
        prefix = medals[i - 1] if i <= 3 else f"**{i}.**"
        lines.append(f"{prefix} {name} — **{_fmt_seconds(int(seconds))}**")
    return "\n".join(lines)


def _summary_metrics(stats: dict) -> str:
    parts = [
        f"💬 **{int(stats.get('total_msgs') or 0)}** сообщ.",
        f"🎙️ **{_fmt_seconds(int(stats.get('total_voice_s') or 0))}** войс",
        f"🎮 **{_fmt_seconds(int(stats.get('total_game_s') or 0))}** игры",
    ]
    return "  •  ".join(parts)


def _period_focus_line(guild: discord.Guild, stats: dict) -> str:
    focus = []
    if stats.get("top_msgs"):
        uid, total = stats["top_msgs"][0]
        focus.append(f"чат держал {_member_name(guild, int(uid))} ({int(total)} сообщ.)")
    if stats.get("top_game_users"):
        uid, seconds = stats["top_game_users"][0]
        focus.append(f"в играх лидировал {_member_name(guild, int(uid))} ({_fmt_seconds(int(seconds))})")
    if stats.get("top_games"):
        name, seconds = stats["top_games"][0]
        focus.append(f"главная игра: {name} ({_fmt_seconds(int(seconds))})")
    if not focus:
        return "Период прошёл тихо: статистика ещё копится."
    return " · ".join(focus[:3])


def _fit_field(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _format_user_game_lines(guild: discord.Guild, rows: list[tuple], limit: int = 10) -> str:
    if not rows:
        return "Пока пусто."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (user_id, game_name, seconds) in enumerate(rows[:limit], start=1):
        prefix = medals[i - 1] if i <= 3 else f"**{i}.**"
        lines.append(
            f"{prefix} {_member_name(guild, int(user_id))} — **{game_name}**, {_fmt_seconds(int(seconds))}"
        )
    return "\n".join(lines)


def _format_game_spotlight_lines(guild: discord.Guild, stats: dict, game_name: str, limit: int = 10) -> str:
    wanted = game_name.strip().casefold()
    rows = [
        (int(user_id), str(activity_name), int(seconds))
        for user_id, activity_name, seconds in stats.get("top_user_games", [])
        if str(activity_name).strip().casefold() == wanted
    ]
    if not rows:
        return ""
    rows.sort(key=lambda row: row[2], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (user_id, _activity_name, seconds) in enumerate(rows[:limit], start=1):
        prefix = medals[i - 1] if i <= 3 else f"**{i}.**"
        lines.append(f"{prefix} {_member_name(guild, user_id)} — **{_fmt_seconds(seconds)}**")
    return "\n".join(lines)


def _tracked_daily_lines(stats: dict) -> str:
    lines = ["💬 сообщения, слова и эмодзи: топ-3 слов и эмодзи длиннее 2 букв"]
    if stats.get("total_voice_s") or stats.get("top_voice"):
        lines.append("🎙️ голосовые сессии")
    if stats.get("total_game_s") or stats.get("top_games") or stats.get("top_game_users"):
        lines.append("🎮 Discord-игры и игровые привычки")
    if stats.get("rep_events"):
        lines.append("⭐ Размер")
    if stats.get("toxic_count"):
        lines.append("☢️ токсичность")
    return "\n".join(lines)


def _tracked_weekly_lines(stats: dict) -> str:
    lines = ["💬 сообщения, слова и эмодзи: топ-3 слов и эмодзи длиннее 2 букв"]
    if stats.get("total_voice_s") or stats.get("top_voice"):
        lines.append("🎙️ голосовые сессии")
    if stats.get("top_games") or stats.get("top_game_users") or stats.get("top_heroes"):
        lines.append("🎮 Discord-игры и игровые привычки")
    if stats.get("top_other_activities") or stats.get("top_activity_users"):
        lines.append("📡 прочие Discord-активности")
    if stats.get("top_balance") or stats.get("top_streaks"):
        lines.append("💰 экономика и дэйлики")
    if stats.get("top_rep"):
        lines.append("⭐ Размер")
    if stats.get("top_toxic"):
        lines.append("☢️ токсичность")
    return "\n".join(lines)


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


def _daily_winners(guild: discord.Guild, stats: dict) -> str:
    lines = []
    if stats.get("top_chatters"):
        uid, total = stats["top_chatters"][0]
        lines.append(f"💬 Чат: {_member_name(guild, int(uid))} — **{int(total)} сообщ.**")
    if stats.get("top_voice"):
        uid, seconds = stats["top_voice"][0]
        lines.append(f"🎙️ Войс: {_member_name(guild, int(uid))} — **{_fmt_seconds(int(seconds))}**")
    if stats.get("top_game_users"):
        uid, seconds = stats["top_game_users"][0]
        lines.append(f"🎮 Игры: {_member_name(guild, int(uid))} — **{_fmt_seconds(int(seconds))}**")
    return "\n".join(lines) if lines else "Сегодня победители спрятались в тумане."


def _weekly_champion_ids(stats: dict) -> list[int]:
    sources = [
        stats.get("top_msgs", []),
        stats.get("top_voice", []),
        stats.get("top_game_users", []),
        stats.get("top_rep", []),
    ]
    result = []
    for rows in sources:
        if rows:
            uid = int(rows[0][0])
            if uid not in result:
                result.append(uid)
    return result[:5]


def _build_winner_congrats(guild: discord.Guild, stats: dict) -> str:
    winners: dict[int, list[str]] = {}

    category_sources = [
        ("активность", stats["top_msgs"]),
        ("слова", stats["top_words"]),
        ("эмодзи", stats["top_emojis"]),
        ("войс", stats["top_voice"]),
        ("баланс", stats["top_balance"]),
        ("серии", stats["top_streaks"]),
        ("Размер", stats["top_rep"]),
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
        meme = random.choice([
            "сервер кивает как ведущий дешёвого шоу",
            "таблица лидеров просит автограф",
            "кубок сделан из чистого онлайна",
            "статистика хлопает стоя, но тихо",
        ])
        block = f"**{display}** — {', '.join(categories)}\n*{haiku}*\n_{meme}_"
        if phrase:
            block += f"\n> {phrase}"
        blocks.append(block)
    return "\n\n".join(blocks)


# ── Генерация хокку ───────────────────────────────────────────────────────────
async def _generate_haiku(stats: dict, guild: discord.Guild) -> str:
    """Собирает короткое хокку из проверяемой статистики дня."""
    if not any((stats.get("total_msgs"), stats.get("total_voice_s"), stats.get("total_game_s"))):
        return REIMI_HAIKU_REQUEST
    games = stats.get("top_games") or []
    game = games[0][0] if games else "ночной сервер"
    middle = "голоса держат связь" if stats.get("total_voice_s") else "сообщения не спят"
    endings = ["статистика помнит", "утро сохранит след", "сервер встретит рассвет"]
    return f"{game} зовёт\n{middle}\n{random.choice(endings)}"


# ── Формируем embed итога дня ─────────────────────────────────────────────────
async def _build_summary_embed(guild: discord.Guild, stats: dict) -> discord.Embed:
    haiku = await _generate_haiku(stats, guild)
    date_fmt = datetime.fromisoformat(stats["date"]).strftime("%d.%m.%Y")
    texts = _summary_texts(guild.id)
    summary_payload = get_feature_payload(guild.id, FEATURE_DAILY_SUMMARY)

    emb = discord.Embed(
        title=_render_summary_template(
            texts["daily_title_template"],
            date=date_fmt,
            guild=guild.name,
            haiku=haiku,
        ),
        description=_render_summary_template(
            texts["daily_description_template"],
            date=date_fmt,
            guild=guild.name,
            haiku=haiku,
        ),
        color=_summary_color(summary_payload)
    )
    _apply_summary_branding(emb, guild, summary_payload)
    daily_limit = _summary_int(summary_payload, "daily_top_limit", 3, maximum=10)
    large_limit = _summary_int(summary_payload, "period_top_limit", 5, maximum=15)
    compact = _summary_compact(summary_payload)

    # Статистика
    if _summary_block_enabled(summary_payload, "daily_block_stats") and (
        stats["total_msgs"] or stats["total_voice_s"] or stats.get("total_game_s")
    ):
        stat_parts = []
        if stats["total_msgs"]:
            stat_parts.append(f"💬 {stats['total_msgs']} сообщений")
        if stats["total_voice_s"]:
            stat_parts.append(f"🎙️ {_fmt_seconds(stats['total_voice_s'])} в войсе")
        if stats.get("total_game_s"):
            stat_parts.append(f"🎮 {_fmt_seconds(stats['total_game_s'])} в играх")
        emb.add_field(name=_summary_block_title(summary_payload, "daily_block_stats", "За день"), value=" · ".join(stat_parts), inline=False)

    if _summary_block_enabled(summary_payload, "daily_block_tracked") and not compact:
        emb.add_field(name=_summary_block_title(summary_payload, "daily_block_tracked", "Что трекалось"), value=_tracked_daily_lines(stats), inline=False)

    # Авто-теги: кто во что играл
    game_tags = []
    for ch_id in stats["voice_channels"]:
        ch = guild.get_channel(ch_id)
        if ch:
            game_tags.append(f"🎮 {ch.name}")
    if _summary_block_enabled(summary_payload, "daily_block_voice_games") and game_tags:
        emb.add_field(name=_summary_block_title(summary_payload, "daily_block_voice_games", "Играли"), value=" · ".join(set(game_tags)), inline=False)

    # Топ чаттеров
    if _summary_block_enabled(summary_payload, "daily_block_top_chatters") and stats["top_chatters"]:
        medals = ["🥇", "🥈", "🥉"]
        limit = _summary_limit(summary_payload, "daily_block_top_chatters", daily_limit)
        lines = [f"{medals[i] if i < 3 else f'**{i + 1}.**'} {_member_name(guild, int(uid))} — {cnt} сообщ." for i, (uid, cnt) in enumerate(stats["top_chatters"][:limit])]
        emb.add_field(name="🗣️ " + _summary_block_title(summary_payload, "daily_block_top_chatters", "Самые активные"), value="\n".join(lines), inline=True)

    # Топ войса
    if _summary_block_enabled(summary_payload, "daily_block_top_voice") and stats["top_voice"]:
        medals = ["🥇", "🥈", "🥉"]
        limit = _summary_limit(summary_payload, "daily_block_top_voice", daily_limit)
        lines = [f"{medals[i] if i < 3 else f'**{i + 1}.**'} {_member_name(guild, int(uid))} — {_fmt_seconds(int(sec))}" for i, (uid, sec) in enumerate(stats["top_voice"][:limit])]
        emb.add_field(name="🎙️ " + _summary_block_title(summary_payload, "daily_block_top_voice", "Топ войса"), value="\n".join(lines), inline=True)

    if _summary_block_enabled(summary_payload, "daily_block_top_words") and stats.get("top_words"):
        emb.add_field(name="📝 " + _summary_block_title(summary_payload, "daily_block_top_words", "Слова дня"), value=_format_term_lines(stats["top_words"]), inline=True)

    if _summary_block_enabled(summary_payload, "daily_block_top_emojis") and stats.get("top_emojis"):
        emb.add_field(name="😎 " + _summary_block_title(summary_payload, "daily_block_top_emojis", "Эмодзи дня"), value=_format_term_lines(stats["top_emojis"]), inline=True)

    top_games = _summary_filter_game_rows(summary_payload, list(stats.get("top_games") or []))
    if _summary_block_enabled(summary_payload, "daily_block_top_games") and top_games:
        lines = [
            f"**{i}.** {name} — **{_fmt_seconds(int(sec))}**"
            for i, (name, sec) in enumerate(top_games[:_summary_limit(summary_payload, "daily_block_top_games", large_limit)], start=1)
        ]
        emb.add_field(name="🎮 " + _summary_block_title(summary_payload, "daily_block_top_games", "Игры дня"), value=_fit_field("\n".join(lines)), inline=False)

    top_user_games = _summary_filter_game_rows(summary_payload, list(stats.get("top_user_games") or []))
    if _summary_block_enabled(summary_payload, "daily_block_user_games") and top_user_games:
        emb.add_field(
            name="🎮 " + _summary_block_title(summary_payload, "daily_block_user_games", "Кто во что играл"),
            value=_fit_field(_format_user_game_lines(guild, top_user_games, limit=_summary_limit(summary_payload, "daily_block_user_games", large_limit))),
            inline=False,
        )

    top_game_users = _summary_selected_game_user_rows(summary_payload, stats)
    if top_game_users:
        lines = [
            f"{['🥇', '🥈', '🥉'][i - 1] if i <= 3 else f'**{i}.**'} {_member_name(guild, int(uid))} — **{_fmt_seconds(int(sec))}**"
            for i, (uid, sec) in enumerate(top_game_users[:10], start=1)
        ]
        winner_id, winner_seconds = top_game_users[0]
        if _summary_block_enabled(summary_payload, "daily_block_game_users"):
            emb.add_field(name="🕹️ " + _summary_block_title(summary_payload, "daily_block_game_users", "Топ игроков дня"), value=_fit_field("\n".join(lines[:_summary_limit(summary_payload, "daily_block_game_users", large_limit)])), inline=False)
        if _summary_block_enabled(summary_payload, "daily_block_game_winner"):
            emb.add_field(
                name="🏆 " + _summary_block_title(summary_payload, "daily_block_game_winner", "Игровой победитель дня"),
                value=f"{_member_name(guild, int(winner_id))} — **{_fmt_seconds(int(winner_seconds))}**",
                inline=False,
            )

    if _summary_block_enabled(summary_payload, "daily_block_winners"):
        emb.add_field(name="🏆 " + _summary_block_title(summary_payload, "daily_block_winners", "Победители дня"), value=_daily_winners(guild, stats), inline=False)

    # Мелочи дня
    misc = []
    if stats["toxic_count"]:
        misc.append(f"☢️ Токсичных сообщений: {stats['toxic_count']}")
        if stats.get("toxic_leader") and stats.get("toxic_quote"):
            leader_id, leader_count = stats["toxic_leader"]
            misc.append(
                f"Лидер: {_member_name(guild, int(leader_id))} — {int(leader_count)} раз\n> {stats['toxic_quote']}"
            )
    if stats["rep_events"]:
        misc.append(f"⭐ Размера выдано: {stats['rep_events']}")
    if _summary_block_enabled(summary_payload, "daily_block_misc") and misc:
        emb.add_field(name=_summary_block_title(summary_payload, "daily_block_misc", "Прочее"), value=_fit_field("\n".join(misc)), inline=False)

    emb.set_footer(
        text=_render_summary_template(
            texts["daily_footer_template"],
            date=date_fmt,
            guild=guild.name,
            haiku=haiku,
        )
    )
    return emb


# ── Авто-постинг ─────────────────────────────────────────────────────────────
async def _post_daily_summary(bot: commands.Bot):
    for guild_id, ch_id in _summary_targets(bot):
        guild = bot.get_guild(guild_id)
        ch    = bot.get_channel(ch_id)
        if not guild or not ch:
            continue
        stats = _get_today_stats(guild_id)
        emb   = await _build_summary_embed(guild, stats)
        try:
            await _send_summary_message(ch, guild, emb, allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            log.bind(guild_id=guild_id, channel_id=ch_id).error(f"daily summary post failed: {e}")


def _weekly_period_key() -> str:
    start_prev_week, start_this_week = _week_bounds_msk()
    return f"{start_prev_week.isoformat()}_{start_this_week.isoformat()}"


def _monthly_period_key() -> str:
    start_month, start_next_month = _month_bounds_msk()
    return f"{start_month.isoformat()}_{start_next_month.isoformat()}"


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


async def _build_period_embed(
    guild: discord.Guild,
    stats: dict,
    *,
    title_prefix: str,
    color: discord.Color,
    period_word: str,
) -> discord.Embed:
    start = datetime.fromisoformat(stats["since"]).strftime("%d.%m")
    end = (datetime.fromisoformat(stats["until"]) - timedelta(days=1)).strftime("%d.%m")
    texts = _summary_texts(guild.id)
    summary_payload = get_feature_payload(guild.id, FEATURE_DAILY_SUMMARY)
    title_template = (
        texts["weekly_title_template"]
        if period_word == "неделю"
        else texts["monthly_title_template"]
    )

    emb = discord.Embed(
        title=_render_summary_template(
            title_template,
            start=start,
            end=end,
            guild=guild.name,
            period=period_word,
            title_prefix=title_prefix,
        ),
        description=(
            f"{_summary_metrics(stats)}\n"
            f"{_period_focus_line(guild, stats)}"
        ),
        color=_summary_color(summary_payload) if summary_payload else color,
    )
    _apply_summary_branding(emb, guild, summary_payload)
    period_limit = _summary_int(summary_payload, "period_top_limit", 5, maximum=15)
    compact = _summary_compact(summary_payload)
    if _summary_block_enabled(summary_payload, "period_block_main_people"):
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_main_people", "Главные люди"), value=_format_rank_lines(guild, stats["top_msgs"], "сообщ.", limit=_summary_limit(summary_payload, "period_block_main_people", period_limit)), inline=True)
    if _summary_block_enabled(summary_payload, "period_block_voice"):
        emb.add_field(
            name=_summary_block_title(summary_payload, "period_block_voice", "Войс"),
            value=_format_rank_lines(guild, stats["top_voice"], "", value_formatter=_fmt_seconds, limit=_summary_limit(summary_payload, "period_block_voice", period_limit)),
            inline=True,
        )
    if _summary_block_enabled(summary_payload, "period_block_rep"):
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_rep", "Размер"), value=_format_rank_lines(guild, stats["top_rep"], "Размера", limit=_summary_limit(summary_payload, "period_block_rep", period_limit)), inline=True)

    word_bits = []
    if stats.get("top_word_terms"):
        word_bits.append("**Слова:**\n" + _format_term_lines(stats["top_word_terms"]))
    if stats.get("top_emoji_terms"):
        word_bits.append("**Эмодзи:**\n" + _format_term_lines(stats["top_emoji_terms"]))
    if _summary_block_enabled(summary_payload, "period_block_words") and word_bits and not compact:
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_words", "О чём шумели"), value=_fit_field("\n\n".join(word_bits)), inline=False)

    game_bits = []
    if stats["top_heroes"]:
        game_bits.append("**Heroes:**\n" + _format_rank_lines(guild, stats["top_heroes"], "", value_formatter=_fmt_seconds))
    top_games = _summary_filter_game_rows(summary_payload, list(stats.get("top_games") or []))
    if top_games:
        game_bits.append("**Игры:**\n" + _format_named_duration_lines(top_games, limit=_summary_limit(summary_payload, "period_block_game_overview", period_limit)))
    top_game_users = _summary_selected_game_user_rows(summary_payload, stats)
    if top_game_users:
        game_bits.append("**Игроки:**\n" + _format_rank_lines(guild, top_game_users, "", value_formatter=_fmt_seconds, limit=_summary_limit(summary_payload, "period_block_game_overview", period_limit)))
    if _summary_block_enabled(summary_payload, "period_block_game_overview") and game_bits:
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_game_overview", "Игровой блок"), value=_fit_field("\n\n".join(game_bits)), inline=False)

    spotlight_game = str(summary_payload.get("game_spotlight_game") or "").strip()
    spotlight_label = str(summary_payload.get("game_spotlight_label") or "Задроты").strip()
    spotlight_enabled = _truthy_payload(summary_payload.get("game_spotlight_enabled"))
    if _summary_block_enabled(summary_payload, "period_block_game_spotlight") and spotlight_enabled and spotlight_game:
        spotlight_lines = _format_game_spotlight_lines(guild, stats, spotlight_game, limit=_summary_limit(summary_payload, "period_block_game_spotlight", period_limit))
        spotlight_title = _render_summary_template(
            texts["game_spotlight_title_template"],
            label=spotlight_label,
            game=spotlight_game,
            guild=guild.name,
            period=period_word,
            start=start,
            end=end,
        )
        if not spotlight_lines:
            spotlight_lines = _render_summary_template(
                texts["game_spotlight_empty_template"],
                label=spotlight_label,
                game=spotlight_game,
                guild=guild.name,
                period=period_word,
                start=start,
                end=end,
            )
        emb.add_field(name=spotlight_title, value=_fit_field(spotlight_lines), inline=False)

    labels = {
        "game": "игра",
        "streaming": "стрим",
        "listening": "слушает",
        "watching": "смотрит",
        "competing": "соревнование",
    }
    top_user_games = _summary_filter_game_rows(summary_payload, list(stats.get("top_user_games") or []))
    if _summary_block_enabled(summary_payload, "period_block_user_games") and top_user_games:
        emb.add_field(
            name=_summary_block_title(summary_payload, "period_block_user_games", "Кто во что играл"),
            value=_fit_field(_format_user_game_lines(guild, top_user_games, limit=_summary_limit(summary_payload, "period_block_user_games", period_limit))),
            inline=False,
        )
    if _summary_block_enabled(summary_payload, "period_block_other_activities") and stats.get("top_other_activities"):
        lines = []
        for i, (name, activity_type, seconds) in enumerate(stats["top_other_activities"], start=1):
            label = labels.get(activity_type, activity_type)
            lines.append(f"**{i}.** {name} ({label}) — **{_fmt_seconds(int(seconds))}**")
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_other_activities", "Другие активности"), value=_fit_field("\n".join(lines[:_summary_limit(summary_payload, "period_block_other_activities", period_limit)])), inline=False)

    if _summary_block_enabled(summary_payload, "period_block_balance"):
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_balance", "Баланс"), value=_format_rank_lines(guild, stats["top_balance"], "Сисек", limit=_summary_limit(summary_payload, "period_block_balance", period_limit)), inline=True)
    if _summary_block_enabled(summary_payload, "period_block_streaks"):
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_streaks", "Серии"), value=_format_rank_lines(guild, stats["top_streaks"], "дн.", limit=_summary_limit(summary_payload, "period_block_streaks", period_limit)), inline=True)
    if _summary_block_enabled(summary_payload, "period_block_toxic") and stats["top_toxic"]:
        toxic_text = _format_rank_lines(guild, stats["top_toxic"], "раз")
        if stats.get("toxic_leader") and stats.get("toxic_quote"):
            leader_id, leader_count = stats["toxic_leader"]
            toxic_text += f"\n\nЛидер недели: {_member_name(guild, int(leader_id))} — {int(leader_count)} раз\n> {stats['toxic_quote']}"
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_toxic", "Токсичность"), value=_fit_field(toxic_text), inline=False)
    if _summary_block_enabled(summary_payload, "period_block_champion_congrats"):
        emb.add_field(name=_summary_block_title(summary_payload, "period_block_champion_congrats", "Поздравления чемпионам"), value=_fit_field(_build_winner_congrats(guild, stats)), inline=False)
    emb.set_footer(
        text=_render_summary_template(
            texts["period_footer_template"],
            start=start,
            end=end,
            guild=guild.name,
            period=period_word,
            title_prefix=title_prefix,
        )
    )
    return emb


async def _build_weekly_embed(guild: discord.Guild, stats: dict) -> discord.Embed:
    return await _build_period_embed(
        guild,
        stats,
        title_prefix="🏆 Итоги недели",
        color=discord.Color.gold(),
        period_word="неделю",
    )


async def _build_monthly_embed(guild: discord.Guild, stats: dict) -> discord.Embed:
    return await _build_period_embed(
        guild,
        stats,
        title_prefix="📅 Итоги месяца",
        color=discord.Color.teal(),
        period_word="месяц",
    )


def _summary_interactions_enabled(guild_id: int) -> bool:
    payload = get_feature_payload(guild_id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
    return _summary_buttons_enabled(payload)


def _summary_view(guild_id: int) -> discord.ui.View | None:
    if not _summary_interactions_enabled(guild_id):
        return None
    return SummaryNavigationView()


def _summary_render_mode(guild_id: int) -> str:
    payload = get_feature_payload(guild_id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
    return str(payload.get("summary_render_mode") or "embed").strip()


def _embed_to_markdown(embed: discord.Embed, *, lead: str | None = None, limit_fields: int = 10) -> str:
    lines = []
    if lead:
        lines.append(str(lead))
    if embed.title:
        lines.append(f"# {embed.title}")
    if embed.description:
        lines.append(str(embed.description))
    for field in embed.fields[:limit_fields]:
        lines.append(f"## {field.name}\n{field.value}")
    footer = getattr(embed, "footer", None)
    footer_text = getattr(footer, "text", None)
    if footer_text:
        lines.append(f"-# {footer_text}")
    text = "\n\n".join(lines).strip()
    return text[:3800] if len(text) > 3800 else text


def _summary_v2_view(guild_id: int, embed: discord.Embed, *, lead: str | None = None):
    payload = get_feature_payload(guild_id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
    if _summary_render_mode(guild_id) != "components_v2" or not HAS_COMPONENTS_V2:
        return None
    return SummaryV2NavigationView(guild_id, embed, lead=lead)


async def _send_summary_message(target, guild: discord.Guild, embed: discord.Embed, *, content=None, allowed_mentions=None):
    if _summary_render_mode(guild.id) == "components_v2":
        v2 = _summary_v2_view(guild.id, embed, lead=content)
        if v2 is not None:
            try:
                return await target.send(view=v2, allowed_mentions=allowed_mentions)
            except Exception as exc:
                log.bind(guild_id=guild.id).warning(f"components v2 summary send failed, fallback to embed: {exc}")
    return await target.send(content=content, embed=embed, view=_summary_view(guild.id), allowed_mentions=allowed_mentions)


async def _followup_summary(interaction: discord.Interaction, embed: discord.Embed):
    guild = interaction.guild
    if guild and _summary_render_mode(guild.id) == "components_v2":
        v2 = _summary_v2_view(guild.id, embed)
        if v2 is not None:
            try:
                return await interaction.followup.send(view=v2, allowed_mentions=discord.AllowedMentions.none())
            except Exception as exc:
                log.bind(guild_id=guild.id).warning(f"components v2 summary followup failed, fallback to embed: {exc}")
    return await interaction.followup.send(
        embed=embed,
        view=_summary_view(guild.id) if guild else None,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _summary_select_options() -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label="Игры", value="games", emoji="🎮", description="Топ игр и выбранная игра"),
        discord.SelectOption(label="Активность", value="activity", emoji="🗣️", description="Сообщения, слова и эмодзи"),
        discord.SelectOption(label="Войс", value="voice", emoji="🎙️", description="Голосовая активность"),
        discord.SelectOption(label="Победители", value="winners", emoji="🏆", description="Кто забрал топы периода"),
    ]


def _summary_admin_allowed(interaction: discord.Interaction) -> bool:
    permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def _summary_section_embed(guild: discord.Guild, section: str) -> discord.Embed:
    payload = get_feature_payload(guild.id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
    week = _get_weekly_stats(guild.id)
    today = _get_today_stats(guild.id)
    emb = discord.Embed(color=_summary_color(payload))
    _apply_summary_branding(emb, guild, payload)
    if section == "games":
        emb.title = "🎮 Игровой слайд"
        rows = _summary_filter_game_rows(payload, list(week.get("top_games") or []))
        selected = _summary_selected_game(payload)
        if rows:
            emb.add_field(name="Топ игр недели", value=_fit_field(_format_named_duration_lines(rows, limit=_summary_int(payload, "period_top_limit", 5, maximum=15))), inline=False)
        if selected:
            spotlight = _format_game_spotlight_lines(guild, week, selected, limit=_summary_int(payload, "period_top_limit", 5, maximum=15))
            emb.add_field(name=f"Фокус: {selected}", value=_fit_field(spotlight or "За период никто не отметился."), inline=False)
    elif section == "activity":
        emb.title = "🗣️ Активность"
        if today.get("top_chatters"):
            emb.add_field(name="Сегодня в чате", value=_format_rank_lines(guild, today["top_chatters"], "сообщ.", limit=_summary_int(payload, "daily_top_limit", 3, maximum=10)), inline=False)
        bits = []
        if week.get("top_word_terms"):
            bits.append("**Слова:**\n" + _format_term_lines(week["top_word_terms"]))
        if week.get("top_emoji_terms"):
            bits.append("**Эмодзи:**\n" + _format_term_lines(week["top_emoji_terms"]))
        if bits:
            emb.add_field(name="О чём шумели", value=_fit_field("\n\n".join(bits)), inline=False)
    elif section == "voice":
        emb.title = "🎙️ Войс"
        emb.add_field(name="Сегодня", value=_format_rank_lines(guild, today.get("top_voice", []), "", value_formatter=_fmt_seconds, limit=_summary_int(payload, "daily_top_limit", 3, maximum=10)), inline=True)
        emb.add_field(name="Неделя", value=_format_rank_lines(guild, week.get("top_voice", []), "", value_formatter=_fmt_seconds, limit=_summary_int(payload, "period_top_limit", 5, maximum=15)), inline=True)
    elif section == "winners":
        emb.title = "🏆 Победители"
        emb.add_field(name="День", value=_fit_field(_daily_winners(guild, today)), inline=False)
        emb.add_field(name="Неделя", value=_fit_field(_build_winner_congrats(guild, week)), inline=False)
    else:
        emb.title = "Итоги"
        emb.description = "Выбери раздел ниже."
    if not emb.fields and not emb.description:
        emb.description = "Данных для этого раздела пока нет."
    return emb


async def _send_summary_section(interaction: discord.Interaction, section: str):
    if not interaction.guild:
        await interaction.response.send_message("Разделы доступны только на сервере.", ephemeral=True)
        return
    emb = _summary_section_embed(interaction.guild, section)
    if _summary_render_mode(interaction.guild.id) == "components_v2":
        v2 = _summary_v2_view(interaction.guild.id, emb)
        if v2 is not None:
            await interaction.response.send_message(view=v2, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
    await interaction.response.send_message(embed=emb, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def _add_modal_text_input(modal: discord.ui.Modal, label: str, *, default: str = "", max_length: int = 400) -> discord.ui.TextInput:
    text_input = discord.ui.TextInput(
        label=None if HAS_MODAL_LABEL else label,
        default=default[:max_length],
        max_length=max_length,
        required=False,
    )
    if HAS_MODAL_LABEL:
        modal.add_item(discord.ui.Label(text=label, component=text_input))
    else:
        modal.add_item(text_input)
    return text_input


class SummaryQuickSettingsModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        payload = get_feature_payload(guild_id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
        super().__init__(title="Быстрая настройка итогов")
        self.daily_title = _add_modal_text_input(
            self,
            "Заголовок дня",
            default=str(payload.get("daily_title_template") or DEFAULT_SUMMARY_TEXTS["daily_title_template"]),
            max_length=400,
        )
        self.footer = _add_modal_text_input(
            self,
            "Подпись дня",
            default=str(payload.get("daily_footer_template") or DEFAULT_SUMMARY_TEXTS["daily_footer_template"]),
            max_length=400,
        )
        self.game = _add_modal_text_input(
            self,
            "Игра в фокусе",
            default=str(payload.get("game_spotlight_game") or ""),
            max_length=120,
        )
        self.label = _add_modal_text_input(
            self,
            "Как назвать игроков",
            default=str(payload.get("game_spotlight_label") or "Задроты"),
            max_length=80,
        )

    async def on_submit(self, interaction: discord.Interaction):
        if not _summary_admin_allowed(interaction):
            await interaction.response.send_message("Эта настройка доступна только администраторам.", ephemeral=True)
            return
        payload = {
            "daily_title_template": str(self.daily_title.value or DEFAULT_SUMMARY_TEXTS["daily_title_template"]).strip(),
            "daily_footer_template": str(self.footer.value or DEFAULT_SUMMARY_TEXTS["daily_footer_template"]).strip(),
            "game_spotlight_game": str(self.game.value or "").strip(),
            "game_spotlight_label": str(self.label.value or "Задроты").strip(),
            "game_spotlight_enabled": bool(str(self.game.value or "").strip()),
        }
        set_feature_payload(self.guild_id, FEATURE_DAILY_SUMMARY, payload)
        await interaction.response.send_message("Настройки итогов сохранены. Следующий итог возьмёт новый текст и фокус.", ephemeral=True)


class SummaryNavigationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SummarySectionSelect(custom_id=f"{SUMMARY_CUSTOM_ID_PREFIX}:section"))

    async def _replace_embed(self, interaction: discord.Interaction, embed: discord.Embed):
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Сегодня", emoji="🌙", style=discord.ButtonStyle.primary, custom_id="vipik:summary:today")
    async def today(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
            return
        stats = _get_today_stats(interaction.guild.id)
        emb = await _build_summary_embed(interaction.guild, stats)
        await self._replace_embed(interaction, emb)

    @discord.ui.button(label="Неделя", emoji="🏆", style=discord.ButtonStyle.secondary, custom_id="vipik:summary:week")
    async def week(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
            return
        stats = _get_weekly_stats(interaction.guild.id)
        emb = await _build_weekly_embed(interaction.guild, stats)
        await self._replace_embed(interaction, emb)

    @discord.ui.button(label="Месяц", emoji="📅", style=discord.ButtonStyle.secondary, custom_id="vipik:summary:month")
    async def month(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
            return
        stats = _get_monthly_stats(interaction.guild.id)
        emb = await _build_monthly_embed(interaction.guild, stats)
        await self._replace_embed(interaction, emb)

    @discord.ui.button(label="Игры", emoji="🎮", style=discord.ButtonStyle.success, custom_id="vipik:summary:games")
    async def games(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Игровая выжимка доступна только на сервере.", ephemeral=True)
            return
        payload = get_feature_payload(interaction.guild.id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
        stats = _get_weekly_stats(interaction.guild.id)
        rows = _summary_filter_game_rows(payload, list(stats.get("top_games") or []))
        selected = _summary_selected_game(payload)
        title = "🎮 Игровая выжимка недели"
        if str(payload.get("game_filter_mode") or "") == "only_selected" and selected:
            title = f"🎮 Игровая выжимка: {selected}"
        emb = discord.Embed(title=title, color=_summary_color(payload))
        _apply_summary_branding(emb, interaction.guild, payload)
        if rows:
            emb.add_field(
                name="Топ игр",
                value=_fit_field(_format_named_duration_lines(rows, limit=_summary_int(payload, "period_top_limit", 5, maximum=15))),
                inline=False,
            )
        if selected:
            spotlight = _format_game_spotlight_lines(interaction.guild, stats, selected, limit=_summary_int(payload, "period_top_limit", 5, maximum=15))
            if spotlight:
                emb.add_field(name=f"Кто играл в {selected}", value=_fit_field(spotlight), inline=False)
        if not emb.fields:
            emb.description = "За неделю игровых данных пока нет."
        await interaction.response.send_message(embed=emb, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Настроить", emoji="⚙️", style=discord.ButtonStyle.danger, custom_id="vipik:summary:settings")
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Настройки доступны только на сервере.", ephemeral=True)
            return
        if not _summary_admin_allowed(interaction):
            await interaction.response.send_message("Быстрые настройки доступны только администраторам.", ephemeral=True)
            return
        await interaction.response.send_modal(SummaryQuickSettingsModal(interaction.guild.id))


class SummarySectionSelect(discord.ui.Select):
    def __init__(self, *, custom_id: str = f"{SUMMARY_CUSTOM_ID_PREFIX}:section"):
        super().__init__(
            custom_id=custom_id,
            placeholder="Открыть отдельный слайд",
            min_values=1,
            max_values=1,
            options=_summary_select_options(),
        )

    async def callback(self, interaction: discord.Interaction):
        await _send_summary_section(interaction, str(self.values[0]))


if HAS_COMPONENTS_V2:
    class SummaryV2NavigationView(discord.ui.LayoutView):
        def __init__(self, guild_id: int, embed: discord.Embed, *, lead: str | None = None):
            payload = get_feature_payload(guild_id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
            super().__init__(timeout=None if _summary_buttons_enabled(payload) else 180)
            self.clear_items()
            container = discord.ui.Container(accent_color=_summary_color(payload))
            container.add_item(discord.ui.TextDisplay(_embed_to_markdown(embed, lead=lead)))
            self.add_item(container)
            if _summary_buttons_enabled(payload):
                self._add_button("Сегодня", "🌙", discord.ButtonStyle.primary, self._today, f"{SUMMARY_CUSTOM_ID_PREFIX}:v2:today")
                self._add_button("Неделя", "🏆", discord.ButtonStyle.secondary, self._week, f"{SUMMARY_CUSTOM_ID_PREFIX}:v2:week")
                self._add_button("Месяц", "📅", discord.ButtonStyle.secondary, self._month, f"{SUMMARY_CUSTOM_ID_PREFIX}:v2:month")
                self._add_button("Игры", "🎮", discord.ButtonStyle.success, self._games, f"{SUMMARY_CUSTOM_ID_PREFIX}:v2:games")
                self._add_button("Настроить", "⚙️", discord.ButtonStyle.danger, self._settings, f"{SUMMARY_CUSTOM_ID_PREFIX}:v2:settings")
                self.add_item(SummarySectionSelect(custom_id=f"{SUMMARY_CUSTOM_ID_PREFIX}:v2:section"))

        def _add_button(self, label: str, emoji: str, style: discord.ButtonStyle, callback, custom_id: str):
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=custom_id)
            button.callback = callback
            self.add_item(button)

        async def _replace_embed(self, interaction: discord.Interaction, embed: discord.Embed):
            if not interaction.guild:
                await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
                return
            view = _summary_v2_view(interaction.guild.id, embed)
            await interaction.response.edit_message(view=view)

        async def _today(self, interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
                return
            stats = _get_today_stats(interaction.guild.id)
            emb = await _build_summary_embed(interaction.guild, stats)
            await self._replace_embed(interaction, emb)

        async def _week(self, interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
                return
            stats = _get_weekly_stats(interaction.guild.id)
            emb = await _build_weekly_embed(interaction.guild, stats)
            await self._replace_embed(interaction, emb)

        async def _month(self, interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("Итоги доступны только на сервере.", ephemeral=True)
                return
            stats = _get_monthly_stats(interaction.guild.id)
            emb = await _build_monthly_embed(interaction.guild, stats)
            await self._replace_embed(interaction, emb)

        async def _games(self, interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("Игровая выжимка доступна только на сервере.", ephemeral=True)
                return
            payload = get_feature_payload(interaction.guild.id, FEATURE_DAILY_SUMMARY, DEFAULT_SUMMARY_TEXTS)
            stats = _get_weekly_stats(interaction.guild.id)
            rows = _summary_filter_game_rows(payload, list(stats.get("top_games") or []))
            selected = _summary_selected_game(payload)
            emb = discord.Embed(title="🎮 Игровая выжимка недели", color=_summary_color(payload))
            _apply_summary_branding(emb, interaction.guild, payload)
            if rows:
                emb.add_field(
                    name="Топ игр",
                    value=_fit_field(_format_named_duration_lines(rows, limit=_summary_int(payload, "period_top_limit", 5, maximum=15))),
                    inline=False,
                )
            if selected:
                spotlight = _format_game_spotlight_lines(interaction.guild, stats, selected, limit=_summary_int(payload, "period_top_limit", 5, maximum=15))
                if spotlight:
                    emb.add_field(name=f"Кто играл в {selected}", value=_fit_field(spotlight), inline=False)
            if not emb.fields:
                emb.description = "За неделю игровых данных пока нет."
            view = _summary_v2_view(interaction.guild.id, emb)
            await interaction.response.send_message(view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        async def _settings(self, interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("Настройки доступны только на сервере.", ephemeral=True)
                return
            if not _summary_admin_allowed(interaction):
                await interaction.response.send_message("Быстрые настройки доступны только администраторам.", ephemeral=True)
                return
            await interaction.response.send_modal(SummaryQuickSettingsModal(interaction.guild.id))
else:
    class SummaryV2NavigationView:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("discord.py Components v2 is not available")


async def _post_weekly_summary(bot: commands.Bot):
    period_key = _weekly_period_key()

    for guild_id, ch_id in _summary_targets(bot):
        if _was_posted(guild_id, "weekly", period_key):
            continue
        guild = bot.get_guild(guild_id)
        channel = bot.get_channel(ch_id)
        if not guild or not channel:
            continue
        try:
            stats = _get_weekly_stats(guild_id)
            emb = await _build_weekly_embed(guild, stats)
            champion_ids = _weekly_champion_ids(stats)
            content = ""
            allowed = discord.AllowedMentions.none()
            if champion_ids:
                mentions = " ".join(f"<@{uid}>" for uid in champion_ids)
                texts = _summary_texts(guild_id)
                content = _render_summary_template(
                    texts["weekly_champion_message_template"],
                    mentions=mentions,
                    guild=guild.name,
                    period="неделю",
                )
                allowed = discord.AllowedMentions(users=True, roles=False, everyone=False)
            await _send_summary_message(channel, guild, emb, content=content or None, allowed_mentions=allowed)
            _mark_posted(guild_id, "weekly", period_key)
        except Exception as e:
            log.bind(guild_id=guild_id, channel_id=ch_id).error(f"weekly summary post failed: {e}")


async def _post_monthly_summary(bot: commands.Bot):
    period_key = _monthly_period_key()

    for guild_id, ch_id in _summary_targets(bot):
        if _was_posted(guild_id, "monthly", period_key):
            continue
        guild = bot.get_guild(guild_id)
        channel = bot.get_channel(ch_id)
        if not guild or not channel:
            continue
        try:
            stats = _get_monthly_stats(guild_id)
            emb = await _build_monthly_embed(guild, stats)
            champion_ids = _weekly_champion_ids(stats)
            content = ""
            allowed = discord.AllowedMentions.none()
            if champion_ids:
                mentions = " ".join(f"<@{uid}>" for uid in champion_ids)
                content = f"📅 Чемпионы месяца: {mentions}"
                allowed = discord.AllowedMentions(users=True, roles=False, everyone=False)
            await _send_summary_message(channel, guild, emb, content=content or None, allowed_mentions=allowed)
            _mark_posted(guild_id, "monthly", period_key)
        except Exception as e:
            log.bind(guild_id=guild_id, channel_id=ch_id).error(f"monthly summary post failed: {e}")


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
        description="Дневные, недельные и месячные итоги сервера"
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
        scheduler.add_job(
            _post_monthly_summary, "cron",
            day="last", hour=23, minute=59, timezone=MSK,
            args=[bot], id="monthly_summary", replace_existing=True, misfire_grace_time=48 * 3600
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
        await _followup_summary(interaction, emb)

    @summary_group.command(name="канал",
                           description="(Админ) Канал для авто-постинга итогов")
    @app_commands.checks.has_permissions(administrator=True)
    async def итог_дня_канал(self, interaction: discord.Interaction,
                               канал: discord.TextChannel):
        _save_summary_channel(interaction.guild.id, канал.id)
        await interaction.response.send_message(
            f"✅ Итоги будут постить в {канал.mention}: день — в 23:59 MSK, неделя — по воскресеньям в 23:59 MSK, месяц — в последний день месяца в 23:59 MSK.\nНастройка сохранена в admin feature settings.",
            ephemeral=True)

    @summary_group.command(name="вкл",
                           description="(Админ) Включить/выключить авто-постинг итогов")
    @app_commands.checks.has_permissions(administrator=True)
    async def итог_дня_вкл(self, interaction: discord.Interaction,
                             включить: bool):
        _save_summary_enabled(interaction.guild.id, включить)
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(
            f"{status} авто-постинг итогов дня, недели и месяца. Настройка сохранена в admin feature settings.", ephemeral=True)

    @summary_group.command(name="неделя",
                           description="Еженедельный дайджест главных топов сервера")
    async def итог_недели(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        stats = _get_weekly_stats(interaction.guild.id)
        emb = await _build_weekly_embed(interaction.guild, stats)
        await _followup_summary(interaction, emb)

    @summary_group.command(name="месяц",
                           description="Месячный дайджест главных топов сервера")
    async def итог_месяца(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        stats = _get_monthly_stats(interaction.guild.id)
        emb = await _build_monthly_embed(interaction.guild, stats)
        await _followup_summary(interaction, emb)

async def setup(bot: commands.Bot):
    bot.add_view(SummaryNavigationView())
    if HAS_COMPONENTS_V2:
        bot.add_view(SummaryV2NavigationView(0, discord.Embed(title="ViPik summary controls")))
    await bot.add_cog(DailySummary(bot))
