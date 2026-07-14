from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from core.db import connection as db_connection
from core.paths import SOCIAL_DB
from core.settings_store import has_feature_setting, is_feature_enabled, set_feature_enabled


FEATURE_REP_ROLES = "rep_roles"
UTC = timezone.utc
_INITIALIZED_DATABASES: set[str] = set()


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def ensure_rep_roles_storage() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with db_connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rep_role_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                min_rep INTEGER NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_rrt_guild_rep
                ON rep_role_thresholds(guild_id, min_rep);
            CREATE TABLE IF NOT EXISTS rep_roles_active (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                threshold INTEGER NOT NULL,
                permanent INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, guild_id)
            );
            """
        )
    migrate_legacy_rep_roles_settings()
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def migrate_legacy_rep_roles_settings() -> int:
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT guild_id, enabled FROM rep_roles_config ORDER BY guild_id"
        ).fetchall() if _table_exists(conn, "rep_roles_config") else []
    for guild_id, enabled in rows:
        guild_id = int(guild_id)
        if not has_feature_setting(guild_id, FEATURE_REP_ROLES):
            set_feature_enabled(guild_id, FEATURE_REP_ROLES, bool(enabled))
        if is_feature_enabled(guild_id, FEATURE_REP_ROLES) != bool(enabled):
            raise RuntimeError(f"rep roles migration mismatch for guild {guild_id}")
    _archive_legacy_config(len(rows))
    return len(rows)


def _archive_legacy_config(expected_rows: int) -> None:
    table, backup = "rep_roles_config", "rep_roles_config_legacy_backup"
    with db_connection(SOCIAL_DB) as conn:
        if not _table_exists(conn, table):
            return
        rows = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        if rows != expected_rows:
            raise RuntimeError("rep roles config changed during migration")
        if _table_exists(conn, backup):
            if rows:
                raise RuntimeError("rep roles backup already exists for non-empty config")
            conn.execute(f'DROP TABLE "{table}"')
            return
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{backup}"')
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_migration_archive(
                table_name TEXT PRIMARY KEY, backup_table TEXT NOT NULL,
                source_rows INTEGER NOT NULL, archived_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings_migration_archive VALUES(?,?,?,?)",
            (table, backup, rows, datetime.now(UTC).isoformat()),
        )


def rep_roles_enabled(guild_id: int) -> bool:
    return is_feature_enabled(guild_id, FEATURE_REP_ROLES, default=True)


def set_rep_roles_enabled(guild_id: int, enabled: bool) -> None:
    set_feature_enabled(guild_id, FEATURE_REP_ROLES, enabled)


def best_threshold(guild_id: int, reputation: int) -> tuple[int, str] | None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT min_rep, label FROM rep_role_thresholds
            WHERE guild_id=? AND min_rep<=? ORDER BY min_rep DESC LIMIT 1
            """,
            (int(guild_id), int(reputation)),
        ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def list_thresholds(guild_id: int) -> list[tuple[int, int, str]]:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT id, min_rep, label FROM rep_role_thresholds WHERE guild_id=? ORDER BY min_rep",
            (int(guild_id),),
        ).fetchall()
    return [(int(row[0]), int(row[1]), str(row[2])) for row in rows]


def upsert_threshold(guild_id: int, min_rep: int, label: str) -> None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO rep_role_thresholds(guild_id,min_rep,label,created_at) VALUES(?,?,?,?)
            ON CONFLICT(guild_id,min_rep) DO UPDATE SET label=excluded.label
            """,
            (int(guild_id), int(min_rep), label.strip()[:50], datetime.now(UTC).isoformat()),
        )


def get_threshold(guild_id: int, threshold_id: int) -> tuple[int, str] | None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT min_rep, label FROM rep_role_thresholds WHERE id=? AND guild_id=?",
            (int(threshold_id), int(guild_id)),
        ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def delete_threshold(guild_id: int, threshold_id: int) -> tuple[int, str] | None:
    row = get_threshold(guild_id, threshold_id)
    if not row:
        return None
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            "DELETE FROM rep_role_thresholds WHERE id=? AND guild_id=?",
            (int(threshold_id), int(guild_id)),
        )
    return row


def update_threshold(guild_id: int, threshold_id: int, min_rep: int, label: str) -> bool:
    ensure_rep_roles_storage()
    try:
        with db_connection(SOCIAL_DB) as conn:
            updated = conn.execute(
                "UPDATE rep_role_thresholds SET min_rep=?, label=? WHERE id=? AND guild_id=?",
                (int(min_rep), label.strip()[:50], int(threshold_id), int(guild_id)),
            ).rowcount
        return bool(updated)
    except sqlite3.IntegrityError:
        return False


def get_active_role(user_id: int, guild_id: int) -> tuple[int, int, bool, str | None] | None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT role_id, threshold, permanent, expires_at FROM rep_roles_active
            WHERE user_id=? AND guild_id=?
            """,
            (int(user_id), int(guild_id)),
        ).fetchone()
    return (int(row[0]), int(row[1]), bool(row[2]), row[3]) if row else None


def save_active_role(
    user_id: int, guild_id: int, role_id: int, threshold: int, expires_at: datetime
) -> None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO rep_roles_active(user_id,guild_id,role_id,threshold,permanent,expires_at,created_at)
            VALUES(?,?,?,?,0,?,?)
            ON CONFLICT(user_id,guild_id) DO UPDATE SET
                role_id=excluded.role_id, threshold=excluded.threshold,
                permanent=0, expires_at=excluded.expires_at
            """,
            (int(user_id), int(guild_id), int(role_id), int(threshold), expires_at.isoformat(), datetime.now(UTC).isoformat()),
        )


def extend_active_role(user_id: int, guild_id: int, expires_at: datetime) -> None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        conn.execute(
            "UPDATE rep_roles_active SET expires_at=? WHERE user_id=? AND guild_id=?",
            (expires_at.isoformat(), int(user_id), int(guild_id)),
        )


def make_role_permanent(user_id: int, guild_id: int) -> tuple[int, bool] | None:
    active = get_active_role(user_id, guild_id)
    if not active:
        return None
    if not active[2]:
        with db_connection(SOCIAL_DB) as conn:
            conn.execute(
                "UPDATE rep_roles_active SET permanent=1, expires_at=NULL WHERE user_id=? AND guild_id=?",
                (int(user_id), int(guild_id)),
            )
    return active[0], active[2]


def list_expiring_roles() -> list[tuple[int, int, int, str | None]]:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT user_id, guild_id, role_id, expires_at FROM rep_roles_active WHERE permanent=0"
        ).fetchall()
    return [(int(row[0]), int(row[1]), int(row[2]), row[3]) for row in rows]


def next_threshold(guild_id: int, reputation: int) -> tuple[int, str] | None:
    ensure_rep_roles_storage()
    with db_connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT min_rep, label FROM rep_role_thresholds
            WHERE guild_id=? AND min_rep>? ORDER BY min_rep LIMIT 1
            """,
            (int(guild_id), int(reputation)),
        ).fetchone()
    return (int(row[0]), str(row[1])) if row else None
