from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from zoneinfo import ZoneInfo

from wwm_kb.pipeline import daily_refresh
from wwm_kb.collectors.fandom_mw import FandomMWCollector
from wwm_kb.collectors.game8_archives import Game8ArchivesCollector

STATE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "wwm_kb_refresh_state.json"))
UTC = timezone.utc


def _read_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(**updates):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state = _read_state()
    state.update(updates)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _needs_retry() -> bool:
    state = _read_state()
    last_success = state.get("last_success")
    last_error = state.get("last_error")
    if last_error:
        return True
    if not last_success:
        return True
    try:
        last_dt = datetime.fromisoformat(last_success)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
    except Exception:
        return True
    return datetime.now(UTC) - last_dt > timedelta(days=8)


async def _refresh_kb():
    collectors = [
        FandomMWCollector(
            categories=[
                "Category:Character",
                "Category:Locations",
                "Category:Items",
                "Category:Quests",
            ]
        ),
        Game8ArchivesCollector(max_pages=800, delay_sec=1.0),
    ]
    started = datetime.now(UTC).isoformat()
    try:
        await daily_refresh(collectors)
    except Exception as e:
        _write_state(last_attempt=started, last_error=f"{type(e).__name__}: {e}")
        raise
    _write_state(last_attempt=started, last_success=datetime.now(UTC).isoformat(), last_error="")


def setup_daily_kb_refresh() -> AsyncIOScheduler:
    """Schedule weekly KB refresh with daily retry safety.

    Call this once during bot startup (e.g., in main_file.py setup_hook).
    """
    berlin = ZoneInfo("Europe/Berlin")
    scheduler = AsyncIOScheduler(timezone=berlin)

    @scheduler.scheduled_job("cron", day_of_week="sun", hour=0, minute=0)
    async def weekly_job():
        await _refresh_kb()

    @scheduler.scheduled_job("cron", hour=6, minute=0)
    async def retry_job():
        if _needs_retry():
            await _refresh_kb()

    scheduler.start()
    return scheduler
