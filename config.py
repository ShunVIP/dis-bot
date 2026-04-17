import os
from dotenv import load_dotenv

load_dotenv(dotenv_path="KGTD.env")
TOKEN = os.getenv("tok")

if not TOKEN:
    raise ValueError("❌ Переменная tok не найдена в файле KGTD")

# Steam API (получить: https://steamcommunity.com/dev/apikey)
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
