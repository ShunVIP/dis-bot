import asyncio
import copy
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import discord

from core import aion2_personal_store as store, aion2_personal_service as service, game_profiles, settings_store
from core import aion2_character_store as links, aion2_dps_service as dps
from core.db import connection
from fun_slesh.aion2 import Aion2View
from fun_slesh.aion2_ui.personal import PrivateView,ChecklistView,checklist_embed,progress_pages,players_pages,capture_progress
from fun_slesh.aion2_ui.dps import report_pages

NOW=datetime(2026,10,28,7,tzinfo=timezone.utc)
REF={'server_id':1314,'character_id':'PXxhXCm8rYfvXy-A6KS8VKiFIC467xLaq1bgTAedD1M='}
INFO={'profile':{'characterName':'Aliciá','characterLevel':45,'combatPower':1000},'stat':{'statList':[{'type':'ItemLevel','value':1400}]}}
BUNDLE={'info':INFO,'equipment':{'equipment':{'equipmentList':[{'id':123,'slotPos':1,'name':'Bow','enchantLevel':1,'slotPosName':'MainHand'}]}}}
REPORT={'record':{'bossName':'Boss','durationMs':1500,'totalDamage':9000,'actors':[{'actorId':1,'nickname':'Me***'},{'actorId':2,'nickname':'Other***'}],
         'details':{'skills':[{'actorId':1,'name':'Shot','dmg':6000},{'actorId':2,'name':'Spell','dmg':3000}]}}}


class PersonalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'social.db')
        self.patches=[patch.object(module,'SOCIAL_DB',self.path) for module in (store,game_profiles,settings_store)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_week_reset_boundary_and_stale_window(self):
        self.assertEqual(service.week_window(NOW)[0],NOW.isoformat())
        self.assertEqual(service.week_window(NOW-timedelta(seconds=1))[0],(NOW-timedelta(days=7)).isoformat())
        data=store.checklist(1,1,NOW)
        task=data['tasks'][0]['id']
        store.toggle_task(1,1,task,data['week'],NOW)
        self.assertTrue(store.checklist(1,1,NOW)['tasks'][0]['done'])
        self.assertFalse(store.checklist(1,1,NOW+timedelta(days=7))['tasks'][0]['done'])
        with self.assertRaises(ValueError):store.toggle_task(1,1,task,data['week'],NOW+timedelta(days=7))

    def test_checklist_owner_guild_caps_and_custom_tasks(self):
        a=store.checklist(1,1,NOW);b=store.checklist(1,2,NOW)
        with self.assertRaises(ValueError):store.toggle_task(1,2,a['tasks'][0]['id'],b['week'],NOW)
        with self.assertRaises(ValueError):store.delete_task(2,1,a['tasks'][0]['id'])
        for i in range(15):store.add_task(1,1,f'My {i}')
        with self.assertRaises(ValueError):store.add_task(1,1,'Overflow')
        tasks=store.checklist(1,1,NOW)['tasks']
        for row in tasks[1:]:store.delete_task(1,1,row['id'])
        with self.assertRaises(ValueError):store.delete_task(1,1,tasks[0]['id'])
        self.assertEqual(len(store.checklist(1,2,NOW)['tasks']),5)

    def test_snapshot_requires_own_link_deduplicates_and_is_bounded(self):
        data=service.character_snapshot(BUNDLE)
        with self.assertRaises(PermissionError):store.save_snapshot(1,REF,data)
        links.link(1,1,REF['server_id'],REF['character_id'],'Aliciá')
        self.assertTrue(store.save_snapshot(1,REF,data))
        self.assertFalse(store.save_snapshot(1,REF,data))
        with self.assertRaises(PermissionError):store.save_snapshot(2,REF,data)
        for i in range(35):store.save_snapshot(1,REF,{**data,'power':1001+i})
        self.assertEqual(len(store.snapshot_history(1,REF)),30)
        self.assertEqual(store.snapshot_history(2,REF),[])
        store.clear_history(2)
        self.assertEqual(len(store.snapshot_history(1,REF)),30)
        store.clear_history(1)
        self.assertEqual(store.snapshot_history(1,REF),[])
        self.assertEqual(len(links.list_links(1)),1)

    def test_incomplete_api_snapshot_not_saved_as_gear_loss(self):
        with self.assertRaises(ValueError):service.character_snapshot({'info':INFO,'equipment':None})
        before=service.character_snapshot(BUNDLE)
        after=copy.deepcopy(before);after['power']=1050;after['equipment']['1']['enchantLevel']=2
        metrics,gear=service.snapshot_changes(before,after)
        self.assertEqual(metrics[0][-1],50)
        self.assertEqual(len(gear),1)
        self.assertIsNone(service.number(float('nan')))
        self.assertIsNone(service.number(10**1000))

    def test_checklist_and_snapshot_survive_reopening_connection(self):
        data=store.checklist(1,1,NOW)
        store.toggle_task(1,1,data['tasks'][0]['id'],data['week'],NOW)
        with connection(self.path) as conn:self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0],'ok')
        self.assertTrue(store.checklist(1,1,NOW)['tasks'][0]['done'])


class DpsTests(unittest.TestCase):
    def test_public_url_allowlist(self):
        self.assertEqual(dps.report_id('https://a2tools.app/logs/OLjrfUNLMAvGG_KixUMGpQ?lang=ru'),'OLjrfUNLMAvGG_KixUMGpQ')
        for url in ['http://a2tools.app/logs/OLjrfUNLMAvGG_KixUMGpQ','https://evil.test/logs/OLjrfUNLMAvGG_KixUMGpQ',
                    'https://a2tools.app.evil.test/logs/OLjrfUNLMAvGG_KixUMGpQ','https://user@a2tools.app/logs/OLjrfUNLMAvGG_KixUMGpQ',
                    'https://a2tools.app/logs/../../secret','https://a2tools.app/logs/OLjrfUNLMAvGG_KixUMGpQ?token=private']:
            with self.assertRaises(ValueError):dps.report_id(url)

    def test_damage_dps_top_skills_and_incomplete_attribution(self):
        report=dps.normalize_report(REPORT)
        self.assertEqual(report['dps'],6000)
        self.assertEqual(report['actors'][0]['dps'],4000)
        self.assertEqual(report['actors'][0]['name'],'Me***')
        self.assertFalse(report['partial'])
        data=copy.deepcopy(REPORT);data['record']['totalDamage']=10000
        self.assertTrue(dps.normalize_report(data)['partial'])
        data['record']['durationMs']=0
        with self.assertRaises(ValueError):dps.normalize_report(data)

    def test_manual_lagofast_numbers_are_not_claimed_automatic(self):
        report=dps.manual_report('1,2m','2:00','Test')
        self.assertEqual(report['dps'],10000)
        self.assertIn('Ручной ввод',report_pages(report)[0].description)
        for duration in ['0','-5','2:99','nan','inf']:
            with self.assertRaises(ValueError):dps.manual_report('1000',duration)
        with self.assertRaises(ValueError):dps.manual_report('-100','10')


class PersonalUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_checklist_callback_and_progress_capture(self):
        fixture=PersonalStoreTests();fixture.setUp()
        try:
            interaction=SimpleNamespace(user=SimpleNamespace(id=1),guild_id=1,response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())
            data=store.checklist(1,1)
            view=ChecklistView(1,1,data)
            select=next(c for c in view.children if isinstance(c,discord.ui.Select))
            select._values=[str(data['tasks'][0]['id'])]
            await select.callback(interaction)
            self.assertTrue(store.checklist(1,1)['tasks'][0]['done'])
            links.link(1,1,REF['server_id'],REF['character_id'],'Aliciá')
            bundle=copy.deepcopy(BUNDLE)
            with patch('fun_slesh.aion2_ui.personal.budget'),patch('fun_slesh.aion2_ui.personal.api.load_character',return_value=bundle):
                await capture_progress(interaction,REF)
                bundle['info']['profile']['combatPower']=1100
                await capture_progress(interaction,REF)
            self.assertEqual(len(store.snapshot_history(1,REF)),2)
            self.assertIn('Прогресс',interaction.followup.send.call_args.kwargs['embed'].title)
            self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])
            view.stop()
        finally:fixture.tearDown()

    async def test_public_report_cache_and_refuses_redirects_or_private_report(self):
        calls=[]
        class Response:
            status=200
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            @property
            def content(self):return self
            async def iter_chunked(self,size):yield json.dumps(REPORT).encode()
        response=Response()
        class Session:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def get(self,url,**kwargs):calls.append((url,kwargs));return response
        url='https://a2tools.app/logs/OLjrfUNLMAvGG_KixUMGpQ'
        with patch.object(dps,'_cache',{}),patch.object(dps.aiohttp,'ClientSession',Session):
            result=await dps.public_report(url)
            result['actors'].clear()
            self.assertEqual(len((await dps.public_report(url))['actors']),2)
            self.assertEqual(len(calls),1)
            self.assertFalse(calls[0][1]['allow_redirects'])
        for status in [302,401,403,404]:
            response.status=status
            with patch.object(dps,'_cache',{}),patch.object(dps.aiohttp,'ClientSession',Session):
                with self.assertRaises(RuntimeError):await dps.public_report(url)

    async def test_four_entries_fit_existing_menu_and_views_are_owner_scoped(self):
        view=Aion2View(1)
        labels={getattr(c,'label',None) for c in view.children}
        self.assertTrue({'Кто играет','Мой прогресс','Мой чеклист','DPS / LagoFast'}<=labels)
        self.assertLessEqual(len(view.children),25)
        private=PrivateView(1,10)
        interaction=SimpleNamespace(user=SimpleNamespace(id=2),guild_id=10,response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await private.interaction_check(interaction))
        interaction.user.id=1;interaction.guild_id=11
        self.assertFalse(await private.interaction_check(interaction))
        interaction.guild_id=10
        self.assertTrue(await private.interaction_check(interaction))
        private.stop();view.stop()

    async def test_cards_are_readable_and_bounded(self):
        data={'tasks':[{'id':i,'label':'@everyone '+str(i),'done':False} for i in range(20)],'reset':NOW,'week':NOW.isoformat()}
        embed=checklist_embed(data)
        self.assertNotIn('@everyone',embed.description)
        self.assertLess(len(embed),6000)
        view=ChecklistView(1,10,data)
        self.assertEqual(len(next(c for c in view.children if isinstance(c,discord.ui.Select)).options),20)
        view.stop()
        snapshot=service.character_snapshot(BUNDLE)
        pages=progress_pages([{'captured_at':NOW.isoformat(),'data':snapshot}],True)
        pages+=report_pages(dps.normalize_report(REPORT))
        for page in pages:
            self.assertLess(len(page),6000)
            self.assertNotIn('```json',str(page.to_dict()))

    async def test_who_plays_excludes_hidden_other_games_and_checks_channel_access(self):
        players=[SimpleNamespace(id=1,bot=False,display_name='Friend',activities=[discord.Game('AION 2')]),
                 SimpleNamespace(id=2,bot=False,display_name='Other',activities=[discord.Game('Heroes III')]),
                 SimpleNamespace(id=3,bot=False,display_name='Hidden',activities=[])]
        channel=SimpleNamespace(members=players,permissions_for=lambda _:SimpleNamespace(view_channel=True))
        interaction=SimpleNamespace(guild_id=10,user=SimpleNamespace(id=1),guild=SimpleNamespace(get_channel=lambda _:channel),channel=channel)
        with patch('fun_slesh.aion2_ui.personal.discord.TextChannel',type(channel)),patch('fun_slesh.aion2_ui.personal.settings_store.get_feature_policy',return_value=SimpleNamespace(output_channel_id=100)),patch('fun_slesh.aion2_ui.personal.links.roster',return_value=[{'user_id':1,'name':'Aliciá','server_id':1314}]):
            pages=await players_pages(interaction)
            rendered=str([p.to_dict() for p in pages])
            self.assertIn('Friend',rendered);self.assertIn('Aliciá',rendered)
            self.assertNotIn('Other',rendered);self.assertNotIn('Hidden',rendered)
            channel.permissions_for=lambda _:SimpleNamespace(view_channel=False)
            with self.assertRaises(ValueError):await players_pages(interaction)
