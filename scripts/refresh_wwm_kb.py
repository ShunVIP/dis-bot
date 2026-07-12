from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from core.data_catalog import audit_database
from core.paths import WWM_DB
from wwm_kb.collectors.fandom_mw import FandomMWCollector
from wwm_kb.collectors.game8_archives import Game8ArchivesCollector
from wwm_kb.pipeline import daily_refresh


async def refresh(max_game8_pages: int) -> dict:
    collectors = [
        FandomMWCollector(
            categories=[
                "Category:Character",
                "Category:Locations",
                "Category:Items",
                "Category:Quests",
            ]
        ),
        Game8ArchivesCollector(max_pages=max_game8_pages, delay_sec=1.0),
    ]
    started = datetime.now(timezone.utc).isoformat()
    await daily_refresh(collectors)
    return {"started_at": started, "audit": audit_database("wwm", WWM_DB)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the heavy WWM knowledge base on the local PC")
    parser.add_argument("--max-game8-pages", type=int, default=800)
    args = parser.parse_args()
    report = asyncio.run(refresh(max(1, args.max_game8_pages)))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["audit"]["integrity"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
