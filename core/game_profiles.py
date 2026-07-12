# core/game_profiles.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB
from core.db import connection as db_connection

GAME_LOL = "lol"
PROVIDER_RIOT = "riot"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_game_profile_tables():
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS game_accounts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_user_id INTEGER NOT NULL,
                game            TEXT NOT NULL,
                provider        TEXT NOT NULL,
                region          TEXT NOT NULL DEFAULT '',
                external_id     TEXT NOT NULL,
                display_name    TEXT NOT NULL DEFAULT '',
                verified        INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                UNIQUE(discord_user_id, game, provider, external_id)
            );

            CREATE TABLE IF NOT EXISTS lol_profile_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_user_id INTEGER NOT NULL,
                puuid           TEXT NOT NULL,
                region          TEXT NOT NULL DEFAULT '',
                snapshot_json   TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lol_match_features (
                match_id                  TEXT NOT NULL,
                puuid                     TEXT NOT NULL,
                queue_id                  INTEGER NOT NULL DEFAULT 0,
                champion_name             TEXT NOT NULL DEFAULT '',
                team_position             TEXT NOT NULL DEFAULT '',
                win                       INTEGER NOT NULL DEFAULT 0,
                kills                     INTEGER NOT NULL DEFAULT 0,
                deaths                    INTEGER NOT NULL DEFAULT 0,
                assists                   INTEGER NOT NULL DEFAULT 0,
                cs_per_min                REAL NOT NULL DEFAULT 0,
                gold_per_min              REAL NOT NULL DEFAULT 0,
                vision_score              INTEGER NOT NULL DEFAULT 0,
                damage_share              REAL NOT NULL DEFAULT 0,
                kill_participation        REAL NOT NULL DEFAULT 0,
                created_at                TEXT NOT NULL,
                PRIMARY KEY (match_id, puuid)
            );

            CREATE TABLE IF NOT EXISTS player_model_profiles (
                discord_user_id INTEGER NOT NULL,
                game            TEXT NOT NULL,
                model_version   TEXT NOT NULL,
                features_json   TEXT NOT NULL,
                labels_json     TEXT NOT NULL,
                explanation     TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL,
                PRIMARY KEY (discord_user_id, game, model_version)
            );
            """
        )
        conn.commit()


def upsert_game_account(
    discord_user_id: int,
    game: str,
    provider: str,
    external_id: str,
    display_name: str,
    region: str = "",
    verified: bool = True,
):
    ensure_game_profile_tables()
    now = _now()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO game_accounts(
                discord_user_id, game, provider, region, external_id,
                display_name, verified, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id, game, provider, external_id) DO UPDATE SET
                region=excluded.region,
                display_name=excluded.display_name,
                verified=excluded.verified,
                updated_at=excluded.updated_at
            """,
            (
                int(discord_user_id),
                game,
                provider,
                region,
                external_id,
                display_name,
                int(verified),
                now,
                now,
            ),
        )
        conn.commit()


def get_game_account(discord_user_id: int, game: str, provider: str = PROVIDER_RIOT) -> dict[str, Any] | None:
    ensure_game_profile_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT discord_user_id, game, provider, region, external_id,
                   display_name, verified, created_at, updated_at
            FROM game_accounts
            WHERE discord_user_id=? AND game=? AND provider=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (int(discord_user_id), game, provider),
        ).fetchone()
    if not row:
        return None
    return {
        "discord_user_id": row[0],
        "game": row[1],
        "provider": row[2],
        "region": row[3],
        "external_id": row[4],
        "display_name": row[5],
        "verified": bool(row[6]),
        "created_at": row[7],
        "updated_at": row[8],
    }


def unlink_game_account(discord_user_id: int, game: str, provider: str = PROVIDER_RIOT) -> int:
    ensure_game_profile_tables()
    with db_connection(SOCIAL_DB) as conn:
        cur = conn.execute(
            "DELETE FROM game_accounts WHERE discord_user_id=? AND game=? AND provider=?",
            (int(discord_user_id), game, provider),
        )
        conn.commit()
        return cur.rowcount


def save_lol_snapshot(discord_user_id: int, puuid: str, region: str, snapshot: dict[str, Any]):
    ensure_game_profile_tables()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO lol_profile_snapshots(discord_user_id, puuid, region, snapshot_json, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (int(discord_user_id), puuid, region, json.dumps(snapshot, ensure_ascii=False), _now()),
        )
        conn.commit()


def get_latest_lol_snapshot(discord_user_id: int) -> dict[str, Any] | None:
    ensure_game_profile_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT snapshot_json FROM lol_profile_snapshots
            WHERE discord_user_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (int(discord_user_id),),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def save_lol_match_features(puuid: str, features: list[dict[str, Any]]):
    ensure_game_profile_tables()
    now = _now()
    rows = []
    for item in features:
        rows.append(
            (
                item.get("match_id", ""),
                puuid,
                int(item.get("queue_id") or 0),
                item.get("champion_name", ""),
                item.get("team_position", ""),
                int(bool(item.get("win"))),
                int(item.get("kills") or 0),
                int(item.get("deaths") or 0),
                int(item.get("assists") or 0),
                float(item.get("cs_per_min") or 0),
                float(item.get("gold_per_min") or 0),
                int(item.get("vision_score") or 0),
                float(item.get("damage_share") or 0),
                float(item.get("kill_participation") or 0),
                now,
            )
        )
    with db_connection(SOCIAL_DB) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO lol_match_features(
                match_id, puuid, queue_id, champion_name, team_position, win,
                kills, deaths, assists, cs_per_min, gold_per_min, vision_score,
                damage_share, kill_participation, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def save_player_model_profile(
    discord_user_id: int,
    game: str,
    model_version: str,
    features: dict[str, Any],
    labels: dict[str, Any],
    explanation: str,
):
    ensure_game_profile_tables()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO player_model_profiles(
                discord_user_id, game, model_version, features_json,
                labels_json, explanation, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id, game, model_version) DO UPDATE SET
                features_json=excluded.features_json,
                labels_json=excluded.labels_json,
                explanation=excluded.explanation,
                updated_at=excluded.updated_at
            """,
            (
                int(discord_user_id),
                game,
                model_version,
                json.dumps(features, ensure_ascii=False),
                json.dumps(labels, ensure_ascii=False),
                explanation,
                _now(),
            ),
        )
        conn.commit()


def get_player_model_profile(discord_user_id: int, game: str, model_version: str = "lol_rules_v1") -> dict[str, Any] | None:
    ensure_game_profile_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT features_json, labels_json, explanation, updated_at
            FROM player_model_profiles
            WHERE discord_user_id=? AND game=? AND model_version=?
            """,
            (int(discord_user_id), game, model_version),
        ).fetchone()
    if not row:
        return None
    return {
        "features": json.loads(row[0]),
        "labels": json.loads(row[1]),
        "explanation": row[2],
        "updated_at": row[3],
    }
