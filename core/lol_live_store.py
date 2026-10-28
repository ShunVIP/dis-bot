"""Opt-in LoL live ranks and durable delivery references in canonical social.db."""
from datetime import datetime,timedelta,timezone

from core import game_profiles
from core.paths import SOCIAL_DB
from core.db import connection

FEATURE='lol_live_ranks'


def ensure_tables():
    game_profiles.ensure_game_profile_tables()
    with connection(SOCIAL_DB) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS lol_live_watches(
                guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,enabled INTEGER NOT NULL,
                PRIMARY KEY(guild_id,user_id));
            CREATE TABLE IF NOT EXISTS lol_live_deliveries(
                guild_id INTEGER NOT NULL,game_key TEXT NOT NULL,channel_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,PRIMARY KEY(guild_id,game_key));
        ''')


def enabled(guild_id,user_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row=conn.execute('SELECT enabled FROM lol_live_watches WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone()
    return bool(row and row[0])


def set_watch(guild_id,user_id,enabled):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if enabled:
            if not conn.execute("SELECT 1 FROM game_accounts WHERE discord_user_id=? AND game='lol' AND provider='riot'",(int(user_id),)).fetchone():
                raise ValueError('Сначала привяжи свой Riot ID в меню LoL.')
            count=conn.execute('SELECT COUNT(*) FROM lol_live_watches WHERE guild_id=? AND enabled=1 AND user_id<>?',(int(guild_id),int(user_id))).fetchone()[0]
            if count>=20:raise ValueError('У сервера уже 20 отслеживаемых игроков.')
        conn.execute('INSERT INTO lol_live_watches VALUES(?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET enabled=excluded.enabled',
                     (int(guild_id),int(user_id),int(bool(enabled))))


def watched_accounts(guild_id,member_ids):
    ensure_tables()
    members={int(i) for i in member_ids}
    with connection(SOCIAL_DB) as conn:
        rows=conn.execute('''SELECT a.discord_user_id,a.external_id,a.display_name,a.region FROM game_accounts a
            JOIN lol_live_watches w ON w.user_id=a.discord_user_id AND w.guild_id=? AND w.enabled=1
            WHERE a.game='lol' AND a.provider='riot' ORDER BY a.updated_at DESC,a.id DESC''',(int(guild_id),)).fetchall()
    seen=set();result=[]
    for row in rows:
        if row[0] not in members or row[0] in seen:continue
        seen.add(row[0]);result.append(dict(user_id=row[0],puuid=row[1],name=row[2],platform=row[3]))
    return result


def delivered(guild_id,game_key):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        return bool(conn.execute('SELECT 1 FROM lol_live_deliveries WHERE guild_id=? AND game_key=?',(int(guild_id),game_key)).fetchone())


def remember_delivery(guild_id,game_key,channel_id,message_id):
    ensure_tables()
    now=datetime.now(timezone.utc)
    with connection(SOCIAL_DB) as conn:
        conn.execute('DELETE FROM lol_live_deliveries WHERE created_at<?',((now-timedelta(days=30)).isoformat(),))
        conn.execute('INSERT OR IGNORE INTO lol_live_deliveries VALUES(?,?,?,?,?)',
                     (int(guild_id),game_key,int(channel_id),int(message_id),now.isoformat()))
