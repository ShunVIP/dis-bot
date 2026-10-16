import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.aion2_timers import snapshot
from fun_slesh.aion2 import timer_embed


class Aion2TimerTests(unittest.TestCase):
    def test_next_shugo_rift_and_weekly_reset(self):
        data=snapshot(datetime(2026,10,16,7,25,tzinfo=timezone.utc))
        self.assertEqual(data["shugo"].hour,8)
        self.assertEqual(data["rifts"][0].hour,9)
        self.assertEqual(data["daily"].isoformat(),"2026-10-17T07:00:00+00:00")
        self.assertEqual(data["weekly"].isoformat(),"2026-10-21T07:00:00+00:00")
        self.assertEqual([d.hour for d in data["rifts"]],[9,12,15,18])

    def test_exact_boundary_and_midnight(self):
        boundary=datetime(2026,10,21,7,tzinfo=timezone.utc)
        data=snapshot(boundary)
        self.assertEqual(data["daily"],boundary)
        self.assertEqual(data["weekly"],boundary)
        data=snapshot(datetime(2026,10,21,23,59,59,tzinfo=timezone.utc))
        self.assertEqual(data["shugo"].isoformat(),"2026-10-22T00:00:00+00:00")
        self.assertEqual(data["rifts"][0],data["shugo"])

    def test_display_timezone_does_not_shift_utc_anchor_at_dst(self):
        for day in (24,25,26):
            now=datetime(2026,10,day,8,tzinfo=ZoneInfo("Europe/Berlin"))
            data=snapshot(now)
            self.assertEqual(data["daily"].hour,7)
            self.assertEqual(data["daily"].astimezone(ZoneInfo("Europe/Moscow")).hour,10)
        self.assertTrue(snapshot(datetime(2026,10,25,2,tzinfo=timezone.utc))["recheck_due"])
        self.assertFalse(snapshot(datetime(2026,10,24,2,tzinfo=timezone.utc))["recheck_due"])

    def test_embed_shows_sources_countdowns_and_recheck_note(self):
        embed=timer_embed(datetime(2026,10,26,tzinfo=timezone.utc))
        self.assertEqual(len(embed.fields),4)
        self.assertIn("повторной сверки",embed.description)
        self.assertIn("<t:",embed.fields[0].value)
        self.assertLess(len(embed),6000)

    def test_naive_datetimes_are_rejected(self):
        with self.assertRaises(ValueError):
            snapshot(datetime(2026,10,16))
