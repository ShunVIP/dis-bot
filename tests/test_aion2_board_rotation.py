import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import discord

from core.aion2_timers import board_refresh_due
from fun_slesh.aion2_ui.boards import publish_board
from fun_slesh.aion2 import Aion2, TIMERS_FEATURE, BOSSES_STATE, BOSSES_MARKER
from core import aion2_timers, settings_store
from tests import test_aion2_delivery as delivery

NOW=datetime(2026,10,27,10,tzinfo=timezone.utc)
MARKER='AION2-EU timers'


def http_error():
    return discord.HTTPException(SimpleNamespace(status=403,reason='Forbidden'),'blocked')


class Channel:
    id=123

    def __init__(self):
        self.messages={}
        self.next_id=1
        self.events=[]
        self.send=AsyncMock(side_effect=self._send)

    def add(self,created_at,*,author=42,marker=MARKER,pinned=True):
        message=SimpleNamespace(id=self.next_id,author=SimpleNamespace(id=author),created_at=created_at,pinned=pinned,
                                embeds=[discord.Embed(title='Board').set_footer(text=marker)])
        self.next_id+=1
        async def edit(**kwargs):message.embeds=[kwargs['embed']]
        async def pin(**kwargs):
            self.events.append(('pin',message.id))
            message.pinned=True
        async def delete():
            self.events.append(('delete',message.id))
            self.messages.pop(message.id,None)
        message.edit=AsyncMock(side_effect=edit)
        message.pin=AsyncMock(side_effect=pin)
        message.delete=AsyncMock(side_effect=delete)
        self.messages[message.id]=message
        return message

    async def _send(self,**kwargs):
        message=self.add(NOW,pinned=False)
        message.embeds=[kwargs['embed']]
        self.events.append(('send',message.id))
        return message

    async def fetch_message(self,message_id):
        if message_id not in self.messages:
            raise discord.NotFound(SimpleNamespace(status=404,reason='Not Found'),'missing')
        return self.messages[message_id]

    async def history(self,limit):
        for message in list(self.messages.values())[::-1][:limit]:yield message


class BoardRotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.channel=Channel()
        self.old=self.channel.add(NOW-timedelta(days=8))
        self.state={'channel_id':123,'message_id':self.old.id,'pending_notices':[{'id':999}], 'last_notice_key':'keep'}
        self.persisted=None
        async def save(state):
            self.channel.events.append(('save',state['message_id']))
            self.persisted=copy.deepcopy(state)
        self.save=AsyncMock(side_effect=save)
        self.embed=discord.Embed(title='Updated board').set_footer(text=MARKER)

    async def publish(self,**kwargs):
        return await publish_board(self.channel,42,self.state,self.embed,MARKER,NOW,self.save,can_pin=kwargs.get('can_pin',True))

    def test_seven_days_boundary_not_last_edit_or_calendar_week(self):
        self.assertTrue(board_refresh_due(NOW-timedelta(days=7),NOW))
        self.assertFalse(board_refresh_due(NOW-timedelta(days=7,seconds=-1),NOW))
        self.assertFalse(board_refresh_due(NOW+timedelta(days=1),NOW))
        self.assertFalse(board_refresh_due('bad timestamp',NOW))

    async def test_rotation_pins_and_persists_before_deleting_old_and_preserves_other_state(self):
        new=await self.publish()
        self.assertNotEqual(new.id,self.old.id)
        self.assertTrue(new.pinned)
        self.assertNotIn(self.old.id,self.channel.messages)
        self.assertEqual(self.channel.events[:4],[('send',new.id),('pin',new.id),('save',new.id),('delete',self.old.id)])
        self.assertEqual(self.state['pending_notices'],[{'id':999}])
        self.assertEqual(self.state['last_notice_key'],'keep')
        self.assertEqual(self.persisted['retired_ids'],[])
        self.assertEqual(self.channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'],[])
        await self.publish()
        self.channel.send.assert_awaited_once()

    async def test_failed_pin_keeps_old_and_retries_same_replacement(self):
        original=self.channel._send
        async def fail_pin(**kwargs):
            message=await original(**kwargs)
            message.pin.side_effect=http_error()
            return message
        self.channel.send.side_effect=fail_pin
        new=await self.publish()
        self.assertIn(self.old.id,self.channel.messages)
        self.assertTrue(self.persisted['pin_pending'])
        self.assertEqual(self.persisted['retired_ids'],[self.old.id])
        new.pin.side_effect=None
        await self.publish()
        self.channel.send.assert_awaited_once()
        self.assertNotIn(self.old.id,self.channel.messages)

    async def test_failed_save_never_deletes_old_and_restart_recovers_sent_replacement(self):
        old_state=copy.deepcopy(self.state)
        self.save.side_effect=RuntimeError('storage unavailable')
        with self.assertRaises(RuntimeError):await self.publish()
        self.old.delete.assert_not_awaited()
        self.state=old_state
        self.save=AsyncMock()
        await self.publish()
        self.channel.send.assert_awaited_once()
        self.old.delete.assert_awaited_once()

    async def test_failed_delete_is_persisted_and_retried_without_resend(self):
        self.old.delete.side_effect=http_error()
        new=await self.publish()
        self.assertEqual(self.persisted['retired_ids'],[self.old.id])
        self.old.delete.side_effect=None
        await self.publish()
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.persisted['retired_ids'],[])
        self.assertEqual(self.state['message_id'],new.id)

    async def test_does_not_delete_user_posts_or_unrelated_bot_posts(self):
        user=self.channel.add(NOW-timedelta(days=8),author=7)
        unrelated=self.channel.add(NOW-timedelta(days=8),marker='Other feature')
        self.state['retired_ids']=[user.id,unrelated.id]
        await self.publish()
        user.delete.assert_not_awaited()
        unrelated.delete.assert_not_awaited()

    async def test_no_rotation_without_pin_permission(self):
        await self.publish(can_pin=False)
        self.channel.send.assert_not_awaited()
        self.old.delete.assert_not_awaited()

    async def test_missing_runtime_reference_recovers_old_board_and_rotates_once(self):
        self.state={}
        await self.publish()
        self.channel.send.assert_awaited_once()
        self.old.delete.assert_awaited_once()

    async def test_both_live_publishers_rotate_old_boards_in_canonical_runtime_state(self):
        fixture=delivery.Aion2DeliveryTests()
        fixture.setUp()
        try:
            self.channel.members=[]
            self.channel.permissions_for=lambda _:SimpleNamespace(view_channel=True,read_message_history=True,
                send_messages=True,embed_links=True,manage_messages=True)
            guild=SimpleNamespace(id=1,me=object(),get_channel=lambda _:self.channel)
            cog=Aion2(SimpleNamespace(user=SimpleNamespace(id=42)))
            settings_store.set_feature_channel(1,TIMERS_FEATURE,123,'output')
            settings_store.set_feature_runtime_state(1,TIMERS_FEATURE,{
                'channel_id':123,'message_id':self.old.id,'board_created_at':self.old.created_at.isoformat(),
                'dashboard_key':aion2_timers.snapshot(NOW)['shugo'].isoformat()+':shugo=0','pending_notices':[]})
            with patch('fun_slesh.aion2.discord.TextChannel',Channel):
                await cog._publish_timers(guild,NOW)
                self.old.delete.assert_awaited_once()
                timers_id=settings_store.get_feature_runtime_state(1,TIMERS_FEATURE)['message_id']
                self.assertNotEqual(timers_id,self.old.id)
                boss=self.channel.add(NOW-timedelta(days=9),marker=BOSSES_MARKER)
                settings_store.set_feature_runtime_state(1,BOSSES_STATE,{'channel_id':123,'message_id':boss.id})
                snapshot={'rows':[],'updated':int(NOW.timestamp()),'stale':False,'catalog_available':True}
                with patch('fun_slesh.aion2.aion2_boss_service.get_snapshot',return_value=snapshot),patch('fun_slesh.aion2.datetime') as clock:
                    clock.now.return_value=NOW
                    await cog._publish_bosses(guild)
                boss.delete.assert_awaited_once()
                self.assertNotEqual(settings_store.get_feature_runtime_state(1,BOSSES_STATE)['message_id'],boss.id)
                self.assertEqual(settings_store.get_feature_runtime_state(1,TIMERS_FEATURE)['message_id'],timers_id)
        finally:
            fixture.tearDown()
