import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from core.heroes_service import extract_game_names
from fun_slesh.heroes_troll import HeroesTroll

REAL_SLEEP = asyncio.sleep


class HeroesPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch('fun_slesh.heroes_troll.ensure_heroes_storage'),
                        patch('fun_slesh.heroes_troll.load_active_sessions',return_value=[]),
                        patch('fun_slesh.heroes_troll.remember_active_session'),
                        patch('fun_slesh.heroes_troll.pop_active_session')]
        self.mocks = [p.start() for p in self.patches]
        self.guild = SimpleNamespace(id=1,get_member=lambda _:self.current)
        self.bot = SimpleNamespace(get_guild=lambda _:self.guild,wait_until_ready=AsyncMock())
        self.current = self.member()
        self.cog = HeroesTroll(self.bot)
        self.cog._send_troll = AsyncMock()
        self.cog._save_finished_session = Mock()
        self.gate = asyncio.Event()
        async def wait(seconds):
            self.assertEqual(seconds,900)
            await self.gate.wait()
        self.sleep_patch = patch('fun_slesh.heroes_troll.asyncio.sleep',side_effect=wait)
        self.sleep_patch.start()

    async def asyncTearDown(self):
        self.cog.cog_unload()
        await REAL_SLEEP(0)
        self.sleep_patch.stop()
        for p in reversed(self.patches):p.stop()

    def member(self,*games):
        return SimpleNamespace(id=2,bot=False,guild=self.guild,activities=[discord.Game(n) for n in games])

    async def change(self,*games):
        before=self.current
        self.current=self.member(*games)
        await self.cog.on_presence_update(before,self.current)
        await REAL_SLEEP(0)

    async def test_repeated_alt_tab_to_another_game_is_one_session(self):
        await self.change('Heroes III')
        started=self.cog._active_sessions[(1,2)]['started_at']
        for _ in range(10):
            await self.change('AION 2')
            await self.change('Heroes III')
        self.assertEqual(self.cog._send_troll.await_count,1)
        self.assertEqual(self.cog._active_sessions[(1,2)]['started_at'],started)
        self.assertEqual(self.mocks[2].call_count,1)
        self.cog._save_finished_session.assert_not_called()
        self.assertEqual(self.cog._pending_exits,{})

    async def test_brief_empty_presence_is_cancelled_on_return(self):
        await self.change('Heroes III')
        await self.change()
        task=self.cog._pending_exits[(1,2)]
        await self.change('Heroes III')
        self.assertTrue(task.cancelled())
        self.assertEqual(self.cog._send_troll.await_count,1)
        self.cog._save_finished_session.assert_not_called()

    async def test_other_game_cancels_exit_even_if_heroes_stays_hidden(self):
        await self.change('Heroes III')
        await self.change()
        task=self.cog._pending_exits[(1,2)]
        await self.change('League of Legends')
        self.assertTrue(task.cancelled())
        self.assertIn((1,2),self.cog._active_sessions)
        self.assertEqual(self.cog._send_troll.await_count,1)

    async def test_confirmed_absence_ends_once_and_excludes_grace_from_duration(self):
        await self.change('Heroes III')
        started=datetime.now(timezone.utc)-timedelta(minutes=20)
        self.cog._active_sessions[(1,2)]['started_at']=started
        await self.change()
        task=self.cog._pending_exits[(1,2)]
        await self.change()  # Duplicate empty update must not reset the deadline.
        self.assertIs(self.cog._pending_exits[(1,2)],task)
        self.gate.set()
        await task
        self.assertNotIn((1,2),self.cog._active_sessions)
        self.assertEqual(self.cog._send_troll.await_count,2)
        self.cog._save_finished_session.assert_called_once()
        ended=self.cog._save_finished_session.call_args.args[-1]
        self.assertLess((ended-started).total_seconds(),1202)
        self.assertTrue(self.cog._send_troll.call_args.kwargs['ended'])
        await self.change()
        self.assertEqual(self.cog._send_troll.await_count,2)

    async def test_final_check_uses_latest_cache_not_only_event_snapshot(self):
        await self.change('Heroes III')
        await self.change()
        task=self.cog._pending_exits[(1,2)]
        self.current=self.member('Heroes III')  # Cache recovered before its listener ran.
        self.gate.set()
        await task
        self.assertIn((1,2),self.cog._active_sessions)
        self.cog._save_finished_session.assert_not_called()

    async def test_activity_name_change_and_multiple_games_do_not_restart(self):
        await self.change('Heroes III')
        await self.change('Heroes of Might & Magic III','AION 2')
        await self.change('AION 2','Heroes of Might & Magic III')
        self.assertEqual(self.cog._send_troll.await_count,1)
        self.assertEqual(self.mocks[2].call_count,1)

    async def test_restart_reconciliation_uses_same_grace_and_unload_cancels(self):
        await self.change('Heroes III')
        self.current=self.member('AION 2')
        await self.cog._reconcile_active_sessions()
        self.assertFalse(self.cog._pending_exits)
        self.current=self.member()
        await self.cog._reconcile_active_sessions()
        task=self.cog._pending_exits[(1,2)]
        self.cog._save_finished_session.assert_not_called()
        self.cog.cog_unload()
        await REAL_SLEEP(0)
        self.assertTrue(task.cancelled())

    def test_non_game_activities_do_not_count_as_a_running_game(self):
        activities=[discord.Game('Heroes III'),SimpleNamespace(name='Heroes V',type=discord.ActivityType.custom),
                    SimpleNamespace(name='Song',type=discord.ActivityType.listening)]
        self.assertEqual(extract_game_names(activities),{'Heroes III'})
