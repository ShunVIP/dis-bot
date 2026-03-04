from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from zoneinfo import ZoneInfo

from wwm_kb.pipeline import daily_refresh
from wwm_kb.collectors.fandom_mw import FandomMWCollector
from wwm_kb.collectors.game8_archives import Game8ArchivesCollector

def setup_daily_kb_refresh() -> AsyncIOScheduler:
    """Schedule daily KB refresh at 00:00 Europe/Berlin.

    Call this once during bot startup (e.g., in main_file.py setup_hook).
    """
    berlin = ZoneInfo("Europe/Berlin")
    scheduler = AsyncIOScheduler(timezone=berlin)

    @scheduler.scheduled_job("cron", hour=0, minute=0)
    async def job():
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
        await daily_refresh(collectors)

    scheduler.start()
    return scheduler
