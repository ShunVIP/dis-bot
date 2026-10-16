"""Aion 2 guild data and bounded official-news cache in the shared social DB."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import connection
from core.paths import SOCIAL_DB


def ensure_tables():
    with connection(SOCIAL_DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS aion2_members(
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                nickname TEXT NOT NULL, region TEXT NOT NULL, server TEXT NOT NULL,
                class_name TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS aion2_news_cache(
                cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS aion2_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL, title TEXT NOT NULL, starts_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_aion2_events_guild_time
                ON aion2_events(guild_id,starts_at);
        """)


def save_member(guild_id, user_id, nickname, region, server, class_name):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute("""INSERT INTO aion2_members VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET nickname=excluded.nickname,
            region=excluded.region,server=excluded.server,class_name=excluded.class_name,
            updated_at=excluded.updated_at""",
            (int(guild_id),int(user_id),nickname,region,server,class_name,
             datetime.now(timezone.utc).isoformat()))


def list_members(guild_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute("SELECT user_id,nickname,region,server,class_name FROM aion2_members WHERE guild_id=? ORDER BY nickname LIMIT 50", (int(guild_id),)).fetchall()
    return [dict(zip(("user_id","nickname","region","server","class_name"), row)) for row in rows]


def delete_member(guild_id, user_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute("DELETE FROM aion2_members WHERE guild_id=? AND user_id=?", (int(guild_id),int(user_id)))


def get_cache():
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute("SELECT payload,fetched_at FROM aion2_news_cache WHERE cache_key='global'").fetchone()
    return (json.loads(row[0]),float(row[1])) if row else ([],0.0)


def set_cache(items, fetched_at):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute("INSERT OR REPLACE INTO aion2_news_cache VALUES('global',?,?)", (json.dumps(items[:30],ensure_ascii=False),float(fetched_at)))


def add_event(guild_id, creator_id, title, starts_at):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        # Retain only a small recent history, never a growing event archive.
        conn.execute("DELETE FROM aion2_events WHERE starts_at < datetime('now','-30 days')")
        count = conn.execute("SELECT COUNT(*) FROM aion2_events WHERE guild_id=? AND starts_at>?",(int(guild_id),datetime.now(timezone.utc).isoformat())).fetchone()[0]
        if count >= 50:
            raise ValueError("У сервера уже 50 будущих событий. Удали лишнее событие.")
        cursor = conn.execute("INSERT INTO aion2_events(guild_id,creator_id,title,starts_at,created_at) VALUES(?,?,?,?,?)",(int(guild_id),int(creator_id),title,starts_at,datetime.now(timezone.utc).isoformat()))
        return int(cursor.lastrowid)


def list_events(guild_id, now):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        return conn.execute("SELECT id,title,starts_at FROM aion2_events WHERE guild_id=? AND starts_at>? ORDER BY starts_at LIMIT 15",(int(guild_id),now)).fetchall()


def delete_event(guild_id, event_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        return bool(conn.execute("DELETE FROM aion2_events WHERE guild_id=? AND id=?",(int(guild_id),int(event_id))).rowcount)
