import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord

from core import game_profiles, lol_live_store as store, settings_store
from core.lol_live_service import LiveService, solo_rank
from core.riot_client import RiotApiError, RiotClient
from fun_slesh.lol_live import LiveRanks, game_embed


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(m,'SOCIAL_DB',str(Path(self.temp.name)/'test.db')) for m in (store,game_profiles,settings_store)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_opt_in_membership_and_durable_delivery(self):
        with self.assertRaises(ValueError):store.set_watch(1,2,True)
        game_profiles.upsert_game_account(2,'lol','riot','puuid','Name#TAG','ru',True)
        self.assertEqual(store.watched_accounts(1,[2]),[])
        store.set_watch(1,2,True)
        self.assertEqual(store.watched_accounts(1,[]),[])
        self.assertEqual(store.watched_accounts(1,[2])[0]['puuid'],'puuid')
        store.remember_delivery(1,'RU:123',3,4)
        store.remember_delivery(1,'RU:123',3,5)
        self.assertTrue(store.delivered(1,'RU:123'))
        self.assertFalse(store.delivered(2,'RU:123'))
        store.set_watch(1,2,False)
        self.assertEqual(store.watched_accounts(1,[2]),[])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_game_404_only(self):
        c=RiotClient('test')
        with patch.object(c,'_get',AsyncMock(side_effect=RiotApiError('not playing',404))):
            self.assertIsNone(await c.active_game('id'))
        with patch.object(c,'_get',AsyncMock(side_effect=RiotApiError('denied',403))):
            with self.assertRaises(RiotApiError):await c.active_game('id')

    async def test_auth_rate_limit_and_missing_key(self):
        self.assertFalse(LiveService('').ready())
        for status,retry in [(403,None),(429,300)]:
            s=LiveService('test')
            with self.assertRaises(RiotApiError):
                await s.request(NS(call=AsyncMock(side_effect=RiotApiError('error',status,retry))),'call')
            self.assertFalse(s.ready())
            self.assertTrue(s.last_error)

    async def test_cache_and_rank_failure_not_unranked(self):
        s=LiveService('test')
        s.request=AsyncMock(return_value=[])
        game={'participants':[{'puuid':'x','riotId':'Me#RU','teamId':100}]}
        self.assertEqual((await s.participants('ru',game))[0]['rank'],'Без ранга соло/дуо')
        await s.participants('ru',game)
        self.assertEqual(s.request.await_count,1)
        s._ranks.clear()
        s.request=AsyncMock(side_effect=RiotApiError('unavailable',503))
        self.assertEqual((await s.participants('ru',game))[0]['rank'],'Данные ранга недоступны')
        self.assertEqual(s.client('na1').routing.regional,'americas')

    async def test_tick_one_delivery_for_two_friends_and_restart(self):
        sent=[]
        async def history(**kwargs):
            for m in sent:yield m
        async def send(**kwargs):
            self.assertEqual(kwargs['allowed_mentions'].to_dict()['parse'],[])
            msg=NS(id=77,author=NS(id=99),embeds=[kwargs['embed']])
            sent.append(msg)
            return msg
        perms=NS(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)
        channel=NS(id=3,history=history,send=send,permissions_for=lambda m:perms)
        guild=NS(id=1,me=NS(),members=[NS(id=2,bot=False)],get_channel=lambda _:channel)
        live=LiveRanks(NS(user=NS(id=99)),'test')
        live.service.active_game=AsyncMock(return_value={'gameId':123})
        live.service.participants=AsyncMock(return_value=[dict(name='Name#RU',rank='Золото I',team=100)])
        accounts=[dict(platform='ru',puuid='a'),dict(platform='ru',puuid='b')]
        delivered=set()
        with patch.object(settings_store,'get_feature_policy',return_value=NS(enabled=True,output_channel_id=3)), \
             patch.object(store,'watched_accounts',return_value=accounts), \
             patch.object(store,'delivered',side_effect=lambda g,k:k in delivered), \
             patch.object(store,'remember_delivery',side_effect=lambda g,k,c,m:delivered.add(k)):
            await live.tick(guild)
            await live.tick(guild)
            self.assertEqual(len(sent),1)
            self.assertEqual(live.service.participants.await_count,1)
            delivered.clear()  # Send succeeded but database commit was interrupted.
            await live.tick(guild)
            self.assertEqual(len(sent),1)
            self.assertIn('RU:123',delivered)

    async def test_disabled_no_key_no_channel(self):
        for key,policy in [('',NS(enabled=True,output_channel_id=3)),('test',NS(enabled=False,output_channel_id=3)),('test',NS(enabled=True,output_channel_id=None))]:
            live=LiveRanks(NS(),key)
            live.service.active_game=AsyncMock()
            with patch.object(settings_store,'get_feature_policy',return_value=policy):
                await live.tick(NS(id=1,get_channel=lambda _:None))
            live.service.active_game.assert_not_awaited()

    def test_solo_only_and_embed(self):
        self.assertEqual(solo_rank([{'queueType':'RANKED_FLEX_SR','tier':'GOLD'}]),'Без ранга соло/дуо')
        self.assertIn('Алмаз II',solo_rank([{'queueType':'RANKED_SOLO_5x5','tier':'DIAMOND','rank':'II','leaguePoints':30}]))
        e=game_embed('RU:1',[dict(name='@everyone',rank='Бот',team=100)])
        self.assertEqual(e.footer.text,'LOL-LIVE RU:1')
        self.assertLess(len(e),6000)
