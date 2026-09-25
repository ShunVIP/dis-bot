from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from core.db import connection
from core.paths import REMINDERS_DB


UTC = timezone.utc
DB_PATH = REMINDERS_DB
_INITIALIZED_DATABASES: set[str] = set()


def _columns(conn) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(reminders)")}


def ensure_tables() -> None:
    if DB_PATH in _INITIALIZED_DATABASES:
        return
    with connection(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                ping_users TEXT NOT NULL DEFAULT '',
                ping_roles TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                repeat TEXT NOT NULL DEFAULT 'once',
                advance_min INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing = _columns(conn)
        migrations = {
            "ping_users": "ALTER TABLE reminders ADD COLUMN ping_users TEXT NOT NULL DEFAULT ''",
            "ping_roles": "ALTER TABLE reminders ADD COLUMN ping_roles TEXT NOT NULL DEFAULT ''",
            "repeat": "ALTER TABLE reminders ADD COLUMN repeat TEXT NOT NULL DEFAULT 'once'",
            "advance_min": "ALTER TABLE reminders ADD COLUMN advance_min INTEGER NOT NULL DEFAULT 0",
            "created_at": "ALTER TABLE reminders ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in migrations.items():
            if column not in existing:
                conn.execute(statement)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user_time ON reminders(user_id,remind_at)"
        )
    _INITIALIZED_DATABASES.add(DB_PATH)


def _parse_ids(raw: str | None) -> list[int]:
    return [int(value) for value in str(raw or "").split(",") if value.strip().isdigit()]


def _reminder(row: tuple | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "channel_id": int(row[2]) if row[2] is not None else None,
        "ping_users": _parse_ids(row[3]),
        "ping_roles": _parse_ids(row[4]),
        "text": str(row[5]),
        "remind_at": str(row[6]),
        "repeat": str(row[7]),
        "advance_min": int(row[8] or 0),
        "created_at": str(row[9] or ""),
    }


_SELECT = (
    "SELECT id,user_id,channel_id,ping_users,ping_roles,text,remind_at,repeat,"
    "advance_min,created_at FROM reminders"
)


def create_reminder(
    *,
    user_id: int,
    channel_id: int | None,
    ping_users: Iterable[int],
    ping_roles: Iterable[int],
    text: str,
    remind_at: datetime,
    repeat: str,
    advance_min: int,
) -> dict[str, Any]:
    ensure_tables()
    when = remind_at if remind_at.tzinfo is not None else remind_at.replace(tzinfo=UTC)
    user_ids = sorted({int(value) for value in ping_users})
    role_ids = sorted({int(value) for value in ping_roles})
    with connection(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO reminders(
                user_id,channel_id,ping_users,ping_roles,text,remind_at,repeat,advance_min,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                int(user_id),
                int(channel_id) if channel_id is not None else None,
                ",".join(str(value) for value in user_ids),
                ",".join(str(value) for value in role_ids),
                str(text)[:1800],
                when.astimezone(UTC).isoformat(),
                str(repeat),
                max(0, int(advance_min)),
                datetime.now(UTC).isoformat(),
            ),
        )
        reminder_id = int(cursor.lastrowid)
    result = get_reminder(reminder_id)
    assert result is not None
    return result


def get_reminder(reminder_id: int) -> dict[str, Any] | None:
    ensure_tables()
    with connection(DB_PATH) as conn:
        row = conn.execute(f"{_SELECT} WHERE id=?", (int(reminder_id),)).fetchone()
    return _reminder(row)


def list_reminders(user_id: int | None = None) -> list[dict[str, Any]]:
    ensure_tables()
    query = _SELECT
    params: tuple[int, ...] = ()
    if user_id is not None:
        query += " WHERE user_id=?"
        params = (int(user_id),)
    query += " ORDER BY remind_at ASC,id ASC"
    with connection(DB_PATH) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_reminder(row) for row in rows if row]


def update_remind_at(reminder_id: int, remind_at: datetime) -> bool:
    ensure_tables()
    when = remind_at if remind_at.tzinfo is not None else remind_at.replace(tzinfo=UTC)
    with connection(DB_PATH) as conn:
        changed = conn.execute(
            "UPDATE reminders SET remind_at=? WHERE id=?",
            (when.astimezone(UTC).isoformat(), int(reminder_id)),
        ).rowcount
    return bool(changed)


def delete_reminder(reminder_id: int, *, owner_user_id: int | None = None) -> bool:
    ensure_tables()
    query = "DELETE FROM reminders WHERE id=?"
    params: tuple[int, ...] = (int(reminder_id),)
    if owner_user_id is not None:
        query += " AND user_id=?"
        params = (int(reminder_id), int(owner_user_id))
    with connection(DB_PATH) as conn:
        changed = conn.execute(query, params).rowcount
    return bool(changed)
