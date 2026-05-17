# -*- coding: utf-8 -*-
"""
Троллинг игроков Heroes of Might and Magic по Discord activity.

Срабатывает, когда участник запускает любую часть Heroes of Might and Magic,
включая Heroes of Might and Magic: Olden Era.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

UTC = timezone.utc
HOUR = timedelta(hours=1)

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
    "🧙 <@{user_id}> снова открыл Heroes. Где-то тихо заплакала ещё одна современная AAA-игра.",
    "⛏️ <@{user_id}> ушёл добывать руду в Heroes. Человек буквально выбрал экономику 1999 года.",
    "🐉 <@{user_id}> вернулся в Heroes. Нормальные люди отдыхают, этот считает мувпойнты.",
    "🎲 <@{user_id}> снова в Heroes. Сейчас начнётся священная война за лучший замок и правильный билд героя.",
    "🏹 <@{user_id}> запустил Heroes. Похоже, вечер официально посвящён гремлинам, скелетам и ошибкам маршрута.",
]

OLDEN_ERA_TROLLS = [
    "🕯️ <@{user_id}> запустил Olden Era. Красиво, конечно, но душой он всё равно в тройке.",
    "⚔️ <@{user_id}> ушёл в Olden Era. Проверяет, можно ли сделать новый Heroes и не разбудить древние споры.",
    "🏹 <@{user_id}> играет в Olden Era. Исторический момент: ностальгия официально получила современную обёртку.",
    "📯 <@{user_id}> открыл Olden Era. Ставлю на то, что через 10 минут он уже сравнивает её с Heroes III.",
    "🗿 <@{user_id}> включил Olden Era. Посмотрим, сколько минут пройдёт до фразы «а в тройке было лучше».",
    "🧾 <@{user_id}> ушёл в Olden Era. Похоже, сегодня опять будет аудит чужой ностальгии.",
    "🏰 <@{user_id}> снова тестирует Olden Era. Сервер ждёт экспертный вердикт в жанре «норм, но дух не тот».",
]

GENERIC_HOURLY_TROLLS = [
    "⏰ <@{user_id}> уже **{hours} ч.** сидит в Heroes. Это уже не матч, это образ жизни.",
    "📉 **{hours} ч.** в Heroes у <@{user_id}>. Производительность офлайн, тактика онлайн.",
    "🪦 <@{user_id}> всё ещё в Heroes уже **{hours} ч.**. Семья, работа, солнечный свет проигрывают.",
    "🛏️ **{hours} ч.** в Heroes. <@{user_id}> официально перешёл в пошаговый режим жизни.",
    "📚 <@{user_id}> уже **{hours} ч.** доказывает, что один ход может длиться бесконечно.",
    "💀 Heroes держит <@{user_id}> уже **{hours} ч.** Если что, это уже полноценная экспедиция.",
]

OLDEN_ERA_HOURLY_TROLLS = [
    "⏰ <@{user_id}> уже **{hours} ч.** в Olden Era. Рецензия явно пишется кровью и ностальгией.",
    "🧓 **{hours} ч.** в Olden Era у <@{user_id}>. Где-то рядом уже созрел новый тезис про Heroes III.",
    "📯 <@{user_id}> не выходит из Olden Era уже **{hours} ч.**. Экспертиза по древним спорам набирает обороты.",
    "🪦 Olden Era держит <@{user_id}> уже **{hours} ч.** Ещё немного и он начнёт сравнивать анимации покадрово.",
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


def _find_active_heroes(names: set[str]) -> str | None:
    for name in sorted(names):
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
        self._active_sessions: dict[tuple[int, int], dict[str, object]] = {}
        self._hourly_presence_check.start()

    def cog_unload(self):
        self._hourly_presence_check.cancel()

    def _remember_session(self, guild_id: int, user_id: int, game_name: str):
        now = datetime.now(UTC)
        self._active_sessions[(guild_id, user_id)] = {
            "game_name": game_name,
            "started_at": now,
            "last_hourly_hours": 0,
        }

    def _drop_session(self, guild_id: int, user_id: int):
        self._active_sessions.pop((guild_id, user_id), None)

    def _pick_message(self, user_id: int, game_name: str, pool: list[str], *, hours: int | None = None) -> str:
        base = pool[hash((user_id, game_name, hours or 0, datetime.now(UTC).hour)) % len(pool)]
        payload = {"user_id": user_id}
        if hours is not None:
            payload["hours"] = hours
        return base.format(**payload)

    async def _send_troll(self, guild: discord.Guild, user_id: int, game_name: str, *, hours: int | None = None):
        channel = _pick_channel(guild)
        if channel is None:
            return
        olden = _is_olden_era(game_name)
        if hours is None:
            pool = OLDEN_ERA_TROLLS if olden else GENERIC_TROLLS
        else:
            pool = OLDEN_ERA_HOURLY_TROLLS if olden else GENERIC_HOURLY_TROLLS
        message = self._pick_message(user_id, game_name, pool, hours=hours)
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        except Exception:
            pass

    @tasks.loop(minutes=10)
    async def _hourly_presence_check(self):
        now = datetime.now(UTC)
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                active = _find_active_heroes(_extract_game_names(member.activities))
                key = (guild.id, member.id)
                if not active:
                    self._drop_session(guild.id, member.id)
                    continue

                session = self._active_sessions.get(key)
                if session is None:
                    self._remember_session(guild.id, member.id, active)
                    continue

                session["game_name"] = active
                started_at = session["started_at"]
                hours = int((now - started_at) // HOUR)
                if hours <= 0:
                    continue
                if hours <= int(session.get("last_hourly_hours", 0)):
                    continue

                session["last_hourly_hours"] = hours
                await self._send_troll(guild, member.id, active, hours=hours)

    @_hourly_presence_check.before_loop
    async def _before_hourly_presence_check(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        before_names = _extract_game_names(before.activities)
        after_names = _extract_game_names(after.activities)
        started = _find_started_heroes(before_names, after_names)
        active_after = _find_active_heroes(after_names)

        if not active_after:
            self._drop_session(after.guild.id, after.id)
            return

        if not started:
            if (after.guild.id, after.id) not in self._active_sessions:
                self._remember_session(after.guild.id, after.id, active_after)
            return
        self._remember_session(after.guild.id, after.id, started)
        await self._send_troll(after.guild, after.id, started)


async def setup(bot: commands.Bot):
    await bot.add_cog(HeroesTroll(bot))
