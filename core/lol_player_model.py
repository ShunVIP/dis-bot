# core/lol_player_model.py
from __future__ import annotations

from collections import Counter
from statistics import mean, pstdev
from typing import Any


def extract_lol_match_features(match: dict[str, Any], puuid: str) -> dict[str, Any] | None:
    info = match.get("info") or {}
    metadata = match.get("metadata") or {}
    participants = info.get("participants") or []
    participant = next((p for p in participants if p.get("puuid") == puuid), None)
    if not participant:
        return None

    duration = max(float(info.get("gameDuration") or 0), 1.0)
    minutes = duration / 60.0
    team_id = participant.get("teamId")
    team = [p for p in participants if p.get("teamId") == team_id]
    team_kills = sum(int(p.get("kills") or 0) for p in team) or 1
    team_damage = sum(int(p.get("totalDamageDealtToChampions") or 0) for p in team) or 1
    cs = int(participant.get("totalMinionsKilled") or 0) + int(participant.get("neutralMinionsKilled") or 0)

    kills = int(participant.get("kills") or 0)
    assists = int(participant.get("assists") or 0)
    deaths = int(participant.get("deaths") or 0)

    return {
        "match_id": metadata.get("matchId") or "",
        "queue_id": int(info.get("queueId") or 0),
        "champion_name": participant.get("championName") or "",
        "team_position": participant.get("teamPosition") or "",
        "win": bool(participant.get("win")),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "cs_per_min": round(cs / minutes, 2),
        "gold_per_min": round(float(participant.get("goldEarned") or 0) / minutes, 2),
        "vision_score": int(participant.get("visionScore") or 0),
        "damage_share": round(float(participant.get("totalDamageDealtToChampions") or 0) / team_damage, 3),
        "kill_participation": round((kills + assists) / team_kills, 3),
    }


def classify_lol_player(features: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not features:
        empty = {
            "matches": 0,
            "winrate": 0,
            "avg_kda": 0,
            "avg_deaths": 0,
            "avg_cs_per_min": 0,
            "avg_vision_score": 0,
            "avg_damage_share": 0,
            "avg_kill_participation": 0,
            "role_main": "unknown",
            "champion_pool": 0,
        }
        return empty, {"primary": "нет данных", "secondary": "нужно больше матчей"}, "Недостаточно матчей для анализа."

    wins = [1 if item.get("win") else 0 for item in features]
    kills = [float(item.get("kills") or 0) for item in features]
    deaths = [float(item.get("deaths") or 0) for item in features]
    assists = [float(item.get("assists") or 0) for item in features]
    kda_values = [(k + a) / max(d, 1) for k, d, a in zip(kills, deaths, assists)]
    roles = [str(item.get("team_position") or "UNKNOWN") for item in features]
    champs = [str(item.get("champion_name") or "unknown") for item in features]

    result = {
        "matches": len(features),
        "winrate": round(mean(wins) * 100, 1),
        "avg_kda": round(mean(kda_values), 2),
        "avg_deaths": round(mean(deaths), 2),
        "death_variance": round(pstdev(deaths), 2) if len(deaths) > 1 else 0,
        "avg_cs_per_min": round(mean(float(x.get("cs_per_min") or 0) for x in features), 2),
        "avg_gold_per_min": round(mean(float(x.get("gold_per_min") or 0) for x in features), 2),
        "avg_vision_score": round(mean(float(x.get("vision_score") or 0) for x in features), 2),
        "avg_damage_share": round(mean(float(x.get("damage_share") or 0) for x in features), 3),
        "avg_kill_participation": round(mean(float(x.get("kill_participation") or 0) for x in features), 3),
        "role_main": Counter(roles).most_common(1)[0][0],
        "champion_pool": len(set(champs)),
        "top_champions": [name for name, _ in Counter(champs).most_common(5)],
    }

    labels = []
    if result["avg_damage_share"] >= 0.30 or mean(kills) >= 7:
        labels.append("агрессивный керри")
    if result["avg_cs_per_min"] >= 7.0 and result["avg_deaths"] <= 5:
        labels.append("стабильный фармер")
    if result["avg_kill_participation"] >= 0.62:
        labels.append("командный плеймейкер")
    if result["avg_vision_score"] >= 28:
        labels.append("контроль карты")
    if result["avg_deaths"] >= 7 or result["death_variance"] >= 3:
        labels.append("рискованный/нестабильный")
    if result["champion_pool"] >= min(8, result["matches"]):
        labels.append("гибкий пул")
    if result["winrate"] >= 58:
        labels.append("эффективный победитель")

    if not labels:
        labels.append("сбалансированный игрок")

    primary = labels[0]
    secondary = labels[1] if len(labels) > 1 else "без яркого второго типа"
    explanation = (
        f"{result['matches']} матчей: winrate {result['winrate']}%, "
        f"KDA {result['avg_kda']}, deaths {result['avg_deaths']}, "
        f"CS/min {result['avg_cs_per_min']}, vision {result['avg_vision_score']}, "
        f"KP {round(result['avg_kill_participation'] * 100, 1)}%."
    )

    return result, {"primary": primary, "secondary": secondary, "all": labels}, explanation
