from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from core.db import connection as db_connection
from core.gamer_profile_service import classify_game_signals
from core.paths import SOCIAL_DB


UTC = timezone.utc


def ensure_gamer_profile_storage() -> None:
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS gamer_profiles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                profile_json TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id,user_id)
            );
            """
        )


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def load_game_signals(guild_id: int, user_id: int) -> list[tuple[str, int]]:
    signals: dict[str, tuple[str, int]] = {}
    with db_connection(SOCIAL_DB) as conn:
        if _table_exists(conn, "activity_sessions"):
            if int(guild_id):
                rows = conn.execute(
                    """
                    SELECT activity_name,COALESCE(SUM(seconds),0) FROM activity_sessions
                    WHERE guild_id=? AND user_id=? AND activity_type='game'
                    GROUP BY activity_name
                    """,
                    (int(guild_id), int(user_id)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT activity_name,COALESCE(SUM(seconds),0) FROM activity_sessions
                    WHERE user_id=? AND activity_type='game' GROUP BY activity_name
                    """,
                    (int(user_id),),
                ).fetchall()
            for name, seconds in rows:
                key = str(name).strip().lower()
                signals[key] = (str(name), max(int(seconds or 0), signals.get(key, ("", 0))[1]))
        if _table_exists(conn, "steam_owned_games_cache"):
            rows = conn.execute(
                "SELECT name,playtime_forever FROM steam_owned_games_cache WHERE user_id=?",
                (int(user_id),),
            ).fetchall()
            for name, minutes in rows:
                key = str(name).strip().lower()
                seconds = max(0, int(minutes or 0)) * 60
                signals[key] = (str(name), max(seconds, signals.get(key, ("", 0))[1]))
    return list(signals.values())


def refresh_gamer_profile(guild_id: int, user_id: int) -> dict[str, object]:
    ensure_gamer_profile_storage()
    signals = load_game_signals(guild_id, user_id)
    source_json = json.dumps(sorted(signals), ensure_ascii=False, separators=(",", ":"))
    source_hash = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT profile_json,source_hash FROM gamer_profiles WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        ).fetchone()
        if row and str(row[1]) == source_hash:
            try:
                return json.loads(str(row[0]))
            except json.JSONDecodeError:
                pass
    profile = classify_game_signals(signals)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO gamer_profiles(guild_id,user_id,profile_json,source_hash,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET
                profile_json=excluded.profile_json,source_hash=excluded.source_hash,updated_at=excluded.updated_at
            """,
            (
                int(guild_id), int(user_id), json.dumps(profile, ensure_ascii=False),
                source_hash, datetime.now(UTC).isoformat(),
            ),
        )
    return profile


def delete_gamer_profile(user_id: int, guild_id: int | None = None) -> int:
    ensure_gamer_profile_storage()
    with db_connection(SOCIAL_DB) as conn:
        if guild_id is None:
            cursor = conn.execute("DELETE FROM gamer_profiles WHERE user_id=?", (int(user_id),))
        else:
            cursor = conn.execute(
                "DELETE FROM gamer_profiles WHERE guild_id=? AND user_id=?", (int(guild_id), int(user_id))
            )
    return int(cursor.rowcount)
