"""Calculated EU timers, anchored to the observed UTC schedule (2026-10-16)."""
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
SOURCE_URL = "https://space4games.com/en/games-en/aion-2-timers-guide/"
VERIFIED_ON = "16.10.2026"
DST_RECHECK = datetime(2026, 10, 25, 1, tzinfo=UTC)


def next_hourly(now, *, interval=1, anchor=0):
    """Next inclusive UTC whole-hour occurrence, independent of display zone."""
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    now = now.astimezone(UTC)
    candidate = now.replace(minute=0, second=0, microsecond=0)
    if candidate < now:
        candidate += timedelta(hours=1)
    while candidate.hour % interval != anchor:
        candidate += timedelta(hours=1)
    return candidate


def next_reset(now, *, weekly=False):
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    now = now.astimezone(UTC)
    candidate = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if candidate < now:
        candidate += timedelta(days=1)
    if weekly:
        candidate += timedelta(days=(2-candidate.weekday()) % 7)
    return candidate


def snapshot(now=None):
    now = now or datetime.now(UTC)
    shugo = next_hourly(now)
    rift = next_hourly(now, interval=3)
    return {
        "now": now.astimezone(UTC),
        "shugo": shugo,
        "rifts": [rift + timedelta(hours=3*i) for i in range(4)],
        "daily": next_reset(now),
        "weekly": next_reset(now, weekly=True),
        "recheck_due": now.astimezone(UTC) >= DST_RECHECK,
    }
