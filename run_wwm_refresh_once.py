import asyncio

from wwm_kb.pipeline import daily_refresh
from wwm_kb.collectors.fandom_mw import FandomMWCollector
from wwm_kb.collectors.game8_archives import Game8ArchivesCollector

async def main():
    collectors = [
        FandomMWCollector(categories=[
            "Category:Character",
            "Category:Locations",
            "Category:Items",
            "Category:Quests",
        ]),
         Game8ArchivesCollector(max_pages=9999, delay_sec=1.0),  # сначала лимит 200 для теста
    ]
    await daily_refresh(collectors)

asyncio.run(main())
