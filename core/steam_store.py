from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from core.db import connection
from core.paths import SOCIAL_DB


UTC = timezone.utc
_INITIALIZED_DATABASES: set[str] = set()


def ensure_tables() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS steam_profiles (
                user_id INTEGER PRIMARY KEY,
                steam_id TEXT NOT NULL,
                added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steam_wishlist_cache (
                user_id INTEGER NOT NULL,
                appid INTEGER NOT NULL,
                name TEXT NOT NULL,
                released INTEGER NOT NULL DEFAULT 0,
                discount INTEGER NOT NULL DEFAULT 0,
                price_rub INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (user_id, appid)
            );
            CREATE TABLE IF NOT EXISTS steam_manual_watchlist (
                user_id INTEGER NOT NULL,
                appid INTEGER NOT NULL,
                name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, appid)
            );
            CREATE TABLE IF NOT EXISTS steam_owned_games_cache (
                user_id INTEGER NOT NULL,
                appid INTEGER NOT NULL,
                name TEXT NOT NULL,
                playtime_forever INTEGER NOT NULL DEFAULT 0,
                playtime_2weeks INTEGER NOT NULL DEFAULT 0,
                last_played INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (user_id, appid)
            );
            CREATE TABLE IF NOT EXISTS steam_auto_settings (
                user_id INTEGER PRIMARY KEY,
                random_enabled INTEGER NOT NULL DEFAULT 1,
                challenge_enabled INTEGER NOT NULL DEFAULT 1,
                backlog_enabled INTEGER NOT NULL DEFAULT 1,
                backlog_tone TEXT NOT NULL DEFAULT 'soft'
            );
            CREATE TABLE IF NOT EXISTS steam_auto_log (
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                appid INTEGER,
                period_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, period_key)
            );
            """
        )
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def upsert_profile(user_id: int, steam_id: str) -> None:
    ensure_tables()
    now = datetime.now(UTC).isoformat()
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO steam_profiles(user_id, steam_id, added_at) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET steam_id=excluded.steam_id, added_at=excluded.added_at
            """,
            (int(user_id), str(steam_id), now),
        )
        conn.execute("INSERT OR IGNORE INTO steam_auto_settings(user_id) VALUES(?)", (int(user_id),))


def get_steam_id(user_id: int) -> str | None:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute("SELECT steam_id FROM steam_profiles WHERE user_id=?", (int(user_id),)).fetchone()
    return str(row[0]) if row else None


def list_profiles() -> list[tuple[int, str]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute("SELECT user_id, steam_id FROM steam_profiles ORDER BY user_id").fetchall()
    return [(int(user_id), str(steam_id)) for user_id, steam_id in rows]


def unlink_profile(user_id: int) -> bool:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        exists = conn.execute("SELECT 1 FROM steam_profiles WHERE user_id=?", (int(user_id),)).fetchone()
        if not exists:
            return False
        for table in (
            "steam_profiles",
            "steam_wishlist_cache",
            "steam_manual_watchlist",
            "steam_owned_games_cache",
            "steam_auto_settings",
            "steam_auto_log",
        ):
            conn.execute(f'DELETE FROM "{table}" WHERE user_id=?', (int(user_id),))
    return True


def upsert_owned_games(user_id: int, games: Iterable[dict[str, Any]]) -> None:
    ensure_tables()
    checked_at = datetime.now(UTC).isoformat()
    with connection(SOCIAL_DB) as conn:
        conn.executemany(
            """
            INSERT INTO steam_owned_games_cache(
                user_id, appid, name, playtime_forever, playtime_2weeks, last_played, checked_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(user_id, appid) DO UPDATE SET
                name=excluded.name,
                playtime_forever=excluded.playtime_forever,
                playtime_2weeks=excluded.playtime_2weeks,
                last_played=excluded.last_played,
                checked_at=excluded.checked_at
            """,
            [
                (
                    int(user_id),
                    int(game.get("appid", 0)),
                    str(game.get("name") or f"App {game.get('appid', '?')}"),
                    int(game.get("playtime_forever", 0)),
                    int(game.get("playtime_2weeks", 0)),
                    int(game.get("rtime_last_played", 0)),
                    checked_at,
                )
                for game in games
                if int(game.get("appid", 0)) > 0
            ],
        )


def list_manual_watchlist(user_id: int, *, limit: int | None = None) -> list[tuple[int, str]]:
    ensure_tables()
    sql = "SELECT appid, name FROM steam_manual_watchlist WHERE user_id=? ORDER BY added_at DESC"
    params: tuple[Any, ...] = (int(user_id),)
    if limit is not None:
        sql += " LIMIT ?"
        params += (max(1, int(limit)),)
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(int(appid), str(name)) for appid, name in rows]


def upsert_manual_watch(user_id: int, appid: int, name: str) -> None:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO steam_manual_watchlist(user_id, appid, name, added_at) VALUES(?,?,?,?)
            ON CONFLICT(user_id, appid) DO UPDATE SET name=excluded.name, added_at=excluded.added_at
            """,
            (int(user_id), int(appid), str(name), datetime.now(UTC).isoformat()),
        )


def remove_manual_watch(user_id: int, appid: int) -> bool:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        changed = conn.execute(
            "DELETE FROM steam_manual_watchlist WHERE user_id=? AND appid=?",
            (int(user_id), int(appid)),
        ).rowcount
    return bool(changed)


def upsert_wishlist_state(
    user_id: int,
    appid: int,
    name: str,
    released: int,
    discount: int,
    price_rub: int,
) -> tuple[int, int] | None:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        old = conn.execute(
            "SELECT released, discount FROM steam_wishlist_cache WHERE user_id=? AND appid=?",
            (int(user_id), int(appid)),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO steam_wishlist_cache(user_id,appid,name,released,discount,price_rub,checked_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(user_id,appid) DO UPDATE SET
                name=excluded.name,
                released=excluded.released,
                discount=excluded.discount,
                price_rub=excluded.price_rub,
                checked_at=excluded.checked_at
            """,
            (
                int(user_id), int(appid), str(name), int(released), int(discount), int(price_rub),
                datetime.now(UTC).isoformat(),
            ),
        )
    return (int(old[0]), int(old[1])) if old else None


def list_daily_prompt_profiles() -> list[tuple[int, str, int | None, int | None]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT p.user_id, p.steam_id, s.random_enabled, s.challenge_enabled
            FROM steam_profiles p
            LEFT JOIN steam_auto_settings s ON s.user_id=p.user_id
            WHERE COALESCE(s.random_enabled, 1)=1 OR COALESCE(s.challenge_enabled, 1)=1
            """
        ).fetchall()
    return [(int(a), str(b), c, d) for a, b, c, d in rows]


def list_backlog_profiles() -> list[tuple[int, str, int, str]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT p.user_id, p.steam_id, COALESCE(s.backlog_enabled, 1), COALESCE(s.backlog_tone, 'soft')
            FROM steam_profiles p
            LEFT JOIN steam_auto_settings s ON s.user_id=p.user_id
            WHERE COALESCE(s.backlog_enabled, 1)=1
            """
        ).fetchall()
    return [(int(a), str(b), int(c), str(d)) for a, b, c, d in rows]


def get_auto_settings(user_id: int) -> dict[str, Any]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute("INSERT OR IGNORE INTO steam_auto_settings(user_id) VALUES(?)", (int(user_id),))
        row = conn.execute(
            """
            SELECT random_enabled, challenge_enabled, backlog_enabled, backlog_tone
            FROM steam_auto_settings WHERE user_id=?
            """,
            (int(user_id),),
        ).fetchone()
    return {
        "random_enabled": bool(row[0]),
        "challenge_enabled": bool(row[1]),
        "backlog_enabled": bool(row[2]),
        "backlog_tone": str(row[3]),
    }


def update_auto_settings(user_id: int, **changes: Any) -> dict[str, Any]:
    ensure_tables()
    allowed = {
        "random_enabled": lambda value: int(bool(value)),
        "challenge_enabled": lambda value: int(bool(value)),
        "backlog_enabled": lambda value: int(bool(value)),
        "backlog_tone": lambda value: "hard" if str(value) == "hard" else "soft",
    }
    with connection(SOCIAL_DB) as conn:
        conn.execute("INSERT OR IGNORE INTO steam_auto_settings(user_id) VALUES(?)", (int(user_id),))
        for key, value in changes.items():
            if key not in allowed or value is None:
                continue
            conn.execute(
                f'UPDATE steam_auto_settings SET "{key}"=? WHERE user_id=?',
                (allowed[key](value), int(user_id)),
            )
    return get_auto_settings(user_id)


def auto_log_exists(user_id: int, kind: str, period_key: str) -> bool:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM steam_auto_log WHERE user_id=? AND kind=? AND period_key=?",
            (int(user_id), str(kind), str(period_key)),
        ).fetchone()
    return bool(row)


def mark_auto_log(user_id: int, kind: str, period_key: str, appid: int | None = None) -> bool:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        changed = conn.execute(
            """
            INSERT OR IGNORE INTO steam_auto_log(user_id, kind, appid, period_key, sent_at)
            VALUES(?,?,?,?,?)
            """,
            (int(user_id), str(kind), appid, str(period_key), datetime.now(UTC).isoformat()),
        ).rowcount
    return bool(changed)
