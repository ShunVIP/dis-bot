import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import aion2_character_store as store, aion2_character_service as api, game_profiles
from fun_slesh.aion2_ui.characters import CharacterDataView, overview

CHAR_ID='PXxhXCm8rYfvXy-A6KS8VKiFIC467xLaq1bgTAedD1M='
REF={'server_id':1314,'character_id':CHAR_ID,'region':'eu'}
INFO={'profile':{'characterName':'Aliciá','serverId':1314,'serverName':'Daminu','characterLevel':45,'className':'Ranger','combatPower':67903},
      'stat':{'statList':[{'type':'ItemLevel','value':1414}]},'future_field':{'untouched':True},'daevanion':{'boardList':[{'id':31,'name':'Nezekan'}]}}
EQUIPMENT={'equipment':{'equipmentList':[{'id':123,'slotPos':1,'enchantLevel':10,'name':'Bow'}]},'skill':{'skillList':[]},'petwing':{}}


class CharacterStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(game_profiles,'SOCIAL_DB',str(Path(self.temp.name)/'social.db'))
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def test_multiple_links_primary_and_owner_scoped_deletion(self):
        store.link(1,1,1314,CHAR_ID,'Aliciá')
        store.link(1,1,1301,'otherCharacterId=','Alt')
        rows=store.list_links(1)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['name'],'Alt')
        self.assertTrue(rows[0]['primary'])
        self.assertFalse(any(r['verified'] for r in rows))
        store.change_link(1,1,rows[1]['id'])
        self.assertEqual(store.list_links(1)[0]['name'],'Aliciá')
        with self.assertRaises(PermissionError):
            store.change_link(2,1,rows[1]['id'],remove=True)
        with self.assertRaises(ValueError):
            store.change_link(2,2,rows[1]['id'],remove=True)
        self.assertEqual(len(store.list_links(1)),2)
        store.change_link(1,1,rows[0]['id'],remove=True)
        self.assertEqual(len(store.list_links(1)),1)

    def test_admin_can_link_others_without_claiming_verified_ownership(self):
        with self.assertRaises(PermissionError):
            store.link(1,2,1314,CHAR_ID,'Aliciá')
        store.link(1,2,1314,CHAR_ID,'Aliciá',is_admin=True)
        row=store.list_links(2)[0]
        self.assertFalse(row['verified'])
        self.assertEqual(store.roster([3]),[])
        self.assertEqual(store.roster([2])[0]['user_id'],2)
        store.change_link(1,2,row['id'],remove=True,is_admin=True)
        self.assertEqual(store.list_links(2),[])

    def test_duplicate_is_idempotent_and_capacity_is_bounded(self):
        for i in range(10): store.link(1,1,1314,f'character{i}=','Alt')
        store.link(1,1,1314,'character0=','Renamed')
        self.assertEqual(len(store.list_links(1)),10)
        with self.assertRaises(ValueError):
            store.link(1,1,1314,'extraCharacter=','Extra')


class CharacterApiTests(unittest.TestCase):
    def test_profile_is_readable_and_uses_only_nc_portrait(self):
        info=copy.deepcopy(INFO)
        info['profile'].update(raceName='Elyos',genderName='Female',regionName='BlackPhoenix',
                               titleName='Encountering Kaisinel’s Core',profileImage='https://profileimg.plaync.com/portrait.png')
        bundle={'info':info}
        embed=overview(bundle)
        self.assertIn('Aliciá',embed.title)
        self.assertIn('Дамину',embed.title)
        self.assertIn('Стрелок',embed.description)
        self.assertIn('67903',embed.description)
        fields={field.name:field.value for field in embed.fields}
        self.assertEqual(fields['Раса'],'Элийцы')
        self.assertEqual(fields['Легион'],'BlackPhoenix')
        self.assertEqual(embed.thumbnail.url,'https://profileimg.plaync.com/portrait.png')
        self.assertNotIn('characterId',str(embed.to_dict()))
        info['profile']['profileImage']='https://untrusted.example/portrait.png'
        self.assertFalse(overview(bundle).thumbnail.url)

    def test_profile_picker_does_not_render_json(self):
        async def check():
            bundle={'ref':REF,'info':INFO,'equipment':EQUIPMENT,'errors':{}}
            view=CharacterDataView(1,1,bundle)
            picker=next(child for child in view.children if child.__class__.__name__=='SectionPicker')
            picker._values=['profile']
            response=SimpleNamespace(edit_message=AsyncMock())
            await picker.callback(SimpleNamespace(response=response))
            embed=response.edit_message.call_args.kwargs['embed']
            self.assertNotIn('```',embed.description)
            self.assertIn('Aliciá',embed.title)
            self.assertEqual(view.pages,[])
            view.stop()
        asyncio.run(check())

    def test_refs_decode_once_and_reject_untrusted_ids(self):
        self.assertEqual(api.character_ref(1314,CHAR_ID.replace('=','%3D'))['character_id'],CHAR_ID)
        with self.assertRaises(ValueError): api.character_ref(1314,'https://evil.example')

    def test_search_maps_daminu_and_preserves_accents(self):
        async def check():
            async def fake(url,params):
                if url.endswith('servers'): return {'serverList':[{'serverId':1314,'serverName':'Daminu'}]}
                self.assertEqual(params['serverId'],1314)
                return {'list':[{'name':'<strong>Alici</strong>á','characterId':CHAR_ID,'serverId':1314,'level':45,'serverName':'Daminu'}],'pagination':{'total':1}}
            with patch.object(api,'_get',side_effect=fake):
                rows,total=await api.search_characters('Alici','Дамину')
            self.assertEqual(rows[0]['name'],'Aliciá'); self.assertEqual(total,1)
        asyncio.run(check())

    def test_full_export_keeps_all_fields_and_reports_partial_errors(self):
        async def check():
            async def fake(ref,kind,**kwargs):
                if kind=='info': return copy.deepcopy(INFO)
                if kind=='equipment': return copy.deepcopy(EQUIPMENT)
                if kind=='item': return {'id':123,'arbitraryStat':[1,2]}
                raise RuntimeError('board unavailable')
            with patch.object(api,'section',side_effect=fake):
                bundle=await api.load_character(REF)
                export=await api.full_export(bundle)
            self.assertEqual(export['info']['future_field'],{'untouched':True})
            self.assertEqual(export['item_details']['1']['arbitraryStat'],[1,2])
            self.assertEqual(export['errors']['board_31'],'board unavailable')
            self.assertEqual(bundle['errors'],{})
        asyncio.run(check())

    def test_equipment_failure_does_not_hide_profile(self):
        async def check():
            async def fake(ref,kind,**kwargs):
                if kind=='info': return INFO
                raise RuntimeError('maintenance')
            with patch.object(api,'section',side_effect=fake): bundle=await api.load_character(REF)
            self.assertEqual(bundle['info'],INFO)
            self.assertIsNone(bundle['equipment'])
            self.assertIn('equipment',bundle['errors'])
        asyncio.run(check())

    def test_discord_sections_paginate_without_losing_fields(self):
        async def check():
            bundle={'ref':REF,'info':INFO,'equipment':EQUIPMENT,'errors':{}}
            view=CharacterDataView(1,2,bundle,can_manage=False)
            self.assertTrue(view.link_button.disabled)
            view.set_pages('All',{'large':'x'*9000})
            self.assertGreater(len(view.pages),1)
            self.assertIn('1414',overview(bundle).description)
            for page in range(len(view.pages)):
                view.page=page
                self.assertLess(len(view.page_embed().description),4096)
        asyncio.run(check())
