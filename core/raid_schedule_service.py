"""Deterministic four-day raid schedule for Lucy and Raimi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class ShiftStatus:
    code: str
    label: str
    emoji: str
    raid_eligible: bool


@dataclass(frozen=True)
class PersonCycle:
    name: str
    anchor_date: date
    days: tuple[ShiftStatus, ...]


@dataclass(frozen=True)
class RaidDay:
    day: date
    lucy: ShiftStatus
    raimi: ShiftStatus
    available: bool
    availability_note: str


OFF = ShiftStatus("off", "выходной", "🟢", True)
LUCY_OFFICE = ShiftStatus("office", "работа до 23:00 МСК", "🔴", False)
LUCY_REMOTE = ShiftStatus("remote", "удалёнка 09:00–21:00 МСК", "🟡", True)
RAIMI_WORK_24H = ShiftStatus("work24", "смена 24 часа (112)", "🔴", False)
RAIMI_RECOVERY = ShiftStatus("recovery", "отсыпной перед сменой", "⚫", False)

LUCY_CYCLE = PersonCycle(
    name="Люси",
    anchor_date=date(2026, 7, 30),
    days=(OFF, LUCY_OFFICE, LUCY_REMOTE, LUCY_REMOTE),
)
RAIMI_CYCLE = PersonCycle(
    name="Рейми",
    anchor_date=date(2026, 7, 30),
    days=(RAIMI_WORK_24H, OFF, OFF, RAIMI_RECOVERY),
)

WEEKDAY_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def cycle_status(cycle: PersonCycle, day: date) -> ShiftStatus:
    offset = (day - cycle.anchor_date).days % len(cycle.days)
    return cycle.days[offset]


def week_bounds(reference: date) -> tuple[date, date]:
    monday = reference - timedelta(days=reference.weekday())
    return monday, monday + timedelta(days=6)


def build_raid_week(reference: date) -> tuple[RaidDay, ...]:
    monday, _ = week_bounds(reference)
    result = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        lucy = cycle_status(LUCY_CYCLE, day)
        raimi = cycle_status(RAIMI_CYCLE, day)
        available = lucy.raid_eligible and raimi.raid_eligible
        if not available:
            note = ""
        elif lucy.code == "remote":
            note = "после 21:00 МСК"
        else:
            note = "весь день"
        result.append(RaidDay(day, lucy, raimi, available, note))
    return tuple(result)


def available_raid_days(reference: date) -> tuple[RaidDay, ...]:
    return tuple(item for item in build_raid_week(reference) if item.available)


def raid_week_key(reference: date) -> str:
    monday, _ = week_bounds(reference)
    return monday.isoformat()


def raid_post_is_due(
    state: dict[str, object],
    reference: date,
    channel_id: int,
) -> bool:
    return (
        state.get("last_week_start") != raid_week_key(reference)
        or state.get("last_channel_id") != int(channel_id)
    )


def build_raid_post_state(
    reference: date,
    channel_id: int,
    message_id: int,
    posted_at: str,
) -> dict[str, object]:
    return {
        "last_week_start": raid_week_key(reference),
        "last_channel_id": int(channel_id),
        "last_message_id": int(message_id),
        "posted_at": posted_at,
    }


def parse_reference_date(raw: str | None, *, today: date) -> date:
    value = str(raw or "").strip()
    if not value:
        return today
    formats = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass
    try:
        return datetime.strptime(f"{value}.{today.year}", "%d.%m.%Y").date()
    except ValueError as exc:
        raise ValueError("дата должна быть в формате ДД.ММ, ДД.ММ.ГГГГ или ГГГГ-ММ-ДД") from exc


def format_raid_day(item: RaidDay) -> str:
    marker = "✅" if item.available else "—"
    suffix = f" · **рейд {item.availability_note}**" if item.available else ""
    return (
        f"{marker} **{WEEKDAY_SHORT[item.day.weekday()]} {item.day:%d.%m}**"
        f" · Люси: {item.lucy.emoji} {item.lucy.label}"
        f" · Рейми: {item.raimi.emoji} {item.raimi.label}{suffix}"
    )
