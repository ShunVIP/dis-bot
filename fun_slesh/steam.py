# -*- coding: utf-8 -*-
# fun_slesh/steam.py
"""
Steam интеграция:
  /стим_привязать   — привязать Steam профиль (URL / ник / SteamID64)
  /стим_отвязать    — отвязать профиль
  /стим             — посмотреть статистику (своё или чужое)
  /стим_вишлист     — вишлист участника
  /стим_общие       — общие игры с другим участником
  /релизы_проверить — (Админ) запустить проверку релизов/скидок вручную
  /релизы_канал     — (Админ) куда постить уведомления о релизах/скидках

Планировщик: каждые 6 часов проверяет вишлисты всех привязанных — если игра вышла
или скидка ≥ настроенного порога → постит в канал.
"""

import os, sqlite3, json, re
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
UTC     = timezone.utc

STEAM_API_BASE = "https://api.steampowered.com"
STORE_API_BASE = "https://store.steampowered.com/api"

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ── БД ────────────────────────────────────────────────────────────────────────
def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS steam_profiles (
                user_id    INTEGER PRIMARY KEY,
                steam_id   TEXT    NOT NULL,
                added_at   TEXT    NOT NULL
            );

            -- Конфиг уведомлений по гильдии
            CREATE TABLE IF NOT EXISTS steam_config (
                guild_id         INTEGER PRIMARY KEY,
                notify_channel   INTEGER,
                discount_min_pct INTEGER NOT NULL DEFAULT 50
            );

            -- Кэш вишлистов: appid → last_price / release_date
            CREATE TABLE IF NOT EXISTS steam_wishlist_cache (
                user_id    INTEGER NOT NULL,
                appid      INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                released   INTEGER NOT NULL DEFAULT 0,
                discount   INTEGER NOT NULL DEFAULT 0,
                price_rub  INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT    NOT NULL,
                PRIMARY KEY (user_id, appid)
            );
        """)


# ── Steam ID resolution ───────────────────────────────────────────────────────
async def _resolve_steam_id(query: str, api_key: str) -> str | None:
    """
    Принимает SteamID64, /id/vanity_url или /profiles/steamid64 ссылку.
    Возвращает SteamID64 строкой или None.
    """
    query = query.strip()

    # Уже SteamID64
    if re.match(r'^\d{17}$', query):
        return query

    # URL вида https://steamcommunity.com/profiles/76561198...
    m = re.search(r'/profiles/(\d{17})', query)
    if m:
        return m.group(1)

    # URL вида https://steamcommunity.com/id/vanityname
    m = re.search(r'/id/([^/?\s]+)', query)
    if m:
        vanity = m.group(1)
    else:
        # Считаем что это vanity URL напрямую
        vanity = query

    # Резолвим через API
    url = f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params={"key": api_key, "vanityurl": vanity},
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            data = await r.json()
    if data.get("response", {}).get("success") == 1:
        return data["response"]["steamid"]
    return None


async def _get_player_summary(steam_id: str, api_key: str) -> dict | None:
    url = f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params={"key": api_key, "steamids": steam_id},
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            data = await r.json()
    players = data.get("response", {}).get("players", [])
    return players[0] if players else None


async def _get_owned_games(steam_id: str, api_key: str) -> list[dict]:
    url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": api_key, "steamid": steam_id,
        "include_appinfo": 1, "include_played_free_games": 1
    }
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params,
                         timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return []
            data = await r.json()
    return data.get("response", {}).get("games", [])


async def _get_wishlist(steam_id: str) -> dict:
    """Возвращает словарь appid → {name, priority, ...}."""
    url = f"https://store.steampowered.com/wishlist/profiles/{steam_id}/wishlistdata/"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return {}
            try:
                return await r.json(content_type=None)
            except Exception:
                return {}


async def _get_app_details(appid: int) -> dict | None:
    url = f"{STORE_API_BASE}/appdetails"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params={"appids": appid, "cc": "ru", "l": "russian"},
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
    entry = data.get(str(appid), {})
    if not entry.get("success"):
        return None
    return entry.get("data")


def _get_api_key() -> str:
    """Берём ключ из config.py или переменной окружения."""
    try:
        from config import STEAM_API_KEY
        return STEAM_API_KEY
    except ImportError:
        pass
    return os.environ.get("STEAM_API_KEY", "")


def _fmt_minutes(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    if h == 0:
        return f"{m}м"
    return f"{h}ч {m}м" if m else f"{h}ч"


# ── Проверка релизов / скидок ─────────────────────────────────────────────────
async def _check_releases(bot: commands.Bot):
    api_key = _get_api_key()
    if not api_key:
        return

    with sqlite3.connect(DB_PATH) as conn:
        profiles  = conn.execute("SELECT user_id, steam_id FROM steam_profiles").fetchall()
        guild_cfgs = conn.execute(
            "SELECT guild_id, notify_channel, discount_min_pct FROM steam_config"
            " WHERE notify_channel IS NOT NULL"
        ).fetchall()

    if not guild_cfgs:
        return

    now_str = datetime.now(UTC).isoformat()

    for user_id, steam_id in profiles:
        wishlist = await _get_wishlist(steam_id)
        if not wishlist:
            continue

        for appid_str, info in list(wishlist.items())[:50]:  # лимит 50 игр
            try:
                appid = int(appid_str)
            except ValueError:
                continue

            name = info.get("name", f"App {appid}")

            # Тянем детали из стора
            details = await _get_app_details(appid)
            if not details:
                continue

            released  = 1 if not details.get("release_date", {}).get("coming_soon", True) else 0
            price_data = details.get("price_overview", {})
            discount   = price_data.get("discount_percent", 0)
            price_rub  = price_data.get("final", 0)  # в копейках

            with sqlite3.connect(DB_PATH) as conn:
                old = conn.execute(
                    "SELECT released, discount FROM steam_wishlist_cache"
                    " WHERE user_id=? AND appid=?",
                    (user_id, appid)
                ).fetchone()

                conn.execute(
                    "INSERT INTO steam_wishlist_cache"
                    "(user_id,appid,name,released,discount,price_rub,checked_at)"
                    " VALUES(?,?,?,?,?,?,?)"
                    " ON CONFLICT(user_id,appid) DO UPDATE SET"
                    " released=excluded.released, discount=excluded.discount,"
                    " price_rub=excluded.price_rub, checked_at=excluded.checked_at",
                    (user_id, appid, name, released, discount, price_rub, now_str)
                )

            # Определяем событие
            event = None
            if released and old and not old[0]:
                event = ("release", f"🚀 **{name}** вышла!")
            elif discount > 0:
                for _, notify_ch, min_pct in guild_cfgs:
                    if discount >= min_pct:
                        if not old or old[1] < min_pct:
                            price_fmt = f"{price_rub // 100}₽" if price_rub else "бесплатно"
                            event = ("discount",
                                     f"🏷️ **{name}** — скидка **{discount}%** · {price_fmt}")
                        break

            if not event:
                continue

            # Постим во все гильдии где есть notify_channel
            for guild_id, notify_ch_id, min_pct in guild_cfgs:
                if event[0] == "discount" and discount < min_pct:
                    continue
                ch = bot.get_channel(notify_ch_id)
                if not ch:
                    continue
                # Проверяем что этот user_id есть на сервере
                guild  = bot.get_guild(guild_id)
                member = guild.get_member(user_id) if guild else None
                if not member:
                    continue

                store_url = f"https://store.steampowered.com/app/{appid}"
                emb = discord.Embed(
                    title=event[1],
                    url=store_url,
                    color=discord.Color.green() if event[0] == "release"
                          else discord.Color.gold()
                )
                emb.add_field(name="В вишлисте у", value=member.mention, inline=True)
                if details.get("header_image"):
                    emb.set_thumbnail(url=details["header_image"])
                try:
                    await ch.send(embed=emb)
                except Exception:
                    pass


# ── Cog ───────────────────────────────────────────────────────────────────────
class Steam(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        if not scheduler.running:
            scheduler.start()
        scheduler.add_job(
            _check_releases, "interval", hours=6,
            args=[bot], id="steam_releases", replace_existing=True
        )

    # ── /стим_привязать ───────────────────────────────────────────────────────
    @app_commands.command(name="стим_привязать",
                          description="Привязать Steam профиль")
    @app_commands.describe(
        профиль="Ссылка на профиль, vanity-URL или SteamID64"
    )
    async def стим_привязать(self, interaction: discord.Interaction, профиль: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        api_key = _get_api_key()
        if not api_key:
            await interaction.followup.send(
                "❌ Steam API ключ не настроен. Добавь `STEAM_API_KEY` в `config.py`.",
                ephemeral=True)
            return

        steam_id = await _resolve_steam_id(профиль, api_key)
        if not steam_id:
            await interaction.followup.send(
                "❌ Не удалось найти профиль. Попробуй:\n"
                "• Ссылку: `https://steamcommunity.com/id/username`\n"
                "• Или SteamID64 (17 цифр)",
                ephemeral=True)
            return

        player = await _get_player_summary(steam_id, api_key)
        if not player:
            await interaction.followup.send(
                "❌ Профиль найден, но недоступен (закрытый?)", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO steam_profiles(user_id, steam_id, added_at) VALUES(?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET steam_id=excluded.steam_id, added_at=excluded.added_at",
                (interaction.user.id, steam_id, datetime.now(UTC).isoformat())
            )

        name = player.get("personaname", "Неизвестно")
        avatar = player.get("avatarfull", "")
        emb = discord.Embed(
            title="✅ Steam профиль привязан",
            description=f"**{name}**\nSteamID64: `{steam_id}`",
            color=discord.Color.blue()
        )
        if avatar:
            emb.set_thumbnail(url=avatar)
        await interaction.followup.send(embed=emb, ephemeral=True)

    # ── /стим_отвязать ────────────────────────────────────────────────────────
    @app_commands.command(name="стим_отвязать", description="Отвязать Steam профиль")
    async def стим_отвязать(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT steam_id FROM steam_profiles WHERE user_id=?",
                (interaction.user.id,)
            ).fetchone()
            if not row:
                await interaction.response.send_message(
                    "❌ У тебя нет привязанного профиля.", ephemeral=True)
                return
            conn.execute(
                "DELETE FROM steam_profiles WHERE user_id=?", (interaction.user.id,))
            conn.execute(
                "DELETE FROM steam_wishlist_cache WHERE user_id=?", (interaction.user.id,))
        await interaction.response.send_message("✅ Steam профиль отвязан.", ephemeral=True)

    # ── /стим ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="стим", description="Статистика Steam профиля")
    @app_commands.describe(пользователь="Чей профиль посмотреть (по умолчанию свой)")
    async def стим(self, interaction: discord.Interaction,
                   пользователь: discord.Member | None = None):
        await interaction.response.defer(thinking=True)
        target = пользователь or interaction.user
        api_key = _get_api_key()

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT steam_id FROM steam_profiles WHERE user_id=?", (target.id,)
            ).fetchone()
        if not row:
            name = "у тебя" if target == interaction.user else f"у {target.display_name}"
            await interaction.followup.send(
                f"❌ Steam профиль не привязан {name}.\n"
                f"Используй `/стим_привязать`", ephemeral=True)
            return

        steam_id = row[0]
        player   = await _get_player_summary(steam_id, api_key)
        games    = await _get_owned_games(steam_id, api_key)

        if not player:
            await interaction.followup.send("❌ Не удалось получить данные профиля.")
            return

        # Топ-5 по часам
        games_sorted = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
        top5 = games_sorted[:5]

        # Статус онлайн
        persona_state = {0:"⚫ Оффлайн", 1:"🟢 Онлайн", 2:"🔵 Занят",
                         3:"🟡 Отошёл",  4:"🟡 Сплю",   5:"🟣 Ищу обмен", 6:"🔴 Играю"}
        state = persona_state.get(player.get("personastate", 0), "⚫")
        game_now = player.get("gameextrainfo", "")

        total_hours = sum(g.get("playtime_forever", 0) for g in games) // 60

        emb = discord.Embed(
            title=f"🎮 {player.get('personaname', 'Steam')}",
            url=player.get("profileurl", ""),
            color=discord.Color.blue()
        )
        emb.set_thumbnail(url=player.get("avatarfull", ""))
        emb.add_field(name="Статус",    value=f"{state}{f' · {game_now}' if game_now else ''}", inline=False)
        emb.add_field(name="Игр",       value=f"**{len(games)}**",   inline=True)
        emb.add_field(name="Всего часов", value=f"**{total_hours}ч**", inline=True)
        emb.add_field(name="SteamID64", value=f"`{steam_id}`",        inline=True)

        if top5:
            lines = [
                f"**{i+1}.** {g.get('name','?')} — {_fmt_minutes(g.get('playtime_forever',0))}"
                for i, g in enumerate(top5)
            ]
            emb.add_field(name="Топ игр по часам", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=emb)

    # ── /стим_вишлист ─────────────────────────────────────────────────────────
    @app_commands.command(name="стим_вишлист",
                          description="Вишлист Steam участника")
    @app_commands.describe(пользователь="Чей вишлист (по умолчанию свой)")
    async def стим_вишлист(self, interaction: discord.Interaction,
                            пользователь: discord.Member | None = None):
        await interaction.response.defer(thinking=True)
        target = пользователь or interaction.user

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT steam_id FROM steam_profiles WHERE user_id=?", (target.id,)
            ).fetchone()
        if not row:
            await interaction.followup.send(
                "❌ Steam профиль не привязан.", ephemeral=True)
            return

        wishlist = await _get_wishlist(row[0])
        if not wishlist:
            await interaction.followup.send(
                "📭 Вишлист пуст или закрыт.", ephemeral=True)
            return

        # Сортируем по приоритету
        items = sorted(wishlist.items(), key=lambda x: x[1].get("priority", 999))[:15]

        emb = discord.Embed(
            title=f"🎮 Вишлист {target.display_name}",
            color=discord.Color.blue()
        )
        lines = []
        for appid_str, info in items:
            name     = info.get("name", f"App {appid_str}")
            released = not info.get("is_free_game", False) and not str(info.get("release_string","")).lower().startswith("soon")
            url      = f"https://store.steampowered.com/app/{appid_str}"
            status   = "🟢" if released else "🔜"
            lines.append(f"{status} [{name}]({url})")

        emb.description = "\n".join(lines)
        emb.set_footer(text=f"Показано {len(items)} из {len(wishlist)} игр")
        await interaction.followup.send(embed=emb)

    # ── /стим_общие ───────────────────────────────────────────────────────────
    @app_commands.command(name="стим_общие",
                          description="Общие игры с другим участником")
    @app_commands.describe(пользователь="С кем сравнить библиотеку")
    async def стим_общие(self, interaction: discord.Interaction,
                          пользователь: discord.Member):
        await interaction.response.defer(thinking=True)
        api_key = _get_api_key()

        ids = {}
        for uid in [interaction.user.id, пользователь.id]:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT steam_id FROM steam_profiles WHERE user_id=?", (uid,)
                ).fetchone()
            if not row:
                name = "у тебя" if uid == interaction.user.id else f"у {пользователь.display_name}"
                await interaction.followup.send(
                    f"❌ Steam профиль не привязан {name}.", ephemeral=True)
                return
            ids[uid] = row[0]

        games1 = await _get_owned_games(ids[interaction.user.id], api_key)
        games2 = await _get_owned_games(ids[пользователь.id], api_key)

        set1 = {g["appid"]: g for g in games1}
        set2 = {g["appid"]: g for g in games2}
        common_ids = set(set1.keys()) & set(set2.keys())

        if not common_ids:
            await interaction.followup.send(
                f"😢 Общих игр с {пользователь.display_name} не найдено.")
            return

        # Сортируем по суммарному времени
        common = sorted(
            common_ids,
            key=lambda aid: set1[aid].get("playtime_forever", 0) + set2[aid].get("playtime_forever", 0),
            reverse=True
        )[:10]

        emb = discord.Embed(
            title=f"🎮 Общие игры: {interaction.user.display_name} & {пользователь.display_name}",
            description=f"Всего общих: **{len(common_ids)}**",
            color=discord.Color.green()
        )
        lines = []
        for aid in common:
            name = set1[aid].get("name", f"App {aid}")
            h1   = _fmt_minutes(set1[aid].get("playtime_forever", 0))
            h2   = _fmt_minutes(set2[aid].get("playtime_forever", 0))
            lines.append(f"**{name}** — {interaction.user.display_name}: {h1} · {пользователь.display_name}: {h2}")
        emb.add_field(name="Топ по времени", value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=emb)

    # ── /релизы_канал ─────────────────────────────────────────────────────────
    @app_commands.command(name="релизы_канал",
                          description="(Админ) Канал для уведомлений о релизах и скидках")
    @app_commands.describe(
        канал="Куда постить уведомления",
        минимальная_скидка="Минимальная скидка для уведомления (%, по умолчанию 50)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def релизы_канал(self, interaction: discord.Interaction,
                            канал: discord.TextChannel,
                            минимальная_скидка: app_commands.Range[int, 10, 100] = 50):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO steam_config(guild_id, notify_channel, discount_min_pct)"
                " VALUES(?,?,?)"
                " ON CONFLICT(guild_id) DO UPDATE SET"
                " notify_channel=excluded.notify_channel,"
                " discount_min_pct=excluded.discount_min_pct",
                (interaction.guild.id, канал.id, минимальная_скидка)
            )
        await interaction.response.send_message(
            f"✅ Уведомления о релизах и скидках ≥ **{минимальная_скидка}%** "
            f"будут постить в {канал.mention}.",
            ephemeral=True)

    # ── /релизы_проверить ─────────────────────────────────────────────────────
    @app_commands.command(name="релизы_проверить",
                          description="(Админ) Проверить вишлисты прямо сейчас")
    @app_commands.checks.has_permissions(administrator=True)
    async def релизы_проверить(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _check_releases(self.bot)
        await interaction.followup.send(
            "✅ Проверка вишлистов завершена.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Steam(bot))
