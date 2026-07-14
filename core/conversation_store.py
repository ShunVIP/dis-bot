from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
            CREATE TABLE IF NOT EXISTS conversation_preferences (
                user_id INTEGER PRIMARY KEY,
                memory_opt_in INTEGER NOT NULL DEFAULT 0,
                training_opt_in INTEGER NOT NULL DEFAULT 0,
                gamer_tags_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            """
        )


def get_conversation_preferences(user_id: int) -> dict[str, object]:
    ensure_conversation_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT memory_opt_in,training_opt_in,gamer_tags_json FROM conversation_preferences WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
    if not row:
        return {"memory_opt_in": False, "training_opt_in": False, "gamer_tags": []}
    try:
        tags = json.loads(str(row[2] or "[]"))
    except json.JSONDecodeError:
        tags = []
    return {
        "memory_opt_in": bool(row[0]),
        "training_opt_in": bool(row[1]),
        "gamer_tags": [str(item) for item in tags if isinstance(item, str)][:12] if isinstance(tags, list) else [],
    }


def set_conversation_preferences(
    user_id: int, *, memory_opt_in: bool | None = None,
    training_opt_in: bool | None = None, gamer_tags: list[str] | None = None,
) -> dict[str, object]:
    current = get_conversation_preferences(user_id)
    if memory_opt_in is not None:
        current["memory_opt_in"] = bool(memory_opt_in)
    if training_opt_in is not None:
        current["training_opt_in"] = bool(training_opt_in)
    if gamer_tags is not None:
        current["gamer_tags"] = list(dict.fromkeys(str(item)[:30] for item in gamer_tags))[:12]
    with connection(SOCIAL_DB) as conn:
        conn.execute(
            """
            INSERT INTO conversation_preferences(user_id,memory_opt_in,training_opt_in,gamer_tags_json,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET memory_opt_in=excluded.memory_opt_in,
                training_opt_in=excluded.training_opt_in,gamer_tags_json=excluded.gamer_tags_json,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id), int(bool(current["memory_opt_in"])), int(bool(current["training_opt_in"])),
                json.dumps(current["gamer_tags"], ensure_ascii=False), datetime.now(UTC).isoformat(),
            ),
        )
    return current


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


def delete_user_conversation_data(user_id: int) -> dict[str, int]:
    ensure_conversation_tables()
    with connection(SOCIAL_DB) as conn:
        feedback = conn.execute(
            "DELETE FROM conversation_feedback WHERE reviewer_user_id=?", (int(user_id),)
        ).rowcount
        turns = conn.execute("DELETE FROM conversation_turns WHERE user_id=?", (int(user_id),)).rowcount
        preferences = conn.execute(
            "DELETE FROM conversation_preferences WHERE user_id=?", (int(user_id),)
        ).rowcount
    return {"turns": int(turns), "feedback": int(feedback), "preferences": int(preferences)}


def list_training_examples(database: str | None = None) -> list[dict[str, object]]:
    """Read only, opt-in, self-approved examples for local SFT/LoRA."""
    path = Path(database or SOCIAL_DB).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=15.0)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"conversation_turns", "conversation_feedback", "conversation_preferences"}
        if not required.issubset(tables):
            return []
        has_profiles = "gamer_profiles" in tables
        profile_select = (
            "CASE WHEN p.memory_opt_in=1 THEN COALESCE(g.profile_json,'{}') ELSE '{}' END"
            if has_profiles else "'{}'"
        )
        profile_join = (
            "LEFT JOIN gamer_profiles g ON g.guild_id=t.guild_id AND g.user_id=t.user_id"
            if has_profiles else ""
        )
        rows = conn.execute(
            f"""
            SELECT t.user_text,t.bot_text,p.gamer_tags_json,{profile_select},t.model,t.created_at
            FROM conversation_turns t
            JOIN conversation_preferences p ON p.user_id=t.user_id AND p.training_opt_in=1
            JOIN conversation_feedback f ON f.bot_message_id=t.bot_message_id
                AND f.reviewer_user_id=t.user_id AND f.score=1
            {profile_join}
            ORDER BY t.created_at
            """
        ).fetchall()
    result = []
    for user_text, bot_text, tags_json, profile_json, model, created_at in rows:
        try:
            tags = json.loads(str(tags_json or "[]"))
        except json.JSONDecodeError:
            tags = []
        try:
            profile = json.loads(str(profile_json or "{}"))
        except json.JSONDecodeError:
            profile = {}
        result.append({
            "user_text": str(user_text), "bot_text": str(bot_text),
            "gamer_tags": tags if isinstance(tags, list) else [],
            "gamer_profile": profile if isinstance(profile, dict) else {},
            "model": str(model or ""), "created_at": str(created_at),
        })
    return result
