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
    # Habit reminders were retired because they were noisy. Keep the key for
    # API compatibility without exposing stale rows from old installations.
    return {"compatible_players": pairs[:20], "habits": []}


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


def _learning_insights(
    conn: sqlite3.Connection,
    tables: set[str],
    guild_id: int | None,
) -> dict[str, Any]:
    conversation = {
        "turns": 0,
        "ollama_turns": 0,
        "rated_turns": 0,
        "training_opt_in_users": 0,
        "approved_examples": 0,
        "target_examples": 50,
        "remaining_examples": 50,
        "fine_tune_ready": False,
    }
    if "conversation_turns" in tables:
        where = " WHERE guild_id=?" if guild_id else ""
        params: tuple[Any, ...] = (int(guild_id),) if guild_id else ()
        conversation["turns"] = int(conn.execute(
            f"SELECT COUNT(*) FROM conversation_turns{where}", params
        ).fetchone()[0])
        provider_where = " WHERE provider='ollama'"
        provider_params: tuple[Any, ...] = ()
        if guild_id:
            provider_where += " AND guild_id=?"
            provider_params = (int(guild_id),)
        conversation["ollama_turns"] = int(conn.execute(
            f"SELECT COUNT(*) FROM conversation_turns{provider_where}", provider_params
        ).fetchone()[0])
    if "conversation_feedback" in tables:
        if guild_id and "conversation_turns" in tables:
            conversation["rated_turns"] = int(conn.execute(
                """
                SELECT COUNT(DISTINCT f.bot_message_id)
                FROM conversation_feedback f
                JOIN conversation_turns t ON t.bot_message_id=f.bot_message_id
                WHERE t.guild_id=?
                """,
                (int(guild_id),),
            ).fetchone()[0])
        else:
            conversation["rated_turns"] = int(conn.execute(
                "SELECT COUNT(DISTINCT bot_message_id) FROM conversation_feedback"
            ).fetchone()[0])
    if "conversation_preferences" in tables:
        conversation["training_opt_in_users"] = int(conn.execute(
            "SELECT COUNT(*) FROM conversation_preferences WHERE training_opt_in=1"
        ).fetchone()[0])
    if {
        "conversation_turns", "conversation_feedback", "conversation_preferences"
    }.issubset(tables):
        guild_where = " AND t.guild_id=?" if guild_id else ""
        params = (int(guild_id),) if guild_id else ()
        conversation["approved_examples"] = int(conn.execute(
            f"""
            SELECT COUNT(DISTINCT t.bot_message_id)
            FROM conversation_turns t
            JOIN conversation_preferences p
              ON p.user_id=t.user_id AND p.training_opt_in=1
            JOIN conversation_feedback f
              ON f.bot_message_id=t.bot_message_id
             AND f.reviewer_user_id=t.user_id
             AND f.score=1
            WHERE t.provider='ollama'{guild_where}
            """,
            params,
        ).fetchone()[0])
    conversation["remaining_examples"] = max(
        0,
        int(conversation["target_examples"]) - int(conversation["approved_examples"]),
    )
    conversation["fine_tune_ready"] = conversation["remaining_examples"] == 0

    toxicity = {
        "shadow_samples": 0,
        "reviewed_samples": 0,
        "coverage_by_level": {str(level): 0 for level in range(4)},
        "target_samples": 500,
        "target_per_level": 50,
        "remaining_samples": 500,
        "candidate_evaluation_ready": False,
        "enforcement": "rules_only",
    }
    if "toxicity_ml_shadow" in tables:
        if guild_id:
            toxicity["shadow_samples"] = int(conn.execute(
                "SELECT COUNT(*) FROM toxicity_ml_shadow WHERE guild_id=?",
                (int(guild_id),),
            ).fetchone()[0])
        else:
            toxicity["shadow_samples"] = int(conn.execute(
                "SELECT COUNT(*) FROM toxicity_ml_shadow"
            ).fetchone()[0])
    if "toxicity_ml_feedback" in tables:
        if guild_id and "toxicity_ml_shadow" in tables:
            feedback_rows = conn.execute(
                """
                SELECT f.corrected_level,COUNT(*)
                FROM toxicity_ml_feedback f
                JOIN toxicity_ml_shadow s ON s.message_id=f.message_id
                WHERE s.guild_id=?
                GROUP BY f.corrected_level
                """,
                (int(guild_id),),
            ).fetchall()
        else:
            feedback_rows = conn.execute(
                "SELECT corrected_level,COUNT(*) FROM toxicity_ml_feedback GROUP BY corrected_level"
            ).fetchall()
        for level, count in feedback_rows:
            if 0 <= int(level) <= 3:
                toxicity["coverage_by_level"][str(int(level))] = int(count)
        toxicity["reviewed_samples"] = sum(toxicity["coverage_by_level"].values())
    toxicity["remaining_samples"] = max(
        0,
        int(toxicity["target_samples"]) - int(toxicity["reviewed_samples"]),
    )
    toxicity["candidate_evaluation_ready"] = (
        toxicity["remaining_samples"] == 0
        and all(
            count >= int(toxicity["target_per_level"])
            for count in toxicity["coverage_by_level"].values()
        )
    )
    return {"conversation": conversation, "toxicity": toxicity}


def _recommendations(insights: dict[str, Any]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    conversation = insights["learning"]["conversation"]
    toxicity = insights["learning"]["toxicity"]
    economy = insights["economy"]
    quality = insights["data_quality"]

    if not conversation["fine_tune_ready"]:
        recommendations.append({
            "level": "info",
            "area": "conversation",
            "title": "Продолжать добровольную разметку ответов Qwen",
            "detail": (
                f"До минимальной выборки не хватает {conversation['remaining_examples']} "
                "положительных ответов от согласившихся пользователей."
            ),
        })
    else:
        recommendations.append({
            "level": "ready",
            "area": "conversation",
            "title": "Можно собрать локальный QLoRA-кандидат",
            "detail": "Данные готовы только к офлайн-обучению и сравнительной оценке на приватном ПК.",
        })
    if not toxicity["candidate_evaluation_ready"]:
        recommendations.append({
            "level": "info",
            "area": "toxicity",
            "title": "Оставить токсичность в теневом режиме",
            "detail": (
                f"Размечено {toxicity['reviewed_samples']} из {toxicity['target_samples']} "
                "примеров; автоматические санкции пока запрещены."
            ),
        })
    else:
        recommendations.append({
            "level": "ready",
            "area": "toxicity",
            "title": "Можно оценить ML-кандидат против правил",
            "detail": "Готовность не включает автоматическое включение санкций; решение остаётся ручным.",
        })
    if economy["wallet_mismatches"]:
        recommendations.append({
            "level": "warning",
            "area": "economy",
            "title": "Проверить расхождения кошельков и ledger",
            "detail": (
                f"Найдено {len(economy['wallet_mismatches'])} расхождений в выборке. "
                "Автоматическое исправление отключено."
            ),
        })
    if economy["anomalies"]:
        recommendations.append({
            "level": "warning",
            "area": "economy",
            "title": "Просмотреть нетипичные операции экономики",
            "detail": f"Найдено {len(economy['anomalies'])} статистических выбросов без автосанкций.",
        })
    if not quality["healthy"]:
        recommendations.append({
            "level": "warning",
            "area": "data_quality",
            "title": "Исправить связи данных перед обучением",
            "detail": "Есть orphan-записи; сначала нужен отдельный безопасный repair-аудит.",
        })
    if not recommendations:
        recommendations.append({
            "level": "ok",
            "area": "data_quality",
            "title": "Критичных рекомендаций нет",
            "detail": "Наблюдение продолжается; изменения данных автоматически не выполняются.",
        })
    return recommendations


def build_ml_insights(*, database: str = SOCIAL_DB, guild_id: int | None = None) -> dict[str, Any]:
    with db_connection(database) as conn:
        tables = _tables(conn)
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "advisory",
            "economy": _economy_insights(conn, tables),
            "activity": _activity_insights(conn, tables, guild_id),
            "data_quality": _quality_insights(conn, tables),
            "learning": _learning_insights(conn, tables, guild_id),
        }
        result["recommendations"] = _recommendations(result)
        return result
