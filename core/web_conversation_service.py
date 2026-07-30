from __future__ import annotations

import re

from core import conversation_service
from core.conversation_store import (
    complete_web_conversation_request,
    enqueue_web_conversation_request,
    fail_web_conversation_request,
    record_turn,
)
from core.platform_store import add_platform_message


BOT_NAME = "ViPik"
_EXPLICIT_MENTION = re.compile(r"(?iu)(?<![\w@])@?(?:vipik|випик)(?!\w)")
_OFFLINE_REPLY = (
    "Я услышал, но мой локальный мозг сейчас не на связи. "
    "Попробуй ещё раз чуть позже — железный шаман скоро вернётся."
)


def is_explicit_request(text: str) -> bool:
    return bool(_EXPLICIT_MENTION.search(str(text or "")))


def queue_if_explicit(
    *, source_message_id: int, target_id: int, guild_id: int, channel_id: int,
    user_id: int, display_name: str, text: str, scope: str = "channel",
) -> bool:
    if scope != "channel" or not is_explicit_request(text):
        return False
    return enqueue_web_conversation_request(
        source_message_id=source_message_id,
        target_id=target_id,
        guild_id=guild_id,
        channel_id=channel_id,
        user_id=user_id,
        display_name=display_name,
        user_text=text,
    )


async def process_web_conversation_job(job: dict[str, object]) -> int:
    try:
        reply = await conversation_service.generate_reply(
            guild_id=int(job["guild_id"]),
            channel_id=int(job["channel_id"]),
            user_id=int(job["user_id"]),
            display_name=str(job["display_name"]),
            text=str(job["user_text"]),
        )
        text = reply.text if reply else _OFFLINE_REPLY
        provider = reply.provider if reply else "templates"
        model = reply.model if reply else ""
        latency_ms = reply.latency_ms if reply else 0
        bot_message_id = add_platform_message(
            "channel",
            int(job["target_id"]),
            0,
            BOT_NAME,
            text,
            guild_id=int(job["guild_id"]),
            channel_id=int(job["channel_id"]),
            source="conversation_ai",
            reply_to_message_id=int(job["source_message_id"]),
            queue_discord=bool(int(job["guild_id"]) and int(job["channel_id"])),
        )
        record_turn(
            bot_message_id=bot_message_id,
            source_message_id=int(job["source_message_id"]),
            guild_id=int(job["guild_id"]),
            channel_id=int(job["channel_id"]),
            user_id=int(job["user_id"]),
            user_text=str(job["user_text"]),
            bot_text=text,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
        )
        complete_web_conversation_request(int(job["id"]), bot_message_id)
        return bot_message_id
    except Exception as exc:
        fail_web_conversation_request(int(job["id"]), f"{type(exc).__name__}: {exc}")
        raise
