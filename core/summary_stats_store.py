from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from core.db import connection
from core.paths import SOCIAL_DB


MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def get_today_stats(guild_id: int, *, today_date: date | None = None) -> dict:
    current_date = today_date or datetime.now(MSK).date()
    today = current_date.isoformat()
    start_today_utc = datetime.combine(current_date, time.min, MSK).astimezone(UTC).isoformat()
    start_tomorrow_utc = datetime.combine(
        current_date + timedelta(days=1), time.min, MSK
    ).astimezone(UTC).isoformat()

    with connection(SOCIAL_DB) as conn:
        msg_rows = conn.execute(
            "SELECT user_id, SUM(messages) AS total FROM msg_stats_daily "
            "WHERE guild_id=? AND date=? GROUP BY user_id ORDER BY total DESC LIMIT 5",
            (int(guild_id), today),
        ).fetchall()
        top_words = conn.execute(
            """
            SELECT word, SUM(count) AS total
            FROM msg_word_freq_daily
            WHERE guild_id=? AND date=?
            GROUP BY word ORDER BY total DESC, word ASC LIMIT 3
            """,
            (int(guild_id), today),
        ).fetchall() if _table_exists(conn, "msg_word_freq_daily") else []
        top_emojis = conn.execute(
            """
            SELECT emoji, SUM(count) AS total
            FROM msg_emoji_freq_daily
            WHERE guild_id=? AND date=?
            GROUP BY emoji ORDER BY total DESC, emoji ASC LIMIT 3
            """,
            (int(guild_id), today),
        ).fetchall() if _table_exists(conn, "msg_emoji_freq_daily") else []
        total_msgs = conn.execute(
            "SELECT COALESCE(SUM(messages), 0) FROM msg_stats_daily WHERE guild_id=? AND date=?",
            (int(guild_id), today),
        ).fetchone()[0]
        voice_rows = conn.execute(
            "SELECT user_id, SUM(seconds) AS total FROM voice_totals_daily "
            "WHERE guild_id=? AND date=? GROUP BY user_id ORDER BY total DESC LIMIT 3",
            (int(guild_id), today),
        ).fetchall()
        total_voice = conn.execute(
            "SELECT COALESCE(SUM(seconds), 0) FROM voice_totals_daily WHERE guild_id=? AND date=?",
            (int(guild_id), today),
        ).fetchone()[0]
        voice_channels = conn.execute(
            "SELECT DISTINCT channel_id FROM voice_sessions WHERE guild_id=? AND DATE(started_at)=?",
            (int(guild_id), today),
        ).fetchall()

        toxic_count = conn.execute(
            "SELECT COUNT(*) FROM toxicity_log WHERE guild_id=? AND logged_at>=? AND logged_at<?",
            (int(guild_id), start_today_utc, start_tomorrow_utc),
        ).fetchone()[0] if _table_exists(conn, "toxicity_log") else 0
        toxic_leader = conn.execute(
            """
            SELECT user_id, COUNT(*) AS total
            FROM toxicity_log
            WHERE guild_id=? AND logged_at>=? AND logged_at<?
            GROUP BY user_id ORDER BY total DESC LIMIT 1
            """,
            (int(guild_id), start_today_utc, start_tomorrow_utc),
        ).fetchone() if _table_exists(conn, "toxicity_log") else None
        toxic_quote = None
        if toxic_leader:
            toxic_quote = conn.execute(
                """
                SELECT msg_snippet FROM toxicity_log
                WHERE guild_id=? AND user_id=? AND logged_at>=? AND logged_at<?
                ORDER BY level DESC, logged_at DESC LIMIT 1
                """,
                (int(guild_id), int(toxic_leader[0]), start_today_utc, start_tomorrow_utc),
            ).fetchone()

        rep_events = conn.execute(
            "SELECT COUNT(*) FROM reputation WHERE date=?", (today,)
        ).fetchone()[0] if _table_exists(conn, "reputation") else 0
        top_games = conn.execute(
            """
            SELECT activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY activity_name HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 10
            """,
            (int(guild_id), start_today_utc, start_tomorrow_utc),
        ).fetchall() if _table_exists(conn, "activity_sessions") else []
        top_game_users = conn.execute(
            """
            SELECT user_id, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY user_id HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 10
            """,
            (int(guild_id), start_today_utc, start_tomorrow_utc),
        ).fetchall() if _table_exists(conn, "activity_sessions") else []
        top_user_games = conn.execute(
            """
            SELECT user_id, activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
            FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            GROUP BY user_id, activity_name HAVING total_seconds > 0
            ORDER BY total_seconds DESC LIMIT 200
            """,
            (int(guild_id), start_today_utc, start_tomorrow_utc),
        ).fetchall() if _table_exists(conn, "activity_sessions") else []
        total_game_s = conn.execute(
            """
            SELECT COALESCE(SUM(seconds), 0) FROM activity_sessions
            WHERE guild_id=? AND started_at>=? AND started_at<? AND activity_type='game'
            """,
            (int(guild_id), start_today_utc, start_tomorrow_utc),
        ).fetchone()[0] if _table_exists(conn, "activity_sessions") else 0

    return {
        "date": today,
        "total_msgs": int(total_msgs or 0),
        "total_voice_s": int(total_voice or 0),
        "total_game_s": int(total_game_s or 0),
        "top_chatters": msg_rows,
        "top_voice": voice_rows,
        "top_words": top_words,
        "top_emojis": top_emojis,
        "top_games": top_games,
        "top_game_users": top_game_users,
        "top_user_games": top_user_games,
        "voice_channels": [row[0] for row in voice_channels],
        "toxic_count": int(toxic_count or 0),
        "toxic_leader": toxic_leader,
        "toxic_quote": toxic_quote[0] if toxic_quote else None,
        "rep_events": int(rep_events or 0),
    }
