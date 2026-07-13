from __future__ import annotations

import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB
from core.db import connection as db_connection


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _economy_insights(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "coin_ledger" not in tables:
        return {"anomalies": [], "wallet_mismatches": []}
    rows = conn.execute(
        "SELECT id,user_id,delta,reason,created_at FROM coin_ledger ORDER BY id DESC LIMIT 5000"
    ).fetchall()
    by_reason: dict[str, list[int]] = defaultdict(list)
    for _, _, delta, reason, _ in rows:
        by_reason[str(reason)].append(abs(int(delta)))
    thresholds = {}
    for reason, values in by_reason.items():
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        thresholds[reason] = max(100.0, median + max(25.0, 6.0 * mad))
    anomalies = [
        {"ledger_id": int(row_id), "user_id": int(user_id), "delta": int(delta), "reason": str(reason), "created_at": str(created_at)}
        for row_id, user_id, delta, reason, created_at in rows
        if abs(int(delta)) >= thresholds[str(reason)] and len(by_reason[str(reason)]) >= 3
    ][:20]
    mismatches = []
    if "coins_wallet" in tables:
        mismatches = [
            {"user_id": int(user_id), "wallet": int(wallet), "ledger": int(ledger)}
            for user_id, wallet, ledger in conn.execute(
                """
                SELECT w.user_id,w.balance,COALESCE(SUM(l.delta),0)
                FROM coins_wallet w LEFT JOIN coin_ledger l ON l.user_id=w.user_id
                GROUP BY w.user_id,w.balance HAVING w.balance != COALESCE(SUM(l.delta),0)
                LIMIT 20
                """
            )
        ]
    return {"anomalies": anomalies, "wallet_mismatches": mismatches}


def _activity_insights(conn: sqlite3.Connection, tables: set[str], guild_id: int | None) -> dict[str, Any]:
    if "activity_sessions" not in tables:
        return {"compatible_players": [], "habits": []}
    params: tuple[Any, ...] = ()
    where = "activity_type='game' AND seconds>0"
    if guild_id:
        where += " AND guild_id=?"
        params = (int(guild_id),)
    rows = conn.execute(
        f"SELECT user_id,activity_name,SUM(seconds) FROM activity_sessions WHERE {where} GROUP BY user_id,activity_name",
        params,
    ).fetchall()
    vectors: dict[int, dict[str, float]] = defaultdict(dict)
    for user_id, game, seconds in rows:
        vectors[int(user_id)][str(game)] = math.log1p(max(0, int(seconds)))
    pairs = []
    user_ids = sorted(vectors)[:250]
    for index, first in enumerate(user_ids):
        first_vector = vectors[first]
        first_norm = math.sqrt(sum(value * value for value in first_vector.values()))
        for second in user_ids[index + 1:]:
            second_vector = vectors[second]
            shared = set(first_vector) & set(second_vector)
            if not shared:
                continue
            second_norm = math.sqrt(sum(value * value for value in second_vector.values()))
            score = sum(first_vector[game] * second_vector[game] for game in shared) / (first_norm * second_norm or 1.0)
            pairs.append({
                "user_a": first,
                "user_b": second,
                "score": round(score, 4),
                "shared_games": sorted(shared, key=lambda game: first_vector[game] + second_vector[game], reverse=True)[:5],
            })
    pairs.sort(key=lambda item: item["score"], reverse=True)
    habits = []
    if "activity_game_habits" in tables:
        habit_params: tuple[Any, ...] = ()
        habit_where = ""
        if guild_id:
            habit_where = "WHERE guild_id=?"
            habit_params = (int(guild_id),)
        habits = [
            {"user_id": int(user_id), "activity": str(activity), "expected_minute_msk": int(minute), "sample_days": int(days)}
            for user_id, activity, minute, days in conn.execute(
                f"SELECT user_id,activity_name,expected_minute,sample_days FROM activity_game_habits {habit_where} ORDER BY sample_days DESC LIMIT 30",
                habit_params,
            )
        ]
    return {"compatible_players": pairs[:20], "habits": habits}


def _quality_insights(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    checks: dict[str, int] = {}
    if "steam_owned_games_cache" in tables and "steam_profiles" in tables:
        checks["orphan_steam_games"] = int(conn.execute(
            "SELECT COUNT(*) FROM steam_owned_games_cache g LEFT JOIN steam_profiles p ON p.user_id=g.user_id WHERE p.user_id IS NULL"
        ).fetchone()[0])
    if "web_sessions" in tables and "web_users" in tables:
        checks["orphan_web_sessions"] = int(conn.execute(
            "SELECT COUNT(*) FROM web_sessions s LEFT JOIN web_users u ON u.discord_user_id=s.discord_user_id WHERE u.discord_user_id IS NULL"
        ).fetchone()[0])
    if "toxicity_ml_feedback" in tables:
        checks["reviewed_toxicity_examples"] = int(conn.execute("SELECT COUNT(*) FROM toxicity_ml_feedback").fetchone()[0])
    return {"checks": checks, "healthy": all(value == 0 for key, value in checks.items() if key.startswith("orphan_"))}


def build_ml_insights(*, database: str = SOCIAL_DB, guild_id: int | None = None) -> dict[str, Any]:
    with db_connection(database) as conn:
        tables = _tables(conn)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "advisory",
            "economy": _economy_insights(conn, tables),
            "activity": _activity_insights(conn, tables, guild_id),
            "data_quality": _quality_insights(conn, tables),
        }
