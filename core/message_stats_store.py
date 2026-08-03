from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from core.db import connection
from core.message_stats_service import split_duration_by_local_day
from core.paths import SOCIAL_DB


UTC = timezone.utc
_INITIALIZED_DATABASES: set[str] = set()
_METRICS = {"messages", "words", "emojis", "chars"}


def ensure_tables() -> None:
    if SOCIAL_DB in _INITIALIZED_DATABASES:
        return
    with connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS msg_stats_daily (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                words INTEGER NOT NULL DEFAULT 0,
                emojis INTEGER NOT NULL DEFAULT 0,
                chars INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, guild_id, channel_id, date)
            );
            CREATE TABLE IF NOT EXISTS msg_word_freq_daily (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                word TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, channel_id, date, word)
            );
            CREATE TABLE IF NOT EXISTS msg_emoji_freq_daily (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                emoji TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, channel_id, date, emoji)
            );
            CREATE TABLE IF NOT EXISTS msg_index_checkpoints (
                channel_id INTEGER PRIMARY KEY,
                last_message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS msg_stats_processed (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                seconds INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS voice_totals_daily (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                seconds INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, guild_id, date)
            );
            CREATE INDEX IF NOT EXISTS idx_msg_stats_guild_date
                ON msg_stats_daily(guild_id, date, channel_id);
            CREATE INDEX IF NOT EXISTS idx_voice_totals_guild_date
                ON voice_totals_daily(guild_id, date);
            CREATE INDEX IF NOT EXISTS idx_msg_stats_processed_channel
                ON msg_stats_processed(channel_id, message_id);
            """
        )
    _INITIALIZED_DATABASES.add(SOCIAL_DB)


def _accumulate_terms(
    conn,
    *,
    table: str,
    column: str,
    guild_id: int,
    channel_id: int,
    day: str,
    values: Iterable[str],
) -> None:
    if (table, column) not in {
        ("msg_word_freq_daily", "word"),
        ("msg_emoji_freq_daily", "emoji"),
    }:
        raise ValueError("unsupported frequency target")
    counts = Counter(str(value).strip() for value in values if str(value).strip())
    for value, count in counts.items():
        conn.execute(
            f"""
            INSERT INTO {table}(guild_id,channel_id,date,{column},count)
            VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id,channel_id,date,{column}) DO UPDATE SET
                count=count+excluded.count
            """,
            (int(guild_id), int(channel_id), str(day), value, int(count)),
        )


def _accumulate_message(conn, record: dict[str, Any]) -> bool:
    inserted = conn.execute(
        """
        INSERT OR IGNORE INTO msg_stats_processed(message_id,channel_id,recorded_at)
        VALUES(?,?,?)
        """,
        (
            int(record["message_id"]),
            int(record["channel_id"]),
            datetime.now(UTC).isoformat(),
        ),
    ).rowcount
    if not inserted:
        return False
    conn.execute(
        """
        INSERT INTO msg_stats_daily(
            user_id,guild_id,channel_id,date,messages,words,emojis,chars
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,guild_id,channel_id,date) DO UPDATE SET
            messages=messages+excluded.messages,
            words=words+excluded.words,
            emojis=emojis+excluded.emojis,
            chars=chars+excluded.chars
        """,
        (
            int(record["user_id"]),
            int(record["guild_id"]),
            int(record["channel_id"]),
            str(record["date"]),
            int(record.get("messages", 1)),
            int(record.get("words", 0)),
            int(record.get("emojis", 0)),
            int(record.get("chars", 0)),
        ),
    )
    common = {
        "guild_id": int(record["guild_id"]),
        "channel_id": int(record["channel_id"]),
        "day": str(record["date"]),
    }
    _accumulate_terms(
        conn,
        table="msg_word_freq_daily",
        column="word",
        values=record.get("word_terms", []),
        **common,
    )
    _accumulate_terms(
        conn,
        table="msg_emoji_freq_daily",
        column="emoji",
        values=record.get("emoji_terms", []),
        **common,
    )
    return True


def record_message(record: dict[str, Any]) -> bool:
    return bool(record_message_batch([record]))


def record_message_batch(
    records: Iterable[dict[str, Any]],
    *,
    checkpoint_channel_id: int | None = None,
    checkpoint_message_id: int | None = None,
) -> int:
    ensure_tables()
    items = list(records)
    inserted = 0
    with connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for record in items:
            inserted += int(_accumulate_message(conn, record))
        if checkpoint_channel_id is not None and checkpoint_message_id is not None:
            conn.execute(
                """
                INSERT INTO msg_index_checkpoints(channel_id,last_message_id) VALUES(?,?)
                ON CONFLICT(channel_id) DO UPDATE SET last_message_id=excluded.last_message_id
                """,
                (int(checkpoint_channel_id), int(checkpoint_message_id)),
            )
    return inserted


def get_checkpoint(channel_id: int) -> int | None:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT last_message_id FROM msg_index_checkpoints WHERE channel_id=?",
            (int(channel_id),),
        ).fetchone()
    return int(row[0]) if row and row[0] else None


def list_user_metric(
    guild_id: int,
    since: str,
    metric: str,
    *,
    channel_id: int | None = None,
    excluded_channel_ids: Iterable[int] = (),
    limit: int = 15,
) -> list[tuple[int, int]]:
    ensure_tables()
    if metric not in _METRICS:
        raise ValueError("unsupported message metric")
    conditions = ["guild_id=?", "date>=?"]
    params: list[int | str] = [int(guild_id), str(since)]
    if channel_id is not None:
        conditions.append("channel_id=?")
        params.append(int(channel_id))
    else:
        excluded = sorted({int(value) for value in excluded_channel_ids})
        if excluded:
            conditions.append(f"channel_id NOT IN ({','.join('?' for _ in excluded)})")
            params.extend(excluded)
    params.append(max(1, min(100, int(limit))))
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            f"""
            SELECT user_id,SUM({metric}) AS total
            FROM msg_stats_daily
            WHERE {' AND '.join(conditions)}
            GROUP BY user_id
            ORDER BY total DESC,user_id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [(int(user_id), int(total)) for user_id, total in rows]


def record_voice_session(
    user_id: int,
    guild_id: int,
    channel_id: int,
    started_at: datetime,
    ended_at: datetime,
) -> int:
    ensure_tables()
    start = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    end = ended_at if ended_at.tzinfo is not None else ended_at.replace(tzinfo=UTC)
    seconds = max(0, int((end - start).total_seconds()))
    if not seconds:
        return 0
    with connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO voice_sessions(user_id,guild_id,channel_id,started_at,ended_at,seconds)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(user_id), int(guild_id), int(channel_id),
                start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat(), seconds,
            ),
        )
        for day, daily_seconds in split_duration_by_local_day(start, end).items():
            conn.execute(
                """
                INSERT INTO voice_totals_daily(user_id,guild_id,date,seconds) VALUES(?,?,?,?)
                ON CONFLICT(user_id,guild_id,date) DO UPDATE SET seconds=seconds+excluded.seconds
                """,
                (int(user_id), int(guild_id), day, int(daily_seconds)),
            )
    return seconds


def list_voice_totals(guild_id: int, since: str, limit: int = 15) -> list[tuple[int, int]]:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT user_id,SUM(seconds) AS total
            FROM voice_totals_daily
            WHERE guild_id=? AND date>=?
            GROUP BY user_id
            ORDER BY total DESC,user_id ASC
            LIMIT ?
            """,
            (int(guild_id), str(since), max(1, min(100, int(limit)))),
        ).fetchall()
    return [(int(user_id), int(total)) for user_id, total in rows]


def get_user_voice_total(guild_id: int, user_id: int, since: str) -> int:
    ensure_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(seconds),0)
            FROM voice_totals_daily
            WHERE guild_id=? AND user_id=? AND date>=?
            """,
            (int(guild_id), int(user_id), str(since)),
        ).fetchone()
    return int(row[0] or 0)
