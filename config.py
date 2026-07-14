import os
from dotenv import load_dotenv

load_dotenv(dotenv_path="KGTD.env")

# Discord bot token. Keep `tok` for backward compatibility with the old VPS env.
TOKEN = (os.getenv("tok") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()

# Discord OAuth for the future site/app.
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "").strip()
DISCORD_OAUTH_SCOPES = os.getenv("DISCORD_OAUTH_SCOPES", "identify email guilds").strip()
APP_ALLOWED_GUILD_IDS = os.getenv("APP_ALLOWED_GUILD_IDS", "").strip()
APP_OWNER_USER_IDS = os.getenv("APP_OWNER_USER_IDS", "").strip()

# Future web/app API.
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip()
APP_API_HOST = os.getenv("APP_API_HOST", "127.0.0.1").strip()
APP_API_PORT = os.getenv("APP_API_PORT", "3000").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "").strip()
UPLOAD_MAX_MB = os.getenv("UPLOAD_MAX_MB", "25").strip()

# Native fallback voice, optional. LiveKit is the preferred self-hosted voice server.
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()

# Steam API (get from https://steamcommunity.com/dev/apikey).
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "").strip()

# Riot API / League of Legends.
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "").strip()
RIOT_PLATFORM_REGION = os.getenv("RIOT_PLATFORM_REGION", "ru").strip()
RIOT_REGIONAL_ROUTING = os.getenv("RIOT_REGIONAL_ROUTING", "europe").strip()

# Server safety toggles.
BOT_SERVER_MODE = os.getenv("BOT_SERVER_MODE", "").strip()
ALLOW_FULL_MAINTENANCE_ON_SERVER = os.getenv("ALLOW_FULL_MAINTENANCE_ON_SERVER", "").strip()

# Optional browser admin panel.
WEB_ADMIN_ENABLED = os.getenv("WEB_ADMIN_ENABLED", "").strip()
WEB_ADMIN_HOST = os.getenv("WEB_ADMIN_HOST", "").strip()
WEB_ADMIN_PORT = os.getenv("WEB_ADMIN_PORT", "").strip()
WEB_ADMIN_TOKEN = os.getenv("WEB_ADMIN_TOKEN", "").strip()
WEB_ADMIN_TITLE = os.getenv("WEB_ADMIN_TITLE", "").strip()
WEB_ADMIN_ALLOWED_IPS = os.getenv("WEB_ADMIN_ALLOWED_IPS", "").strip()
