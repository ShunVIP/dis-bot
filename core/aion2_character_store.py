"""Aion 2 links share the canonical game_accounts table with other games."""
from datetime import datetime, timezone

from core import game_profiles
from core.db import connection

GAME = "aion2"
PROVIDER = "plaync"


def authorize(actor_id, target_id, is_admin=False):
    if int(actor_id) != int(target_id) and not is_admin:
        raise PermissionError("Менять персонажей другого участника может только администратор.")


def list_links(user_id):
    game_profiles.ensure_game_profile_tables()
    with connection(game_profiles.SOCIAL_DB) as conn:
        rows = conn.execute("""SELECT id,external_id,display_name,region,verified FROM game_accounts
            WHERE discord_user_id=? AND game=? AND provider=? ORDER BY updated_at DESC,id DESC""",
            (int(user_id),GAME,PROVIDER)).fetchall()
    result=[]
    for row in rows:
        server,character_id=row[1].split(":",1)
        result.append(dict(id=row[0],server_id=int(server),character_id=character_id,
                           name=row[2],region=row[3],verified=bool(row[4]),primary=not result))
    return result


def link(actor_id, target_id, server_id, character_id, name, *, is_admin=False):
    authorize(actor_id,target_id,is_admin)
    game_profiles.ensure_game_profile_tables()
    external_id=f"{int(server_id)}:{character_id}"
    now=datetime.now(timezone.utc).isoformat()
    with connection(game_profiles.SOCIAL_DB) as conn:
        # Serialize the capacity check and write, including concurrent modal submissions.
        conn.execute("BEGIN IMMEDIATE")
        existing=conn.execute("SELECT id FROM game_accounts WHERE discord_user_id=? AND game=? AND provider=? AND external_id=?",
                              (int(target_id),GAME,PROVIDER,external_id)).fetchone()
        count=conn.execute("SELECT COUNT(*) FROM game_accounts WHERE discord_user_id=? AND game=? AND provider=?",
                           (int(target_id),GAME,PROVIDER)).fetchone()[0]
        if not existing and count>=10:
            raise ValueError("Можно привязать до 10 персонажей. Сначала отвяжи лишнего.")
        conn.execute("""INSERT INTO game_accounts(discord_user_id,game,provider,region,external_id,display_name,verified,created_at,updated_at)
            VALUES(?,?,?,'eu',?,?,0,?,?) ON CONFLICT(discord_user_id,game,provider,external_id)
            DO UPDATE SET display_name=excluded.display_name,updated_at=excluded.updated_at,verified=0""",
            (int(target_id),GAME,PROVIDER,external_id,str(name)[:100],now,now))


def change_link(actor_id,target_id,account_id,*,remove=False,is_admin=False):
    authorize(actor_id,target_id,is_admin)
    if account_id is None:
        raise ValueError("Персонаж ещё не привязан.")
    game_profiles.ensure_game_profile_tables()
    with connection(game_profiles.SOCIAL_DB) as conn:
        if remove:
            cur=conn.execute("DELETE FROM game_accounts WHERE id=? AND discord_user_id=? AND game=? AND provider=?",
                             (int(account_id),int(target_id),GAME,PROVIDER))
        else:
            cur=conn.execute("UPDATE game_accounts SET updated_at=? WHERE id=? AND discord_user_id=? AND game=? AND provider=?",
                             (datetime.now(timezone.utc).isoformat(),int(account_id),int(target_id),GAME,PROVIDER))
        if not cur.rowcount:
            raise ValueError("Эта привязка уже удалена или принадлежит другому пользователю.")


def roster(user_ids):
    game_profiles.ensure_game_profile_tables()
    user_ids=list({int(v) for v in user_ids})
    result=[]
    with connection(game_profiles.SOCIAL_DB) as conn:
        for start in range(0,len(user_ids),400):
            batch=user_ids[start:start+400]
            marks=','.join('?' for _ in batch)
            rows=conn.execute(f"SELECT discord_user_id,display_name,external_id FROM game_accounts WHERE game=? AND provider=? AND discord_user_id IN ({marks}) ORDER BY updated_at DESC",(GAME,PROVIDER,*batch)).fetchall()
            result.extend({'user_id':r[0],'name':r[1],'server_id':int(r[2].split(':',1)[0])} for r in rows)
    return result
