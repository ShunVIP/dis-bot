import asyncio
import errno
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core import admin_panel


class AdminStartupTests(unittest.TestCase):
    def test_missing_private_address_does_not_abort_discord_startup(self):
        async def check():
            runner=SimpleNamespace(setup=AsyncMock(),cleanup=AsyncMock())
            site=SimpleNamespace(start=AsyncMock(side_effect=OSError(errno.EADDRNOTAVAIL,"private address missing")))
            bot=SimpleNamespace()
            with patch.object(admin_panel,"WEB_ADMIN_ENABLED",True), patch.object(admin_panel.web,"AppRunner",return_value=runner), patch.object(admin_panel.web,"TCPSite",return_value=site):
                await admin_panel.start_admin_panel(bot,MagicMock())
            runner.cleanup.assert_awaited_once()
            self.assertIsNone(bot.admin_panel_runner)
            self.assertIn("private address missing",bot.admin_panel_unavailable_reason)
        asyncio.run(check())
