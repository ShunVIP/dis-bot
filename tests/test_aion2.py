import asyncio
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import aion2_service as service, aion2_store as store
from fun_slesh.aion2 import Aion2View, EventModal


class Aion2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store,"SOCIAL_DB",str(Path(self.temp.name)/"social.db"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_profiles_are_guild_scoped_and_deletable(self):
        values=service.validate_member("Люся","global","Server","Целитель")
        store.save_member(1,10,*values)
        store.save_member(2,10,"Other","kr","Korea","Tank")
        self.assertEqual(store.list_members(1)[0]["nickname"],"Люся")
        store.delete_member(1,10)
        self.assertEqual(store.list_members(1),[])
        self.assertEqual(len(store.list_members(2)),1)
        with self.assertRaises(ValueError):
            service.validate_member("name","invalid","server","class")

    def test_schedule_uses_moscow_and_cross_guild_delete_is_rejected(self):
        now=datetime(2026,10,16,tzinfo=timezone.utc)
        title,starts=service.parse_event("Рейд","17.10.2026 21:00",now=now)
        self.assertEqual(starts,"2026-10-17T18:00:00+00:00")
        event_id=store.add_event(1,10,title,starts)
        self.assertEqual(len(store.list_events(1,now.isoformat())),1)
        self.assertFalse(store.delete_event(2,event_id))
        self.assertTrue(store.delete_event(1,event_id))
        with self.assertRaises(ValueError):
            service.parse_event("Рейд","15.10.2026 21:00",now=now)

    def test_news_only_accepts_official_feed_and_constructs_trusted_links(self):
        payload={"appnews":{"newsitems":[
            {"gid":"123","title":"Maintenance","contents":"<b>Hello</b> [url=x]world[/url]","date":1,"feedname":"steam_community_announcements","url":"https://evil.example"},
            {"gid":"124","title":"Unofficial","feedname":"other"},
        ]}}
        news=service.normalize_news(payload)
        self.assertEqual(len(news),1)
        self.assertEqual(news[0]["kind"],"maintenance")
        self.assertEqual(news[0]["url"],"https://store.steampowered.com/news/app/3393110")
        self.assertNotIn("<b>",news[0]["text"])

    def test_cached_news_does_not_call_network(self):
        store.set_cache([{"title":"Cached"}],time.time())
        with patch.object(service.aiohttp,"ClientSession",side_effect=AssertionError("network called")):
            items,stale=asyncio.run(service.get_news())
        self.assertEqual(items,[{"title":"Cached"}])
        self.assertFalse(stale)

    def test_ui_rechecks_owner_and_admin_permissions(self):
        async def check():
            view=Aion2View(10)
            inter=SimpleNamespace(user=SimpleNamespace(id=20),guild_id=1,response=SimpleNamespace(send_message=AsyncMock()))
            self.assertFalse(await view.interaction_check(inter))
            inter.permissions=SimpleNamespace(manage_guild=False)
            with patch.object(store,"add_event",side_effect=AssertionError("unauthorized write")):
                await EventModal().on_submit(inter)
            self.assertEqual(inter.response.send_message.await_count,2)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
