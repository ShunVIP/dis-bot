from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db import connection
from core.paths import SOCIAL_DB


UTC = timezone.utc


def ensure_conversation_tables() -> None:
    with connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_turns (
                bot_message_id INTEGER PRIMARY KEY,
                source_message_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_text TEXT NOT NULL,
                bot_text TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_context
                ON conversation_turns(guild_id, channel_id, user_id, created_at);
            CREATE TABLE IF NOT EXISTS conversation_feedback (
                bot_message_id INTEGER NOT NULL,
                reviewer_user_id INTEGER NOT NULL,
                score INTEGER NOT NULL CHECK(score IN (-1, 1)),
                created_at TEXT NOT NULL,
                PRIMARY KEY (bot_message_id, reviewer_user_id),
                FOREIGN KEY (bot_message_id) REFERENCES conversation_turns(bot_message_id)
                    ON DELETE CASCADE
            );
            """
        )


def recent_context(
    guild_id: int,
    channel_id: int,
    user_id: int,
    *,
    limit: int = 5,
) -> list[dict[str, str]]:
    ensure_conversation_tables()
    with connection(SOCIAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT user_text,bot_text FROM conversation_turns
            WHERE guild_id=? AND channel_id=? AND user_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (int(guild_id), int(channel_id), int(user_id), max(0, min(int(limit), 10))),
        ).fetchall()
    messages: list[dict[str, str]] = []
    for user_text, bot_text in reversed(rows):
        messages.append({"role": "user", "content": str(user_text)[:2000]})
        messages.append({"role": "assistant", "content": str(bot_text)[:2000]})
    return messages


def record_turn(
    *,
    bot_message_id: int,
    source_message_id: int,
    guild_id: int,
    channel_id: int,
    user_id: int,
    user_text: str,
    bot_text: str,
    provider: str,
    model: str = "",
    latency_ms: int = 0,
) -> None:
    ensure_conversation_tables()
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO conversation_turns(
                bot_message_id,source_message_id,guild_id,channel_id,user_id,
                user_text,bot_text,provider,model,latency_ms,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(bot_message_id), int(source_message_id), int(guild_id),
                int(channel_id), int(user_id), str(user_text)[:2000],
                str(bot_text)[:2000], str(provider)[:40], str(model)[:120],
                max(0, int(latency_ms)), datetime.now(UTC).isoformat(),
            ),
        )


def record_feedback(bot_message_id: int, reviewer_user_id: int, score: int) -> bool:
    if int(score) not in {-1, 1}:
        raise ValueError("score must be -1 or 1")
    ensure_conversation_tables()
    with connection(SOCIAL_DB) as conn:
        exists = conn.execute(
            "SELECT 1 FROM conversation_turns WHERE bot_message_id=?",
            (int(bot_message_id),),
        ).fetchone()
        if not exists:
            return False
        conn.execute(
            """
            INSERT INTO conversation_feedback(bot_message_id,reviewer_user_id,score,created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(bot_message_id,reviewer_user_id) DO UPDATE SET
                score=excluded.score, created_at=excluded.created_at
            """,
            (int(bot_message_id), int(reviewer_user_id), int(score), datetime.now(UTC).isoformat()),
        )
    return True


def purge_old_turns(*, retention_days: int = 90) -> int:
    ensure_conversation_tables()
    cutoff = (datetime.now(UTC) - timedelta(days=max(7, int(retention_days)))).isoformat()
    with connection(SOCIAL_DB) as conn:
        cursor = conn.execute("DELETE FROM conversation_turns WHERE created_at<?", (cutoff,))
        return int(cursor.rowcount)
