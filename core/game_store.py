from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db import connection
from core.paths import SOCIAL_DB


UTC = timezone.utc


def ensure_game_tables() -> None:
    with connection(SOCIAL_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hangman_games (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                host_id     INTEGER NOT NULL,
                word        TEXT    NOT NULL,
                guessed     TEXT    NOT NULL DEFAULT '',
                wrong       TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hangman_active_channel
                ON hangman_games(channel_id, status, id);
            """
        )


def _game(row: tuple | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row[0]),
        "guild_id": int(row[1]),
        "channel_id": int(row[2]),
        "host_id": int(row[3]),
        "word": str(row[4]),
        "guessed": str(row[5]),
        "wrong": str(row[6]),
        "status": str(row[7]),
        "created_at": str(row[8]),
    }


def start_hangman_game(
    guild_id: int,
    channel_id: int,
    host_id: int,
    word: str,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    ensure_game_tables()
    timestamp = created_at or datetime.now(UTC).isoformat()
    with connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE hangman_games SET status='cancelled' WHERE channel_id=? AND status='active'",
            (int(channel_id),),
        )
        cursor = conn.execute(
            """
            INSERT INTO hangman_games(
                guild_id, channel_id, host_id, word, guessed, wrong, status, created_at
            ) VALUES(?, ?, ?, ?, '', '', 'active', ?)
            """,
            (int(guild_id), int(channel_id), int(host_id), str(word), timestamp),
        )
        row = conn.execute(
            "SELECT id,guild_id,channel_id,host_id,word,guessed,wrong,status,created_at "
            "FROM hangman_games WHERE id=?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _game(row) or {}


def get_active_hangman_game(channel_id: int) -> dict[str, Any] | None:
    ensure_game_tables()
    with connection(SOCIAL_DB) as conn:
        row = conn.execute(
            """
            SELECT id,guild_id,channel_id,host_id,word,guessed,wrong,status,created_at
            FROM hangman_games
            WHERE channel_id=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (int(channel_id),),
        ).fetchone()
    return _game(row)


def guess_hangman_letter(
    channel_id: int,
    user_id: int,
    letter: str,
    *,
    max_wrong: int,
) -> dict[str, Any]:
    ensure_game_tables()
    normalized = str(letter).strip().lower()
    if len(normalized) != 1 or max_wrong < 1:
        raise ValueError("one letter and a positive max_wrong are required")

    with connection(SOCIAL_DB) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id,guild_id,channel_id,host_id,word,guessed,wrong,status,created_at
            FROM hangman_games
            WHERE channel_id=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (int(channel_id),),
        ).fetchone()
        game = _game(row)
        if not game:
            return {"outcome": "no_game"}
        if game["host_id"] == int(user_id):
            return {"outcome": "host_forbidden", "game": game}

        guessed = set(game["guessed"])
        wrong = list(game["wrong"])
        if normalized in guessed or normalized.upper() in wrong:
            return {"outcome": "repeated", "game": game}

        if normalized in game["word"]:
            guessed.add(normalized)
            game["guessed"] = "".join(sorted(guessed))
            won = all(char in guessed or char == "-" for char in game["word"])
            game["status"] = "win" if won else "active"
            outcome = "win" if won else "hit"
        else:
            wrong.append(normalized.upper())
            game["wrong"] = "".join(wrong)
            lost = len(wrong) >= int(max_wrong)
            game["status"] = "lose" if lost else "active"
            outcome = "lose" if lost else "miss"

        conn.execute(
            "UPDATE hangman_games SET guessed=?, wrong=?, status=? WHERE id=? AND status='active'",
            (game["guessed"], game["wrong"], game["status"], game["id"]),
        )
    return {"outcome": outcome, "game": game}
