from __future__ import annotations

import asyncio
import hashlib
import json
from typing import AsyncIterator, List, Optional

import aiohttp

from .base import Collector, RawRecord

API_URL = "https://where-winds-meet.fandom.com/api.php"


class FandomMWCollector(Collector):
    """Collect pages from Where Winds Meet Fandom wiki via MediaWiki API."""

    source = "fandom"
    method = "mw_api"

    def __init__(self, categories: List[str], *, polite_delay: float = 0.8) -> None:
        self.categories = categories
        self.polite_delay = max(0.0, polite_delay)

    async def _get(self, session: aiohttp.ClientSession, params: dict) -> dict:
        params = dict(params)
        params.setdefault("format", "json")
        async with session.get(
            API_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def _iter_category_members(self, session: aiohttp.ClientSession, category: str):
        cmcontinue: Optional[str] = None
        while True:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category,
                "cmlimit": 500,
                "cmtype": "page",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            data = await self._get(session, params)
            members = data.get("query", {}).get("categorymembers", [])
            for m in members:
                yield m

            cmcontinue = data.get("continue", {}).get("cmcontinue")
            if not cmcontinue:
                break

            if self.polite_delay:
                await asyncio.sleep(self.polite_delay)

    async def _fetch_page(self, session: aiohttp.ClientSession, pageid: int):
        data = await self._get(
            session,
            {
                "action": "query",
                "prop": "revisions|categories|info",
                "pageids": str(pageid),
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "cllimit": 500,
                "inprop": "url",
            },
        )
        page = next(iter(data.get("query", {}).get("pages", {}).values()))
        rev = (page.get("revisions") or [{}])[0]
        slots = rev.get("slots", {})
        wikitext = ""

        if "main" in slots and "*" in slots["main"]:
            wikitext = slots["main"]["*"]
        elif "*" in rev:
            wikitext = rev["*"]

        payload = {
            "pageid": page.get("pageid"),
            "title": page.get("title"),
            "canonicalurl": page.get("canonicalurl") or page.get("fullurl"),
            "revid": rev.get("revid"),
            "timestamp": rev.get("timestamp"),
            "categories": [c["title"] for c in page.get("categories", []) if "title" in c],
            "wikitext": wikitext,
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        h = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        return payload, payload_json, h

    async def collect(self) -> AsyncIterator[RawRecord]:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "dis-bot/2.0 (WhereWindsMeet KB; daily sync)"}
        ) as session:
            for category in self.categories:
                async for m in self._iter_category_members(session, category):
                    pageid = m["pageid"]
                    payload, payload_json, h = await self._fetch_page(session, pageid)
                    yield RawRecord(
                        source=self.source,
                        method=self.method,
                        entity_type="page",
                        external_id=str(pageid),
                        title=payload.get("title"),
                        url=payload.get("canonicalurl"),
                        payload_json=payload_json,
                        content_hash=h,
                    )
                    # extra small delay to reduce burstiness
                    if self.polite_delay:
                        await asyncio.sleep(self.polite_delay / 2)
