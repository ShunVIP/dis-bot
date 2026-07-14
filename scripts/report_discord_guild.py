from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

from config import TOKEN


async def main(guild_id: int) -> None:
    if not TOKEN:
        raise SystemExit("Discord bot token is not configured")
    headers = {"Authorization": f"Bot {TOKEN}"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(f"https://discord.com/api/v10/guilds/{guild_id}") as response:
            if response.status >= 400:
                raise SystemExit(f"Discord guild lookup failed: HTTP {response.status}")
            data = await response.json()
    print(json.dumps({
        "id": int(data["id"]),
        "name": data.get("name") or "",
        "owner_id": int(data["owner_id"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("usage: python -m scripts.report_discord_guild GUILD_ID")
    asyncio.run(main(int(sys.argv[1])))
