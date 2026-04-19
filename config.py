import os
from dotenv import load_dotenv

load_dotenv(dotenv_path="KGTD.env")
TOKEN = os.getenv("tok")

if not TOKEN:
    raise ValueError("❌ Переменная tok не найдена в файле KGTD")

# Steam API (получить: https://steamcommunity.com/dev/apikey)
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")

# Remote heavy-model bridge (optional)
REMOTE_MODEL_API_URL = os.getenv("REMOTE_MODEL_API_URL", "").strip()
REMOTE_MODEL_API_TOKEN = os.getenv("REMOTE_MODEL_API_TOKEN", "").strip()

# Server safety toggles
BOT_SERVER_MODE = os.getenv("BOT_SERVER_MODE", "").strip()
ALLOW_GPT_TRAINING_ON_SERVER = os.getenv("ALLOW_GPT_TRAINING_ON_SERVER", "").strip()
ALLOW_FULL_MAINTENANCE_ON_SERVER = os.getenv("ALLOW_FULL_MAINTENANCE_ON_SERVER", "").strip()
ALLOW_REMOTE_MODEL_INFERENCE = os.getenv("ALLOW_REMOTE_MODEL_INFERENCE", "").strip()

# Optional browser admin panel
WEB_ADMIN_ENABLED = os.getenv("WEB_ADMIN_ENABLED", "").strip()
WEB_ADMIN_HOST = os.getenv("WEB_ADMIN_HOST", "").strip()
WEB_ADMIN_PORT = os.getenv("WEB_ADMIN_PORT", "").strip()
WEB_ADMIN_TOKEN = os.getenv("WEB_ADMIN_TOKEN", "").strip()
WEB_ADMIN_TITLE = os.getenv("WEB_ADMIN_TITLE", "").strip()
WEB_ADMIN_ALLOWED_IPS = os.getenv("WEB_ADMIN_ALLOWED_IPS", "").strip()
