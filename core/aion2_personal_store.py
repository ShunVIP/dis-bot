"""Bounded private checklist/history in canonical social.db, never extra DB files."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from core.db import connection
from core.paths import SOCIAL_DB
from core import game_profiles
from core.aion2_character_service import character_ref
from core.aion2_personal_service import DEFAULT_TASKS, week_window


def ensure_tables():
    with connection(SOCIAL_DB) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS aion2_check_tasks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,label TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS aion2_check_owner ON aion2_check_tasks(guild_id,user_id);
            CREATE TABLE IF NOT EXISTS aion2_check_marks(
                task_id INTEGER NOT NULL REFERENCES aion2_check_tasks(id) ON DELETE CASCADE,
                week TEXT NOT NULL,done INTEGER NOT NULL,PRIMARY KEY(task_id,week));
            CREATE TABLE IF NOT EXISTS aion2_character_snapshots(
                id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,external_id TEXT NOT NULL,
                captured_at TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS aion2_snapshot_owner ON aion2_character_snapshots(user_id,external_id,id);
        ''')


def checklist(guild_id,user_id,now=None):
    ensure_tables()
    week,reset=week_window(now)
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM aion2_check_tasks WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone():
            conn.executemany('INSERT INTO aion2_check_tasks(guild_id,user_id,label) VALUES(?,?,?)',
                             [(int(guild_id),int(user_id),label) for label in DEFAULT_TASKS])
        cutoff=(datetime.fromisoformat(week)-timedelta(days=56)).isoformat()
        conn.execute('DELETE FROM aion2_check_marks WHERE week<?',(cutoff,))
        rows=conn.execute('''SELECT t.id,t.label,COALESCE(m.done,0) FROM aion2_check_tasks t
            LEFT JOIN aion2_check_marks m ON m.task_id=t.id AND m.week=?
            WHERE t.guild_id=? AND t.user_id=? ORDER BY t.id''',(week,int(guild_id),int(user_id))).fetchall()
    return {'week':week,'reset':reset,'tasks':[{'id':r[0],'label':r[1],'done':bool(r[2])} for r in rows]}


def toggle_task(guild_id,user_id,task_id,expected_week,now=None):
    ensure_tables()
    if expected_week!=week_window(now)[0]:raise ValueError('Началась новая игровая неделя. Обнови чеклист.')
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM aion2_check_tasks WHERE id=? AND guild_id=? AND user_id=?',
                            (int(task_id),int(guild_id),int(user_id))).fetchone():raise ValueError('Задача не найдена в твоём списке.')
        conn.execute('''INSERT INTO aion2_check_marks VALUES(?,?,1)
            ON CONFLICT(task_id,week) DO UPDATE SET done=NOT done''',(int(task_id),expected_week))


def add_task(guild_id,user_id,label):
    ensure_tables()
    label=str(label).strip()
    if not 1<=len(label)<=80:raise ValueError('Название задачи: от 1 до 80 символов.')
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT COUNT(*) FROM aion2_check_tasks WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone()[0]>=20:
            raise ValueError('В личном списке уже 20 задач.')
        conn.execute('INSERT INTO aion2_check_tasks(guild_id,user_id,label) VALUES(?,?,?)',(int(guild_id),int(user_id),label))


def delete_task(guild_id,user_id,task_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT COUNT(*) FROM aion2_check_tasks WHERE guild_id=? AND user_id=?',(int(guild_id),int(user_id))).fetchone()[0]<=1:
            raise ValueError('Оставь хотя бы одну задачу; сначала можно добавить новую.')
        if not conn.execute('DELETE FROM aion2_check_tasks WHERE id=? AND guild_id=? AND user_id=?',
                            (int(task_id),int(guild_id),int(user_id))).rowcount:raise ValueError('Задача не найдена в твоём списке.')


def external_id(ref):
    ref=character_ref(ref['server_id'],ref['character_id'])
    return f"{ref['server_id']}:{ref['character_id']}"


def save_snapshot(user_id,ref,data):
    game_profiles.ensure_game_profile_tables()
    ensure_tables()
    external=external_id(ref)
    payload=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if len(payload)>24000:raise ValueError('Снимок персонажа слишком большой.')
    fingerprint=hashlib.sha256(payload.encode()).hexdigest()
    with connection(SOCIAL_DB) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute("SELECT 1 FROM game_accounts WHERE discord_user_id=? AND game='aion2' AND provider='plaync' AND external_id=?",(int(user_id),external)).fetchone():
            raise PermissionError('Сохранять прогресс можно только своего привязанного персонажа.')
        last=conn.execute('SELECT fingerprint FROM aion2_character_snapshots WHERE user_id=? AND external_id=? ORDER BY id DESC LIMIT 1',(int(user_id),external)).fetchone()
        created=not last or last[0]!=fingerprint
        if created:
            conn.execute('INSERT INTO aion2_character_snapshots(user_id,external_id,captured_at,fingerprint,payload) VALUES(?,?,?,?,?)',
                         (int(user_id),external,datetime.now(timezone.utc).isoformat(),fingerprint,payload))
        conn.execute('''DELETE FROM aion2_character_snapshots WHERE user_id=? AND external_id=? AND id NOT IN
            (SELECT id FROM aion2_character_snapshots WHERE user_id=? AND external_id=? ORDER BY id DESC LIMIT 30)''',
                     (int(user_id),external,int(user_id),external))
        conn.execute('''DELETE FROM aion2_character_snapshots WHERE user_id=? AND id NOT IN
            (SELECT id FROM aion2_character_snapshots WHERE user_id=? ORDER BY id DESC LIMIT 300)''',(int(user_id),int(user_id)))
    return created


def snapshot_history(user_id,ref):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows=conn.execute('SELECT captured_at,payload FROM aion2_character_snapshots WHERE user_id=? AND external_id=? ORDER BY id DESC LIMIT 30',
                          (int(user_id),external_id(ref))).fetchall()
    return [{'captured_at':r[0],'data':json.loads(r[1])} for r in rows]


def clear_history(user_id):
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute('DELETE FROM aion2_character_snapshots WHERE user_id=?',(int(user_id),))
