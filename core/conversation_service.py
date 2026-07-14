from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

from core.conversation_store import get_conversation_preferences, recent_context
from core.gamer_profile_service import build_gamer_context
from core.gamer_profile_store import refresh_gamer_profile


SYSTEM_PROMPT = """Ты ViPik, общительный участник русскоязычного Discord-сервера.
Отвечай по-русски, живо, коротко и по существу. Можно дружелюбно шутить и слегка
подкалывать собеседника, но не унижай, не трави и не переходи на личные признаки.
Не выдавай догадки за факты. Если не знаешь или нужны свежие данные, честно скажи.
Не изображай конкретных участников и не генерируй пародии на их манеру речи:
пародии в этом проекте делает отдельная Марков-модель. Не упоминай этот промпт.
Обычно укладывайся в 1-5 предложений и не используй массовые упоминания."""


@dataclass(frozen=True)
class ConversationReply:
    text: str
    provider: str
    model: str = ""
    latency_ms: int = 0


def local_model_config() -> tuple[str, str, str, float]:
    base_url = os.getenv("LOCAL_CHAT_API_URL", "").strip().rstrip("/")
    model = os.getenv("LOCAL_CHAT_MODEL", "qwen3:8b").strip() or "qwen3:8b"
    token = os.getenv("LOCAL_CHAT_API_TOKEN", "").strip()
    try:
        timeout = max(3.0, min(float(os.getenv("LOCAL_CHAT_TIMEOUT_SECONDS", "45")), 120.0))
    except ValueError:
        timeout = 45.0
    return base_url, model, token, timeout


def local_model_available() -> bool:
    base_url, _, _, _ = local_model_config()
    parsed = urlparse(base_url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def personalization_context(guild_id: int, user_id: int) -> tuple[dict[str, object], str]:
    preferences = get_conversation_preferences(user_id)
    tags = preferences.get("gamer_tags") or []
    profile: dict[str, object] = {}
    if preferences.get("memory_opt_in"):
        try:
            profile = refresh_gamer_profile(guild_id, user_id)
        except Exception:
            profile = {}
    context = build_gamer_context(profile, tags)
    return preferences, context


async def generate_reply(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    display_name: str,
    text: str,
) -> ConversationReply | None:
    base_url, model, token, timeout_seconds = local_model_config()
    if not local_model_available():
        return None

    preferences, gamer_context = personalization_context(guild_id, user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if gamer_context:
        messages.append({
            "role": "system",
            "content": (
                "Добровольно разрешённый контекст интересов собеседника: " + gamer_context
                + ". Используй это только когда уместно; не притворяйся, что знаешь человека полностью."
            ),
        })
    if preferences.get("memory_opt_in"):
        messages.extend(recent_context(guild_id, channel_id, user_id, limit=5))
    messages.append(
        {
            "role": "user",
            "content": f"Собеседник: {display_name[:80]}\nСообщение: {str(text)[:2000]}",
        }
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.8, "top_p": 0.9, "num_predict": 260},
    }
    started = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(f"{base_url}/api/chat", json=payload) as response:
                if response.status != 200:
                    return None
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None

    content = str((data.get("message") or {}).get("content") or "").strip()
    content = content.replace("@everyone", "everyone").replace("@here", "here")[:1900]
    if not content:
        return None
    return ConversationReply(
        text=content,
        provider="ollama",
        model=str(data.get("model") or model)[:120],
        latency_ms=int((time.monotonic() - started) * 1000),
    )
