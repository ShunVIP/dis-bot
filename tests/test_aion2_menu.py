import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fun_slesh.menu import MenuView


class Aion2MenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_games_section_has_direct_working_aion_button(self):
        hub = AsyncMock(spec=lambda interaction: None)
        bot = SimpleNamespace(get_cog=lambda name: SimpleNamespace(open_hub=hub) if name == "Aion2" else None)
        view = MenuView(bot, "🕹️ Игры", {"🕹️ Игры": []}, admin_only=False)
        buttons = [item for item in view.children if getattr(item, "label", None) == "Aion 2"]
        self.assertEqual(len(buttons), 1)
        interaction = SimpleNamespace()
        await buttons[0].callback(interaction)
        hub.assert_awaited_once_with(interaction)
        view.stop()
