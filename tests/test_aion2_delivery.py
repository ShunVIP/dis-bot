import asyncio
import tempfile
import unittest
import discord
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import aion2_timers, settings_store
from fun_slesh.aion2 import Aion2, TIMERS_FEATURE
from core.aion2_presence import is_playing_aion2, has_players


class Channel:
    id = 123

    def __init__(self):
        self.messages = []
        self.members = []
        self.send = AsyncMock(side_effect=self._send)

    def permissions_for(self,member):
        return SimpleNamespace(view_channel=True,send_messages=True,embed_links=True,read_message_history=True,manage_messages=False)

    async def _send(self,**kwargs):
        message = SimpleNamespace(id=len(self.messages)+1,author=SimpleNamespace(id=42),embeds=[kwargs['embed']],pinned=False,edit=AsyncMock(),delete=AsyncMock())
        async def edit(**values):
            message.embeds=[values['embed']]
        async def delete():
            if message in self.messages:
                self.messages.remove(message)
        message.edit.side_effect=edit
        message.delete.side_effect=delete
        self.messages.insert(0,message)
        return message

    async def fetch_message(self,message_id):
        return next(m for m in self.messages if m.id==message_id)

    async def history(self,limit):
        for message in self.messages[:limit]:
            yield message


class Aion2DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(settings_store,'SOCIAL_DB',str(Path(self.temp.name)/'social.db'))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_coincident_events_have_one_notification_and_no_backlog(self):
        now=datetime(2026,10,21,6,55,tzinfo=timezone.utc)
        notice=aion2_timers.due_notice(now)
        self.assertEqual(len(notice['names']),3)  # Shugo and both resets at 07 UTC.
        self.assertIsNone(aion2_timers.due_notice(now.replace(minute=54)))
        self.assertIsNone(aion2_timers.due_notice(now.replace(hour=7,minute=0)))

    def test_delivery_is_channel_scoped_no_mentions_and_restart_deduplicated(self):
        async def check():
            channel=Channel()
            guild=SimpleNamespace(id=1,me=object(),get_channel=lambda channel_id: channel if channel_id==123 else None)
            bot=SimpleNamespace(user=SimpleNamespace(id=42))
            now=datetime(2026,10,16,11,55,tzinfo=timezone.utc)
            settings_store.set_feature_channel(1,TIMERS_FEATURE,123,'output')
            with patch('fun_slesh.aion2.discord.TextChannel',Channel):
                await Aion2(bot)._publish_timers(guild,now)
                await Aion2(bot)._publish_timers(guild,now)
                self.assertEqual(channel.send.await_count,2)  # Dashboard + combined Shugo/Rift alert.
                for call in channel.send.call_args_list:
                    self.assertEqual(call.kwargs['allowed_mentions'].to_dict()['parse'],[])
                # Recover a sent notification even if the process died before saving its key.
                settings_store.set_feature_runtime_state(1,TIMERS_FEATURE,{'last_notice_key':None})
                await Aion2(bot)._publish_timers(guild,now)
                self.assertEqual(channel.send.await_count,2)
                settings_store.set_feature_enabled(1,TIMERS_FEATURE,False)
                await Aion2(bot)._publish_timers(guild,now)
                self.assertEqual(channel.send.await_count,2)
        asyncio.run(check())

    def test_unconfigured_guild_never_selects_another_channel(self):
        async def check():
            guild=SimpleNamespace(id=2)
            await Aion2(SimpleNamespace())._publish_timers(guild,datetime.now(timezone.utc))
        asyncio.run(check())

    def test_expiry_windows_and_restart_cleanup_keep_dashboard(self):
        async def check():
            channel=Channel()
            bot=SimpleNamespace(user=SimpleNamespace(id=42))
            guild=SimpleNamespace(id=1,me=object(),get_channel=lambda _:channel)
            settings_store.set_feature_channel(1,TIMERS_FEATURE,123,'output')
            with patch('fun_slesh.aion2.discord.TextChannel',Channel):
                await Aion2(bot)._publish_timers(guild,datetime(2026,10,16,11,55,tzinfo=timezone.utc))
                notice,dashboard=channel.messages
                # Combined Shugo + Rift lasts ten minutes after the opening.
                await Aion2(bot)._publish_timers(guild,datetime(2026,10,16,12,2,tzinfo=timezone.utc))
                notice.delete.assert_not_awaited()
                await Aion2(bot)._publish_timers(guild,datetime(2026,10,16,12,10,tzinfo=timezone.utc))
                notice.delete.assert_awaited_once()
                dashboard.delete.assert_not_awaited()
                self.assertEqual(settings_store.get_feature_runtime_state(1,TIMERS_FEATURE)['pending_notices'],[])
        asyncio.run(check())

    def test_shugo_only_expires_three_minutes_after_start(self):
        starts=datetime(2026,10,16,13,tzinfo=timezone.utc)
        self.assertEqual(aion2_timers.notice_expires_at(starts).isoformat(),'2026-10-16T13:03:00+00:00')

    def test_only_playing_presence_counts_not_custom_status_other_game_or_bot(self):
        def member(name,kind=discord.ActivityType.playing,bot=False):
            return SimpleNamespace(bot=bot,activities=[SimpleNamespace(name=name,type=kind)])
        for name in ['AION 2','Aion2','AION Ⅱ','AION 2 (Global)','아이온2']:
            self.assertTrue(is_playing_aion2(member(name)))
        for m in [member('AION'),member('AION 20'),member('AION 2',discord.ActivityType.custom),member('AION 2',bot=True),SimpleNamespace(activities=[])]:
            self.assertFalse(is_playing_aion2(m))
        self.assertFalse(has_players(SimpleNamespace(members=[])))

    def test_shugo_suppressed_without_players_then_appears_and_is_removed_on_exit(self):
        async def check():
            channel=Channel()
            guild=SimpleNamespace(id=1,me=object(),get_channel=lambda _:channel)
            cog=Aion2(SimpleNamespace(user=SimpleNamespace(id=42)))
            settings_store.set_feature_channel(1,TIMERS_FEATURE,123,'output')
            now=datetime(2026,10,16,12,55,tzinfo=timezone.utc)
            with patch('fun_slesh.aion2.discord.TextChannel',Channel):
                await cog._publish_timers(guild,now)
                self.assertEqual(channel.send.await_count,1)
                self.assertFalse(any('шуго' in f.name for f in channel.messages[0].embeds[0].fields))
                channel.members=[SimpleNamespace(bot=False,activities=[discord.Game('AION 2')])]
                await cog._publish_timers(guild,now)
                notice=channel.messages[0]
                self.assertIn('шуго',notice.embeds[0].description)
                self.assertEqual(channel.send.await_count,2)
                channel.members=[]
                await cog._publish_timers(guild,now)
                notice.delete.assert_awaited_once()
                self.assertEqual(settings_store.get_feature_runtime_state(1,TIMERS_FEATURE)['pending_notices'],[])
        asyncio.run(check())

    def test_combined_rift_stays_without_shugo_and_updates_when_activity_changes(self):
        async def check():
            channel=Channel()
            guild=SimpleNamespace(id=1,me=object(),get_channel=lambda _:channel)
            cog=Aion2(SimpleNamespace(user=SimpleNamespace(id=42)))
            settings_store.set_feature_channel(1,TIMERS_FEATURE,123,'output')
            now=datetime(2026,10,16,11,55,tzinfo=timezone.utc)
            with patch('fun_slesh.aion2.discord.TextChannel',Channel):
                await cog._publish_timers(guild,now)
                notice=channel.messages[0]
                self.assertIn('Рифт',notice.embeds[0].description)
                self.assertNotIn('шуго',notice.embeds[0].description)
                channel.members=[SimpleNamespace(bot=False,activities=[discord.Game('AION 2')])]
                await cog._publish_timers(guild,now)
                self.assertIn('шуго',notice.embeds[0].description)
                channel.members=[]
                await cog._publish_timers(guild,now)
                self.assertNotIn('шуго',notice.embeds[0].description)
                self.assertEqual(channel.send.await_count,2)
                notice.delete.assert_not_awaited()
        asyncio.run(check())
