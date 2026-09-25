from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

WEEKDAY_MAP = {
    "weekly_mon": "mon",
    "weekly_tue": "tue",
    "weekly_wed": "wed",
    "weekly_thu": "thu",
    "weekly_fri": "fri",
    "weekly_sat": "sat",
    "weekly_sun": "sun",
}
REPEAT_LABELS = {
    "once": "разовое",
    "daily": "каждый день",
    "weekly_mon": "каждый пн",
    "weekly_tue": "каждый вт",
    "weekly_wed": "каждую ср",
    "weekly_thu": "каждый чт",
    "weekly_fri": "каждую пт",
    "weekly_sat": "каждую сб",
    "weekly_sun": "каждое вс",
    "biweekly": "каждые 2 нед",
}
VALID_REPEATS = set(REPEAT_LABELS)


def aware_utc(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def next_occurrence(
    repeat: str,
    hour: int,
    minute: int,
    fixed_date: datetime | None = None,
    *,
    now: datetime | None = None,
) -> datetime:
    if repeat not in VALID_REPEATS:
        raise ValueError("unsupported repeat mode")
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise ValueError("invalid reminder time")
    now_msk = (now or datetime.now(MSK)).astimezone(MSK)
    if fixed_date is not None:
        target = fixed_date.replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0, tzinfo=MSK
        )
        return target.astimezone(UTC)
    target = now_msk.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if repeat in {"once", "daily"}:
        if target <= now_msk:
            target += timedelta(days=1)
        return target.astimezone(UTC)
    if repeat in WEEKDAY_MAP:
        target_weekday = list(WEEKDAY_MAP).index(repeat)
        days_ahead = (target_weekday - now_msk.weekday()) % 7
        if days_ahead == 0 and target <= now_msk:
            days_ahead = 7
        return (target + timedelta(days=days_ahead)).astimezone(UTC)
    # A biweekly series starts on the nearest future Monday; the scheduler
    # keeps the following occurrences two weeks apart from that anchor.
    days_ahead = (0 - now_msk.weekday()) % 7
    if days_ahead == 0 and target <= now_msk:
        days_ahead = 7
    return (target + timedelta(days=days_ahead)).astimezone(UTC)


def active_reminder(reminder: dict[str, Any], *, now: datetime | None = None) -> bool:
    return reminder["repeat"] != "once" or aware_utc(reminder["remind_at"]) > aware_utc(
        now or datetime.now(UTC)
    )


def active_reminders(rows: list[dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    return [row for row in rows if active_reminder(row, now=now)]


def countdown(value: datetime | str, *, now: datetime | None = None) -> str:
    delta = aware_utc(value) - aware_utc(now or datetime.now(UTC))
    if delta.total_seconds() <= 0:
        return "прямо сейчас"
    days, rest = divmod(int(delta.total_seconds()), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    return "через " + " ".join(parts) if parts else "меньше минуты"


def ping_text(reminder: dict[str, Any]) -> str:
    pings = [f"<@{user_id}>" for user_id in reminder.get("ping_users", [])]
    pings.extend(f"<@&{role_id}>" for role_id in reminder.get("ping_roles", []))
    return " ".join(pings or [f"<@{int(reminder['user_id'])}>"])
