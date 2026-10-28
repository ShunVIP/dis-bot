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
        conn.execute('BEGIN IMMEDIATE')
        columns={r[1] for r in conn.execute('PRAGMA table_info(lol_live_watches)')}
        if 'destination' not in columns:
            conn.execute("ALTER TABLE lol_live_watches ADD COLUMN destination TEXT NOT NULL DEFAULT 'channel'")


def enabled(guild_id,user_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row=conn.execute('SELECT enabled FROM lol_live_watches WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone()
    return bool(row and row[0])


def destination(guild_id,user_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row=conn.execute('SELECT destination FROM lol_live_watches WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone()
    return row[0] if row else 'dm'


def watch_matches(guild_id,user_id,target):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        return bool(conn.execute('SELECT 1 FROM lol_live_watches WHERE guild_id=? AND user_id=? AND enabled=1 AND destination=?',
                                 (int(guild_id),int(user_id),target)).fetchone())


def set_watch(guild_id,user_id,enabled,destination=None):
    if destination not in {None,'dm','channel'}:raise ValueError('Неизвестный способ доставки.')
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if enabled:
            if not conn.execute("SELECT 1 FROM game_accounts WHERE discord_user_id=? AND game='lol' AND provider='riot'",(int(user_id),)).fetchone():
                raise ValueError('Сначала привяжи свой Riot ID в меню LoL.')
            count=conn.execute('SELECT COUNT(*) FROM lol_live_watches WHERE guild_id=? AND enabled=1 AND user_id<>?',(int(guild_id),int(user_id))).fetchone()[0]
            if count>=20:raise ValueError('У сервера уже 20 отслеживаемых игроков.')
        row=conn.execute('SELECT destination FROM lol_live_watches WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone()
        target=destination or (row[0] if row else 'dm')
        conn.execute('INSERT INTO lol_live_watches(guild_id,user_id,enabled,destination) VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET enabled=excluded.enabled,destination=excluded.destination',
                     (int(guild_id),int(user_id),int(bool(enabled)),target))


def watched_accounts(guild_id,member_ids):
    ensure_tables()
    members={int(i) for i in member_ids}
    with connection(SOCIAL_DB) as conn:
        rows=conn.execute('''SELECT a.discord_user_id,a.external_id,a.display_name,a.region,w.destination FROM game_accounts a
            JOIN lol_live_watches w ON w.user_id=a.discord_user_id AND w.guild_id=? AND w.enabled=1
            WHERE a.game='lol' AND a.provider='riot' ORDER BY a.updated_at DESC,a.id DESC''',(int(guild_id),)).fetchall()
    seen=set();result=[]
    for row in rows:
        if row[0] not in members or row[0] in seen:continue
        seen.add(row[0]);result.append(dict(user_id=row[0],puuid=row[1],name=row[2],platform=row[3],destination=row[4]))
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
