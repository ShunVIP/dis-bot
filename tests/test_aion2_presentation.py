import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fun_slesh.aion2_ui.characters import CharacterDataView
from fun_slesh.aion2_ui.presentation import effect, label, picture, section_pages


ICON='https://assets.playnccdn.com/static-aion2-gamedata/resources/bow.png'
ROW={'id':123,'name':'Spiritforged Bow','icon':ICON,'grade':'Unique','enchantLevel':10,'slotPosName':'MainHand'}


class CharacterPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_russian_labels_slots_and_numeric_effects(self):
        self.assertEqual(label('SubHand'),'Левая рука')
        self.assertEqual(label('아이템레벨'),'Уровень предметов')
        self.assertIn('Сопротивление критическому удару',effect('Critical Hit Resist increase +0.9%'))
        self.assertIn('+0.9%',effect('Critical Hit Resist increase +0.9%'))
        self.assertEqual(effect('Unknown Skill +2'),'Unknown Skill +2')

    async def test_every_normal_section_uses_embeds_not_json(self):
        examples={
            'stat':{'statList':[{'name':'Might','value':25,'statSecondList':['Attack increase +2.5%']}]},
            'title':{'ownedCount':167,'totalCount':297,'titleList':[{'name':'Champion','statList':[{'desc':'Attack +25'}]}]},
            'ranking':{'rankingList':[{'rankingName':'Arena','rank':12,'score':1500}]},
            'daevanion':{'boardList':[{'id':31,'name':'Nezekan','icon':ICON,'openNodeCount':52,'totalNodeCount':88}]},
            'equipment':{'equipmentList':[ROW],'skinList':[dict(ROW,name='Costume')]},
            'petwing':{'pet':dict(ROW,name='Swift Lugi',level=3),'wing':dict(ROW,name='Wings')},
            'skill':{'skillList':[dict(ROW,name='Snipe',skillLevel=14,acquired=1,equip=1)]},
        }
        bundle={'info':{'profile':{'characterName':'Test'},**{k:v for k,v in examples.items() if k in {'stat','title','ranking','daevanion'}}},
                'equipment':{k:v for k,v in examples.items() if k in {'equipment','petwing','skill'}}}
        view=CharacterDataView(1,1,bundle)
        picker=next(c for c in view.children if c.__class__.__name__=='SectionPicker')
        for key in examples:
            with self.subTest(section=key):
                picker._values=[key]
                response=SimpleNamespace(edit_message=AsyncMock())
                await picker.callback(SimpleNamespace(response=response))
                for page in view.pages:
                    payload=str(page.to_dict())
                    self.assertNotIn('```',payload)
                    self.assertNotIn('"statList"',payload)
                    self.assertLess(len(page),6000)
                self.assertEqual(view.prev.disabled,len(view.pages)<2)
        picker._values=['all']
        await picker.callback(SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock())))
        self.assertIn('```json',view.page_embed().description)
        view.stop()

    async def test_items_have_actual_icons_and_formatted_bonuses(self):
        data={**ROW,'mainStats':[{'name':'Attack','value':'223','extra':'50'}],
              'subStats':[{'name':'Might','value':'15'}], 'magicStoneStat':[{'name':'MP','value':'+15'}]}
        pages=section_pages('item',data)
        self.assertEqual(pages[0].thumbnail.url,ICON)
        rendered=' '.join(str(p.to_dict()) for p in pages)
        self.assertIn('Заточка',rendered)
        self.assertIn('Атака',rendered)
        self.assertIn('223',rendered)
        self.assertIn('усиление 50',rendered)
        self.assertIn('Магические камни',rendered)

    async def test_daevanion_shows_effects_and_nodes_without_empty_grid_cells(self):
        pages=section_pages('board',{'name':'Nezekan','icon':ICON,
            'openStatEffectList':[{'desc':'HP +500'}],
            'nodeList':[{'type':'None','open':0},{'type':'Stat','name':'Power','open':1,'effectList':[{'desc':'Attack +10'}]}]})
        rendered=' '.join(str(p.to_dict()) for p in pages)
        self.assertIn('Здоровье +500',rendered)
        self.assertIn('Power',rendered)
        self.assertIn('Открыто 1 из 1',rendered)
        self.assertNotIn('nodeList',rendered)

    async def test_empty_partial_and_large_sections_fit_discord(self):
        for key in ('stat','title','ranking','daevanion','equipment','petwing','skill','item','board'):
            self.assertTrue(section_pages(key,None))
        pages=section_pages('item',{'name':'x'*1000,'desc':'a'*15000,'mainStats':[{'name':'Attack','value':1}]*500})
        self.assertGreater(len(pages),1)
        for page in pages:
            self.assertLess(len(page),6000)
            self.assertLessEqual(len(page.title),256)
            for field in page.fields:
                self.assertLessEqual(len(field.value),1024)

    async def test_picture_allowlist_and_mentions(self):
        self.assertEqual(picture(ICON),ICON)
        for url in ('http://assets.playnccdn.com/a','https://evil.example/a','https://assets.playnccdn.com.evil.example/a','https://user@assets.playnccdn.com/a','https://['):
            self.assertIsNone(picture(url))
        page=section_pages('skill',{'skillList':[{'name':'@everyone','desc':'@here'}]})[0]
        self.assertNotIn('@everyone',page.title)
        self.assertNotIn('@here',str(page.to_dict()))
