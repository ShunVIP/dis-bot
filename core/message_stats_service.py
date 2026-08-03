from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")

_SPLITTER = re.compile(r"\s+", re.UNICODE)
_UNICODE_EMOJI = re.compile(
    r"[\U0001F300-\U0001F5FF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|"
    r"[\U0001F700-\U0001F77F]|[\U0001F780-\U0001F7FF]|[\U0001F800-\U0001F8FF]|"
    r"[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|"
    r"[\u2600-\u26FF]|[\u2700-\u27BF]",
    re.UNICODE,
)
_CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_~]+:[0-9]+>")
_CUSTOM_EMOJI_NAME = re.compile(r"<a?:([A-Za-z0-9_~]+):[0-9]+>")
_WORD_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}", re.UNICODE)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def local_day(value: datetime) -> str:
    return _aware(value).astimezone(MSK).date().isoformat()


def split_duration_by_local_day(started_at: datetime, ended_at: datetime) -> dict[str, int]:
    start = _aware(started_at).astimezone(UTC)
    end = _aware(ended_at).astimezone(UTC)
    if end <= start:
        return {}
    result: dict[str, int] = {}
    cursor = start
    while cursor < end:
        local_cursor = cursor.astimezone(MSK)
        next_day = local_cursor.date() + timedelta(days=1)
        boundary = datetime.combine(next_day, time.min, MSK).astimezone(UTC)
        segment_end = min(end, boundary)
        seconds = max(0, int((segment_end - cursor).total_seconds()))
        if seconds:
            key = local_cursor.date().isoformat()
            result[key] = result.get(key, 0) + seconds
        cursor = segment_end
    return result


def count_words(text: str) -> int:
    clean = str(text or "").strip()
    return len([token for token in _SPLITTER.split(clean) if token]) if clean else 0


def extract_words(text: str) -> list[str]:
    return [
        token
        for token in _WORD_TOKEN.findall(str(text or "").lower())
        if not token.isdigit()
    ]


def extract_emojis(text: str) -> list[str]:
    clean = str(text or "")
    custom = [f":{name}:" for name in _CUSTOM_EMOJI_NAME.findall(clean)]
    return custom + _UNICODE_EMOJI.findall(clean)


def build_message_record(
    *,
    message_id: int,
    user_id: int,
    guild_id: int,
    channel_id: int,
    content: str,
    created_at: datetime,
) -> dict[str, object]:
    clean = str(content or "")
    return {
        "message_id": int(message_id),
        "user_id": int(user_id),
        "guild_id": int(guild_id),
        "channel_id": int(channel_id),
        "date": local_day(created_at),
        "messages": 1,
        "words": count_words(clean),
        "emojis": len(_UNICODE_EMOJI.findall(clean)) + len(_CUSTOM_EMOJI.findall(clean)),
        "chars": len(clean),
        "word_terms": extract_words(clean),
        "emoji_terms": extract_emojis(clean),
    }


def format_duration(seconds: int) -> str:
    clean = max(0, int(seconds))
    return f"{clean // 3600}ч {(clean % 3600) // 60}м"
