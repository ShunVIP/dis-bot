from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from core.db import connection
from core.paths import SOCIAL_DB


UTC = timezone.utc
_INITIALIZED_DATABASES: set[str] = set()


def _column_names(conn, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def ensure_tables() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_rewards (
                user_id INTEGER PRIMARY KEY,
                last_claim_msk TEXT NOT NULL,
                streak INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS role_shop (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER NOT NULL UNIQUE,
                role_name TEXT NOT NULL,
                price INTEGER NOT NULL,
                duration_h INTEGER NOT NULL DEFAULT 0,
                added_by INTEGER NOT NULL,
                added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS temp_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                guild_id INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_temp_roles_expiry ON temp_roles(expires_at);
            """
        )
        if "guild_id" not in _column_names(conn, "temp_roles"):
            conn.execute("ALTER TABLE temp_roles ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0")
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def advance_daily_streak(user_id: int, today: date) -> dict[str, Any]:
    """Atomically claim one calendar day and return the new streak."""
    ensure_tables()
    today_text = today.isoformat()
    with connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT last_claim_msk, streak FROM daily_rewards WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        if row:
            try:
                last_date = date.fromisoformat(str(row[0]))
            except ValueError:
                last_date = date.min
            if last_date == today:
                return {"claimed": False, "streak": int(row[1]), "last_claim": str(row[0])}
            streak = int(row[1]) + 1 if (today - last_date).days == 1 else 1
        else:
            streak = 1
        conn.execute(
            """
            INSERT INTO daily_rewards(user_id, last_claim_msk, streak) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_claim_msk=excluded.last_claim_msk,
                streak=excluded.streak
            """,
            (int(user_id), today_text, streak),
        )
    return {"claimed": True, "streak": streak, "last_claim": today_text}


def list_daily_streaks(limit: int = 100) -> list[tuple[int, int]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT user_id, streak FROM daily_rewards ORDER BY streak DESC, user_id ASC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [(int(user_id), int(streak)) for user_id, streak in rows]


def list_shop_items() -> list[dict[str, Any]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT id, role_id, role_name, price, duration_h FROM role_shop ORDER BY price ASC, id ASC"
        ).fetchall()
    return [
        {
            "id": int(row[0]),
            "role_id": int(row[1]),
            "role_name": str(row[2]),
            "price": int(row[3]),
            "duration_h": int(row[4]),
        }
        for row in rows
    ]


def get_shop_item(shop_id: int) -> dict[str, Any] | None:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT id, role_id, role_name, price, duration_h FROM role_shop WHERE id=?",
            (int(shop_id),),
        ).fetchone()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "role_id": int(row[1]),
        "role_name": str(row[2]),
        "price": int(row[3]),
        "duration_h": int(row[4]),
    }


def upsert_shop_item(
    role_id: int,
    role_name: str,
    price: int,
    duration_h: int,
    added_by: int,
) -> dict[str, Any]:
    ensure_tables()
    clean_price = max(1, int(price))
    clean_duration = max(0, int(duration_h))
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO role_shop(role_id, role_name, price, duration_h, added_by, added_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(role_id) DO UPDATE SET
                role_name=excluded.role_name,
                price=excluded.price,
                duration_h=excluded.duration_h,
                added_by=excluded.added_by,
                added_at=excluded.added_at
            """,
            (
                int(role_id), str(role_name)[:120], clean_price, clean_duration,
                int(added_by), datetime.now(UTC).isoformat(),
            ),
        )
        shop_id = int(conn.execute("SELECT id FROM role_shop WHERE role_id=?", (int(role_id),)).fetchone()[0])
    item = get_shop_item(shop_id)
    assert item is not None
    return item


def delete_shop_item(shop_id: int) -> dict[str, Any] | None:
    item = get_shop_item(shop_id)
    if not item:
        return None
    with connection(SOCIAL_DB) as conn:
        conn.execute("DELETE FROM role_shop WHERE id=?", (int(shop_id),))
    return item


def save_temp_role(user_id: int, role_id: int, guild_id: int, expires_at: datetime) -> None:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute("DELETE FROM temp_roles WHERE user_id=? AND role_id=?", (int(user_id), int(role_id)))
        conn.execute(
            "INSERT INTO temp_roles(user_id, role_id, expires_at, guild_id) VALUES(?,?,?,?)",
            (int(user_id), int(role_id), expires_at.astimezone(UTC).isoformat(), int(guild_id)),
        )


def delete_temp_role(user_id: int, role_id: int) -> bool:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        changed = conn.execute(
            "DELETE FROM temp_roles WHERE user_id=? AND role_id=?",
            (int(user_id), int(role_id)),
        ).rowcount
    return bool(changed)


def list_temp_roles() -> list[dict[str, Any]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            "SELECT user_id, role_id, guild_id, expires_at FROM temp_roles ORDER BY expires_at ASC"
        ).fetchall()
    result = []
    for user_id, role_id, guild_id, expires_at in rows:
        try:
            parsed = datetime.fromisoformat(str(expires_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        except ValueError:
            parsed = datetime.now(UTC)
        result.append(
            {
                "user_id": int(user_id),
                "role_id": int(role_id),
                "guild_id": int(guild_id),
                "expires_at": parsed.astimezone(UTC),
            }
        )
    return result
