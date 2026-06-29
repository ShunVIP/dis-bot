# core/paths.py
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_DIR = Path(os.getenv("DATABASE_DIR", PROJECT_ROOT / "datebase")).resolve()
MODELS_DIR = Path(os.getenv("MODELS_DIR", PROJECT_ROOT / "models")).resolve()
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", DATABASE_DIR / "uploads")).resolve()


def ensure_runtime_dirs():
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def db_path(filename: str) -> str:
    ensure_runtime_dirs()
    return str(DATABASE_DIR / filename)


SOCIAL_DB = db_path("social.db")
MESSAGES_DB = db_path("messages.db")
BIRTHDAYS_DB = db_path("birthdays.db")
REMINDERS_DB = db_path("reminders.db")
PERSONA_DB = db_path("persona.db")
PARODY_FILTERS_DB = db_path("parody_filters.db")
PARODY_RATINGS_DB = db_path("parody_ratings.db")
WWM_DB = db_path("wwm.db")
