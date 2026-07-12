# core/settings_store.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.paths import SOCIAL_DB
from core.db import connection as db_connection


@dataclass(frozen=True)
class FeatureChannelPolicy:
    feature: str
    enabled: bool = True
    output_channel_id: int | None = None
    allowed_channel_ids: tuple[int, ...] = ()
    excluded_channel_ids: tuple[int, ...] = ()
    extra: dict[str, Any] | None = None


def ensure_settings_tables():
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_settings (
                guild_id    INTEGER NOT NULL,
                feature     TEXT NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                payload     TEXT NOT NULL DEFAULT '{}',
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (guild_id, feature)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_channels (
                guild_id    INTEGER NOT NULL,
                feature     TEXT NOT NULL,
                channel_id  INTEGER NOT NULL,
                mode        TEXT NOT NULL,
                reason      TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (guild_id, feature, channel_id, mode)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_runtime_state (
                guild_id    INTEGER NOT NULL,
                feature     TEXT NOT NULL,
                state       TEXT NOT NULL DEFAULT '{}',
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (guild_id, feature)
            )
            """
        )
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_feature_payload(guild_id: int, feature: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT payload FROM feature_settings WHERE guild_id=? AND feature=?",
            (guild_id, feature),
        ).fetchone()
    if not row:
        return dict(default or {})
    merged = dict(default or {})
    merged.update(_loads(row[0]))
    return merged


def set_feature_payload(guild_id: int, feature: str, payload: dict[str, Any], enabled: bool | None = None):
    ensure_settings_tables()
    existing = get_feature_payload(guild_id, feature)
    existing.update(payload)
    enabled_value = 1 if enabled is None else int(enabled)
    with db_connection(SOCIAL_DB) as conn:
        if enabled is None:
            row = conn.execute(
                "SELECT enabled FROM feature_settings WHERE guild_id=? AND feature=?",
                (guild_id, feature),
            ).fetchone()
            enabled_value = int(row[0]) if row else 1
        conn.execute(
            """
            INSERT INTO feature_settings(guild_id, feature, enabled, payload, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, feature) DO UPDATE SET
                enabled=excluded.enabled,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (guild_id, feature, enabled_value, json.dumps(existing, ensure_ascii=False), _now()),
        )
        conn.commit()


def set_feature_enabled(guild_id: int, feature: str, enabled: bool):
    set_feature_payload(guild_id, feature, {}, enabled=enabled)


def get_feature_runtime_state(
    guild_id: int,
    feature: str,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT state FROM feature_runtime_state WHERE guild_id=? AND feature=?",
            (guild_id, feature),
        ).fetchone()
    result = dict(default or {})
    if row:
        result.update(_loads(row[0]))
    return result


def set_feature_runtime_state(guild_id: int, feature: str, state: dict[str, Any]) -> None:
    ensure_settings_tables()
    existing = get_feature_runtime_state(guild_id, feature)
    existing.update(state)
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO feature_runtime_state(guild_id, feature, state, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(guild_id, feature) DO UPDATE SET
                state=excluded.state,
                updated_at=excluded.updated_at
            """,
            (guild_id, feature, json.dumps(existing, ensure_ascii=False), _now()),
        )


def is_feature_enabled(guild_id: int, feature: str, default: bool = True) -> bool:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT enabled FROM feature_settings WHERE guild_id=? AND feature=?",
            (guild_id, feature),
        ).fetchone()
    return default if not row else bool(row[0])


def has_feature_setting(guild_id: int, feature: str) -> bool:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM feature_settings WHERE guild_id=? AND feature=?",
            (guild_id, feature),
        ).fetchone()
    return bool(row)


def set_feature_channel(
    guild_id: int,
    feature: str,
    channel_id: int,
    mode: str,
    reason: str = "",
):
    if mode not in {"output", "allow", "exclude"}:
        raise ValueError("mode must be output, allow or exclude")
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO feature_settings(guild_id, feature, enabled, payload, updated_at)
            VALUES(?, ?, 1, '{}', ?)
            """,
            (guild_id, feature, _now()),
        )
        if mode == "output":
            conn.execute(
                "DELETE FROM feature_channels WHERE guild_id=? AND feature=? AND mode='output'",
                (guild_id, feature),
            )
        conn.execute(
            """
            INSERT INTO feature_channels(guild_id, feature, channel_id, mode, reason, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, feature, channel_id, mode) DO UPDATE SET
                reason=excluded.reason,
                updated_at=excluded.updated_at
            """,
            (guild_id, feature, channel_id, mode, reason[:200], _now()),
        )
        conn.commit()


def clear_feature_channel(guild_id: int, feature: str, channel_id: int, mode: str) -> int:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        cur = conn.execute(
            "DELETE FROM feature_channels WHERE guild_id=? AND feature=? AND channel_id=? AND mode=?",
            (guild_id, feature, channel_id, mode),
        )
        conn.commit()
        return cur.rowcount


def clear_feature_channels(guild_id: int, feature: str, mode: str) -> int:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        cur = conn.execute(
            "DELETE FROM feature_channels WHERE guild_id=? AND feature=? AND mode=?",
            (guild_id, feature, mode),
        )
        conn.commit()
        return cur.rowcount


def get_feature_channel_ids(guild_id: int, feature: str, mode: str) -> set[int]:
    ensure_settings_tables()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT channel_id FROM feature_channels WHERE guild_id=? AND feature=? AND mode=?",
            (guild_id, feature, mode),
        ).fetchall()
    return {int(row[0]) for row in rows}


def get_feature_policy(guild_id: int, feature: str) -> FeatureChannelPolicy:
    payload = get_feature_payload(guild_id, feature)
    output_ids = get_feature_channel_ids(guild_id, feature, "output")
    return FeatureChannelPolicy(
        feature=feature,
        enabled=is_feature_enabled(guild_id, feature),
        output_channel_id=next(iter(output_ids), None),
        allowed_channel_ids=tuple(sorted(get_feature_channel_ids(guild_id, feature, "allow"))),
        excluded_channel_ids=tuple(sorted(get_feature_channel_ids(guild_id, feature, "exclude"))),
        extra=payload,
    )


def is_channel_allowed(guild_id: int, feature: str, channel_id: int) -> bool:
    policy = get_feature_policy(guild_id, feature)
    if not policy.enabled:
        return False
    if channel_id in policy.excluded_channel_ids:
        return False
    if policy.allowed_channel_ids and channel_id not in policy.allowed_channel_ids:
        return False
    return True
