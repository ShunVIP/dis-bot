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
