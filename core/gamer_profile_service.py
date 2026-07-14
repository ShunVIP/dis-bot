from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


ARCHETYPES = {
    "mmo": ("MMO", ("world of warcraft", "wow", "final fantasy xiv", "ffxiv", "guild wars", "lineage", "revelation online", "black desert", "lost ark", "new world", "elder scrolls online", "albion", "throne and liberty")),
    "souls": ("Souls-like", ("dark souls", "elden ring", "sekiro", "bloodborne", "lies of p", "nioh", "soulslike", "souls-like", "lords of the fallen")),
    "shooter": ("Шутеры", ("counter-strike", "counter strike", "cs2", "valorant", "call of duty", "battlefield", "apex legends", "overwatch", "destiny", "tarkov", "pubg", "rainbow six", "helldivers", "warframe")),
    "moba": ("MOBA", ("league of legends", "dota", "smite", "heroes of the storm", "mobile legends", "deadlock")),
    "strategy": ("Стратегии", ("heroes of might", "homm", "civilization", "stellaris", "total war", "age of empires", "crusader kings", "europa universalis", "starcraft", "warcraft iii", "old world")),
    "rpg": ("RPG", ("witcher", "cyberpunk", "skyrim", "fallout", "pathfinder", "baldur", "dragon age", "mass effect", "divinity", "persona", "kingdom come")),
    "survival": ("Survival", ("rust", "valheim", "project zomboid", "ark survival", "the forest", "sons of the forest", "dayz", "7 days to die", "palworld")),
    "sandbox": ("Песочницы", ("minecraft", "terraria", "factorio", "satisfactory", "no man's sky", "stardew valley", "rimworld", "kenshi")),
    "fighting": ("Файтинги", ("tekken", "street fighter", "mortal kombat", "guilty gear", "brawlhalla", "super smash")),
    "racing": ("Гонки", ("forza", "need for speed", "assetto corsa", "dirt rally", "f1 ", "gran turismo", "the crew")),
}

TAG_ALIASES = {
    "ммo": "mmo", "ммо": "mmo", "soulslike": "souls", "souls-like": "souls",
    "соулс": "souls", "шутер": "shooter", "шутеры": "shooter", "моба": "moba",
    "стратегии": "strategy", "стратегия": "strategy", "рпг": "rpg",
    "выживание": "survival", "песочница": "sandbox", "файтинг": "fighting", "гонки": "racing",
}


def normalize_game_name(value: str) -> str:
    text = str(value).lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^a-zа-я0-9'&+ -]", " ", text)).strip()


def normalize_requested_tags(value: str | Iterable[str]) -> list[str]:
    chunks = re.split(r"[,;/\s]+", value) if isinstance(value, str) else list(value)
    result = []
    for raw in chunks:
        tag = normalize_game_name(str(raw)).replace(" ", "_")
        tag = TAG_ALIASES.get(tag, tag)
        if tag in ARCHETYPES and tag not in result:
            result.append(tag)
    return result


def classify_game_signals(signals: Iterable[tuple[str, int]]) -> dict[str, object]:
    scores: dict[str, float] = defaultdict(float)
    evidence: dict[str, list[str]] = defaultdict(list)
    games: dict[str, int] = defaultdict(int)
    display_names: dict[str, str] = {}
    for raw_name, raw_seconds in signals:
        name = str(raw_name).strip()
        normalized = normalize_game_name(name)
        if not normalized:
            continue
        seconds = max(60, int(raw_seconds or 0))
        games[normalized] = max(games[normalized], seconds)
        display_names[normalized] = name[:120]
        hours_weight = max(1.0, min(seconds / 3600.0, 100.0) ** 0.5)
        for tag, (_, keywords) in ARCHETYPES.items():
            if any(keyword in normalized for keyword in keywords):
                scores[tag] += hours_weight
                if name not in evidence[tag] and len(evidence[tag]) < 5:
                    evidence[tag].append(name[:120])
    ranked = sorted(scores, key=lambda tag: (-scores[tag], tag))
    top_games = sorted(games, key=lambda game: (-games[game], game))[:10]
    return {
        "archetypes": [
            {
                "tag": tag,
                "label": ARCHETYPES[tag][0],
                "score": round(scores[tag], 3),
                "evidence": evidence[tag],
            }
            for tag in ranked
        ],
        "top_games": [
            {"name": display_names[game], "hours": round(games[game] / 3600.0, 1)}
            for game in top_games
        ],
    }


def build_gamer_context(profile: dict[str, object], explicit_tags: Iterable[str] = ()) -> str:
    inferred = [str(item.get("label")) for item in profile.get("archetypes", [])[:4] if isinstance(item, dict)]
    explicit = [ARCHETYPES[tag][0] for tag in normalize_requested_tags(explicit_tags)]
    tags = list(dict.fromkeys(explicit + inferred))
    games = [str(item.get("name")) for item in profile.get("top_games", [])[:5] if isinstance(item, dict)]
    parts = []
    if tags:
        parts.append("игровые интересы: " + ", ".join(tags))
    if games:
        parts.append("часто играет: " + ", ".join(games))
    return "; ".join(parts)[:700]
