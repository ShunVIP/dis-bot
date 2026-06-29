# core/economy_profile.py
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

GENDER_MALE = "male"
GENDER_FEMALE = "female"


def _ensure_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS economy_profiles (
                user_id          INTEGER PRIMARY KEY,
                gender           TEXT NOT NULL DEFAULT '',
                age_confirmed    INTEGER NOT NULL DEFAULT 0,
                updated_at       TEXT NOT NULL
            )
            """
        )
        conn.commit()


def set_economy_profile(user_id: int, gender: str, age_confirmed: bool):
    _ensure_tables()
    if gender not in {GENDER_MALE, GENDER_FEMALE}:
        raise ValueError("gender must be male or female")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO economy_profiles(user_id, gender, age_confirmed, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                gender=excluded.gender,
                age_confirmed=excluded.age_confirmed,
                updated_at=excluded.updated_at
            """,
            (user_id, gender, int(age_confirmed), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_economy_profile(user_id: int) -> dict:
    _ensure_tables()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT gender, age_confirmed, updated_at FROM economy_profiles WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"gender": "", "age_confirmed": False, "updated_at": ""}
    return {"gender": row[0] or "", "age_confirmed": bool(row[1]), "updated_at": row[2] or ""}


def can_receive_currency(user_id: int) -> bool:
    profile = get_economy_profile(user_id)
    return profile["age_confirmed"] and profile["gender"] in {GENDER_MALE, GENDER_FEMALE}


def currency_name(user_id: int) -> str:
    profile = get_economy_profile(user_id)
    if profile["gender"] == GENDER_MALE and profile["age_confirmed"]:
        return "Пенис"
    if profile["gender"] == GENDER_FEMALE and profile["age_confirmed"]:
        return "Сиськи"
    return "валюта"


def currency_amount(user_id: int, amount: int) -> str:
    return f"{amount} {currency_name(user_id)}"


def size_name(user_id: int) -> str:
    profile = get_economy_profile(user_id)
    if profile["gender"] == GENDER_MALE and profile["age_confirmed"]:
        return "Размер пениса"
    if profile["gender"] == GENDER_FEMALE and profile["age_confirmed"]:
        return "Размер сисек"
    return "Размер"


def economy_profile_required_text() -> str:
    return (
        "Чтобы получать валюту, сначала заполни экономический профиль: "
        "укажи пол и подтверди, что тебе есть 18 лет. "
        "После этого для мужчин валюта называется Пенис, для девушек - Сиськи."
    )
