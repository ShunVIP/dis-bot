import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import aion2_boss_service as service, settings_store
from fun_slesh.aion2 import Aion2, TIMERS_FEATURE, BOSSES_STATE
from fun_slesh.aion2_ui.bosses import boss_embed, BossesView
from tests import test_aion2_delivery as delivery

NOW=1792416367
CATALOG={'101001':{'name':'Нейкель востока','area':'Бертрон','side':'elyos','level':45},
         '111001':{'name':'Босс Асмо','area':'Альтгард','side':'asmodian','level':45}}
PAYLOAD={'format':1,'server':1314,'updated':NOW,'bosses':{
    '101001':{'state':'dead','nextSpawn':NOW+180,'reportedAt':NOW-30,'confirmed':True},
    '111001':{'state':'up','nextSpawn':None,'reportedAt':NOW-30,'confirmed':True}}}
CAT_SOURCE='const FIELD_BOSSES = '+json.dumps({'101001':{'name':'name','where':'map','side':'elyos'}})+';\nconst EVENT_TEXT = '+json.dumps({'name':{'ru':'Нейкель'},'map':{'ru':'Бертрон'}})+';'


class BossServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_is_data_not_executed_code(self):
        self.assertEqual(service.parse_catalog(CAT_SOURCE)['101001']['name'],'Нейкель')
        with self.assertRaises(ValueError):
            service.parse_catalog('const FIELD_BOSSES = evil();')

    def test_wrong_server_format_and_future_feed_are_rejected(self):
        for change in ({'server':1114},{'format':2},{'updated':NOW+999},{'bosses':[]}):
            with self.assertRaises(ValueError):service.normalize({**PAYLOAD,**change},CATALOG,NOW)

    def test_expired_timer_old_or_unconfirmed_sighting_never_claims_alive(self):
        snapshot=service.normalize(PAYLOAD,CATALOG,NOW)
        base=snapshot['rows'][0]
        self.assertEqual(service.status(base,snapshot,NOW)[0],'waiting')
        self.assertEqual(service.status(base,snapshot,NOW+181)[0],'unknown')
        for change in ({'reported_at':NOW-301},{'confirmed':False}):
            row={**base,'state':'up',**change}
            self.assertEqual(service.status(row,snapshot,NOW)[0],'unknown')
        self.assertEqual(service.status({**base,'state':'up'},snapshot,NOW)[0],'up')
        self.assertEqual(service.status(base,{**snapshot,'stale':True},NOW)[0],'unknown')

    def test_factions_and_readable_bounded_embeds(self):
        snapshot=service.normalize(PAYLOAD,CATALOG,NOW)
        self.assertEqual([r['id'] for r in service.ordered_rows(snapshot,'elyos',NOW)],['101001'])
        self.assertEqual([r['id'] for r in service.ordered_rows(snapshot,'asmodian',NOW)],['111001'])
        self.assertEqual(len(service.ordered_rows(snapshot,'all',NOW)),2)
        for compact in (True,False):
            embed,_=boss_embed(snapshot,compact=compact,now=NOW)
            self.assertLess(len(embed),6000)
            self.assertIn('Нейкель',embed.fields[0].name)
            self.assertNotIn('```',str(embed.to_dict()))
        self.assertIn('Нет свежих',boss_embed({**snapshot,'stale':True},now=NOW)[0].description)

    async def test_cache_no_auth_and_failure_marks_previous_data_stale(self):
        async def read(url,*args):
            return CAT_SOURCE if url==service.CATALOG_URL else json.dumps(PAYLOAD)
        with patch.multiple(service,_cache=None,_catalog=None,_catalog_at=0,_retry_at=0),patch.object(service.time,'time',return_value=NOW),patch.object(service,'_read',side_effect=read) as mocked:
            result=await service.get_snapshot()
            self.assertFalse(result['stale'])
            await service.get_snapshot()
            self.assertEqual(mocked.await_count,2)
            result['rows'].clear()
            self.assertEqual(len((await service.get_snapshot())['rows']),2)
            mocked.side_effect=ValueError('bad reply')
            with patch.object(service.time,'time',return_value=NOW+121):
                stale=await service.get_snapshot()
            self.assertTrue(stale['stale'])
            self.assertTrue(stale['error'])

    async def test_first_request_failure_is_explicit(self):
        with patch.multiple(service,_cache=None,_catalog=None,_catalog_at=0,_retry_at=0),patch.object(service,'_read',side_effect=ValueError('bad reply')):
            with self.assertRaises(RuntimeError):await service.get_snapshot()

    async def test_view_owner_and_pagination(self):
        snapshot=service.normalize(PAYLOAD,CATALOG,NOW)
        view=BossesView(1,snapshot)
        self.assertTrue(view.next.disabled)
        interaction=SimpleNamespace(user=SimpleNamespace(id=2),guild_id=1,response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        view.stop()


class BossBoardTests(unittest.TestCase):
    def test_single_board_edits_without_mentions_and_uses_separate_state(self):
        # Reuse the isolated canonical-store fixture, not the production database.
        fixture=delivery.Aion2DeliveryTests()
        fixture.setUp()
        try:
            async def check():
                channel=delivery.Channel()
                permissions=channel.permissions_for(None)
                permissions.manage_messages=True
                channel.permissions_for=lambda _:permissions
                original_send=channel._send
                async def send(**kwargs):
                    message=await original_send(**kwargs)
                    async def pin(**kwargs):
                        message.pinned=True
                    message.pin=AsyncMock(side_effect=pin)
                    return message
                channel.send.side_effect=send
                guild=SimpleNamespace(id=1,me=object(),get_channel=lambda _:channel)
                cog=Aion2(SimpleNamespace(user=SimpleNamespace(id=42)))
                settings_store.set_feature_channel(1,TIMERS_FEATURE,123,'output')
                with patch('fun_slesh.aion2.discord.TextChannel',delivery.Channel),patch('fun_slesh.aion2.aion2_boss_service.get_snapshot',return_value=service.normalize(PAYLOAD,CATALOG,NOW)):
                    await cog._publish_bosses(guild)
                    await cog._publish_bosses(guild)
                    channel.messages[0].pin.assert_awaited_once()
                    self.assertTrue(channel.messages[0].pinned)
                    self.assertEqual(channel.send.await_count,1)
                    self.assertEqual(channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'],[])
                    self.assertTrue(settings_store.get_feature_runtime_state(1,BOSSES_STATE)['message_id'])
                    self.assertEqual(settings_store.get_feature_runtime_state(1,TIMERS_FEATURE),{})
            asyncio.run(check())
        finally:
            fixture.tearDown()
