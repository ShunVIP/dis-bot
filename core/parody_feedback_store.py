from __future__ import annotations

from datetime import datetime, timezone

from core.db import connection as db_connection
from core.paths import PARODY_RATINGS_DB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_feedback_tables() -> None:
    with db_connection(PARODY_RATINGS_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS phrase_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                quality TEXT NOT NULL,
                phrase TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK(rating IN (-1, 1)),
                rated_by INTEGER NOT NULL,
                rated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pr_user
            ON phrase_ratings(user_id, quality, rating);
            """
        )


def save_rating(user_id: int, quality: str, phrase: str, rating: int, rated_by: int) -> None:
    if int(rating) not in (-1, 1):
        raise ValueError("rating must be -1 or 1")
    clean_phrase = str(phrase).strip()[:2000]
    if not clean_phrase:
        raise ValueError("phrase is required")
    ensure_feedback_tables()
    with db_connection(PARODY_RATINGS_DB) as conn:
        conn.execute(
            """
            INSERT INTO phrase_ratings(user_id, quality, phrase, rating, rated_by, rated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (int(user_id), str(quality)[:40], clean_phrase, int(rating), int(rated_by), _now()),
        )


def get_phrases_by_rating(user_id: int, quality: str, rating: int) -> list[str]:
    ensure_feedback_tables()
    with db_connection(PARODY_RATINGS_DB) as conn:
        rows = conn.execute(
            """
            SELECT phrase FROM phrase_ratings
            WHERE user_id=? AND quality=? AND rating=?
            ORDER BY id ASC
            """,
            (int(user_id), str(quality), int(rating)),
        ).fetchall()
    return [str(row[0]) for row in rows]


def get_bad_phrases(user_id: int, quality: str) -> set[str]:
    return set(get_phrases_by_rating(user_id, quality, -1))


def get_good_phrases(user_id: int, quality: str) -> list[str]:
    return get_phrases_by_rating(user_id, quality, 1)
