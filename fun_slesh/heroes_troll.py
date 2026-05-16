# -*- coding: utf-8 -*-
"""
Троллинг игроков Heroes of Might and Magic по Discord activity.

Срабатывает, когда участник запускает любую часть Heroes of Might and Magic,
включая Heroes of Might and Magic: Olden Era.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

UTC = timezone.utc
COOLDOWN_HOURS = 4

HEROES_PATTERNS = (
    "heroes of might and magic",
    "heroes of might & magic",
    "might and magic heroes",
    "might & magic heroes",
    "homm",
    "olden era",
    "heroes iii",
    "heroes iv",
    "heroes v",
    "heroes vi",
    "heroes vii",
    "heroes 3",
    "heroes 4",
    "heroes 5",
    "heroes 6",
    "heroes 7",
)

GENERIC_TROLLS = [
    "🎺 <@{user_id}> снова ушёл двигать пиксельных рыцарей по клеточкам. Традиции уважаю.",
    "🪦 <@{user_id}> опять запускает Heroes. Современные игры снова проиграли некромантии.",
    "🏰 <@{user_id}> выбрал Heroes. Ещё один вечер, когда инициативу считают важнее сна.",
    "🐴 <@{user_id}> снова ускакал в Heroes. Если пропадёт, ищите его между таверной и шахтой руды.",
    "📜 <@{user_id}> включил Heroes. Значит, ближайшие часы будут крики про идеальный старт и сломанный баланс.",
]

OLDEN_ERA_TROLLS = [
    "🕯️ <@{user_id}> запустил Olden Era. Красиво, конечно, но душой он всё равно в тройке.",
    "⚔️ <@{user_id}> ушёл в Olden Era. Проверяет, можно ли сделать новый Heroes и не разбудить древние споры.",
    "🏹 <@{user_id}> играет в Olden Era. Исторический момент: ностальгия официально получила современную обёртку.",
    "📯 <@{user_id}> открыл Olden Era. Ставлю на то, что через 10 минут он уже сравнивает её с Heroes III.",
]


def _normalize_name(name: str) -> str:
    return " ".join((name or "").lower().replace(":", " ").replace("-", " ").split())


def _is_heroes_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return any(pattern in normalized for pattern in HEROES_PATTERNS)


def _is_olden_era(name: str) -> bool:
    return "olden era" in _normalize_name(name)


def _extract_game_names(activities: tuple[discord.BaseActivity, ...] | list[discord.BaseActivity]) -> set[str]:
    names: set[str] = set()
    for activity in activities or []:
        name = getattr(activity, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def _find_started_heroes(before_names: set[str], after_names: set[str]) -> str | None:
    for name in sorted(after_names):
        if name in before_names:
            continue
        if _is_heroes_name(name):
            return name
    return None


def _pick_channel(guild: discord.Guild) -> discord.TextChannel | None:
    me = guild.me
    if guild.system_channel and me:
        perms = guild.system_channel.permissions_for(me)
        if perms.send_messages and perms.view_channel:
            return guild.system_channel

    for channel in guild.text_channels:
        if not me:
            continue
        perms = channel.permissions_for(me)
        if perms.send_messages and perms.view_channel:
            return channel
    return None


class HeroesTroll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], datetime] = {}

    def _ready_for_troll(self, guild_id: int, user_id: int) -> bool:
        key = (guild_id, user_id)
        now = datetime.now(UTC)
        last = self._cooldowns.get(key)
        if last and now - last < timedelta(hours=COOLDOWN_HOURS):
            return False
        self._cooldowns[key] = now
        return True

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        started = _find_started_heroes(_extract_game_names(before.activities), _extract_game_names(after.activities))
        if not started:
            return
        if not self._ready_for_troll(after.guild.id, after.id):
            return

        channel = _pick_channel(after.guild)
        if channel is None:
            return

        pool = OLDEN_ERA_TROLLS if _is_olden_era(started) else GENERIC_TROLLS
        message = pool[0] if len(pool) == 1 else pool[hash((after.id, started, datetime.now(UTC).hour)) % len(pool)]
        message = message.format(user_id=after.id)
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HeroesTroll(bot))
