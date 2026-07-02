# -*- coding: utf-8 -*-
# fun_slesh/steam.py
"""
Steam интеграция:
  /steam привязать  — привязать Steam профиль (URL / ник / SteamID64)
  /steam отвязать   — отвязать профиль
  /steam профиль    — посмотреть статистику (своё или чужое)
  /steam вишлист    — Steam-вишлист участника
  /steam общие      — общие игры с другим участником
  /релизы_проверить — (Админ) запустить проверку релизов/скидок вручную
  /релизы_канал     — (Админ) куда постить уведомления о релизах/скидках

Планировщик: каждые 6 часов проверяет вишлисты всех привязанных — если игра вышла
или скидка ≥ настроенного порога → постит в канал.
"""

import os, sqlite3, json, re, random
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.paths import SOCIAL_DB
from core.settings_store import get_feature_policy, has_feature_setting, set_feature_channel, set_feature_payload

DB_PATH = SOCIAL_DB
FEATURE_STEAM = "steam"
UTC     = timezone.utc
MSK     = ZoneInfo("Europe/Moscow")

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

            CREATE TABLE IF NOT EXISTS steam_manual_watchlist (
                user_id    INTEGER NOT NULL,
                appid      INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                added_at   TEXT    NOT NULL,
                PRIMARY KEY (user_id, appid)
            );

            CREATE TABLE IF NOT EXISTS steam_owned_games_cache (
                user_id          INTEGER NOT NULL,
                appid            INTEGER NOT NULL,
                name             TEXT    NOT NULL,
                playtime_forever INTEGER NOT NULL DEFAULT 0,
                playtime_2weeks  INTEGER NOT NULL DEFAULT 0,
                last_played      INTEGER NOT NULL DEFAULT 0,
                checked_at       TEXT    NOT NULL,
                PRIMARY KEY (user_id, appid)
            );

            CREATE TABLE IF NOT EXISTS steam_auto_settings (
                user_id           INTEGER PRIMARY KEY,
                random_enabled    INTEGER NOT NULL DEFAULT 1,
                challenge_enabled INTEGER NOT NULL DEFAULT 1,
                backlog_enabled   INTEGER NOT NULL DEFAULT 1,
                backlog_tone      TEXT    NOT NULL DEFAULT 'soft'
            );

            CREATE TABLE IF NOT EXISTS steam_auto_log (
                user_id    INTEGER NOT NULL,
                kind       TEXT    NOT NULL,
                appid      INTEGER,
                period_key TEXT    NOT NULL,
                sent_at    TEXT    NOT NULL,
                PRIMARY KEY (user_id, kind, period_key)
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


async def _search_store_app(query: str) -> dict | None:
    query = (query or "").strip()
    if not query:
        return None
    if re.match(r"^\d+$", query):
        details = await _get_app_details(int(query))
        if details:
            return {"appid": int(query), "name": details.get("name") or f"App {query}"}
        return None
    url = "https://store.steampowered.com/api/storesearch/"
    async with aiohttp.ClientSession() as s:
        async with s.get(
            url,
            params={"term": query, "cc": "ru", "l": "russian"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
    items = data.get("items") or []
    if not items:
        return None
    best = items[0]
    return {"appid": int(best["id"]), "name": best.get("name") or query}


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


def _period_key(kind: str, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    if kind == "backlog":
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"
    return now.date().isoformat()


def _auto_log_exists(user_id: int, kind: str, period_key: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM steam_auto_log WHERE user_id=? AND kind=? AND period_key=?",
            (user_id, kind, period_key),
        ).fetchone()
    return bool(row)


def _mark_auto_log(user_id: int, kind: str, period_key: str, appid: int | None = None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO steam_auto_log(user_id, kind, appid, period_key, sent_at)
            VALUES(?,?,?,?,?)
            """,
            (user_id, kind, appid, period_key, datetime.now(UTC).isoformat()),
        )
        conn.commit()


def _steam_notify_configs(bot: commands.Bot) -> list[tuple[int, int, int]]:
    with sqlite3.connect(DB_PATH) as conn:
        legacy_rows = conn.execute(
            "SELECT guild_id, notify_channel, discount_min_pct FROM steam_config"
            " WHERE notify_channel IS NOT NULL"
        ).fetchall()

    by_guild: dict[int, tuple[int, int, int]] = {
        int(guild_id): (int(guild_id), int(channel_id), int(discount_min_pct))
        for guild_id, channel_id, discount_min_pct in legacy_rows
        if channel_id
    }
    for guild in bot.guilds:
        policy = get_feature_policy(guild.id, FEATURE_STEAM)
        configured = has_feature_setting(guild.id, FEATURE_STEAM) or policy.output_channel_id is not None
        if not configured:
            continue
        if not policy.enabled or not policy.output_channel_id:
            by_guild.pop(guild.id, None)
            continue
        payload = policy.extra or {}
        legacy_min_pct = by_guild.get(guild.id, (guild.id, int(policy.output_channel_id), 50))[2]
        try:
            min_pct = int(payload.get("discount_min_pct", legacy_min_pct))
        except (TypeError, ValueError):
            min_pct = legacy_min_pct
        by_guild[guild.id] = (guild.id, int(policy.output_channel_id), max(0, min(100, min_pct)))
    return list(by_guild.values())


def _public_channel_for_user(bot: commands.Bot, user_id: int, preferred_channel_id: int | None = None) -> discord.TextChannel | None:
    if preferred_channel_id:
        channel = bot.get_channel(preferred_channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
    for guild_id, channel_id, _ in _steam_notify_configs(bot):
        guild = bot.get_guild(int(guild_id))
        if guild and guild.get_member(int(user_id)):
            channel = bot.get_channel(int(channel_id))
            if isinstance(channel, discord.TextChannel):
                return channel
    return None


async def _public_or_dm(bot: commands.Bot, user_id: int, embed: discord.Embed, fallback_channel_id: int | None = None) -> bool:
    channel = _public_channel_for_user(bot, user_id, fallback_channel_id)
    if channel:
        try:
            await channel.send(
                content=f"<@{user_id}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            return True
        except Exception:
            pass
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(embed=embed)
        return True
    except Exception:
        pass
    return False


async def _sync_owned_games(user_id: int, steam_id: str, api_key: str) -> list[dict]:
    games = await _get_owned_games(steam_id, api_key)
    now_str = datetime.now(UTC).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        for game in games:
            conn.execute(
                """
                INSERT INTO steam_owned_games_cache(
                    user_id, appid, name, playtime_forever, playtime_2weeks, last_played, checked_at
                )
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id, appid) DO UPDATE SET
                    name=excluded.name,
                    playtime_forever=excluded.playtime_forever,
                    playtime_2weeks=excluded.playtime_2weeks,
                    last_played=excluded.last_played,
                    checked_at=excluded.checked_at
                """,
                (
                    user_id,
                    int(game.get("appid", 0)),
                    game.get("name") or f"App {game.get('appid', '?')}",
                    int(game.get("playtime_forever", 0)),
                    int(game.get("playtime_2weeks", 0)),
                    int(game.get("rtime_last_played", 0)),
                    now_str,
                ),
            )
        conn.commit()
    return games


def _challenge_text(game_name: str) -> str:
    templates = [
        f"Запусти **{game_name}** хотя бы на 30 минут и не называй это тестом лаунчера.",
        f"Сделай один честный заход в **{game_name}** и выйди до того, как игра начнёт жить в голове.",
        f"Найди в **{game_name}** один момент, за который её можно похвалить. Даже если придётся копать.",
        f"Сыграй в **{game_name}** без альт-таба первые 20 минут. Босс этого челленджа — внимание.",
    ]
    return random.choice(templates)


def _backlog_text(game_name: str, tone: str) -> str:
    if tone == "hard":
        variants = [
            f"**{game_name}** лежит в библиотеке почти нетронутой. Покупка была, прохождения не было. Классика жанра.",
            f"**{game_name}** смотрит из бэклога и тихо спрашивает, зачем её вообще спасали скидкой.",
        ]
    else:
        variants = [
            f"**{game_name}** давно ждёт первого нормального запуска. Можно дать ей один вечер и посмотреть, зацепит ли.",
            f"В бэклоге мягко светится **{game_name}**. Не срочно, но игра явно просит шанс.",
        ]
    return random.choice(variants)


# ── Проверка релизов / скидок ─────────────────────────────────────────────────
async def _check_releases(bot: commands.Bot):
    api_key = _get_api_key()
    if not api_key:
        return

    with sqlite3.connect(DB_PATH) as conn:
        profiles  = conn.execute("SELECT user_id, steam_id FROM steam_profiles").fetchall()
    guild_cfgs = _steam_notify_configs(bot)

    if not guild_cfgs:
        return

    now_str = datetime.now(UTC).isoformat()

    for user_id, steam_id in profiles:
        wishlist = await _get_wishlist(steam_id)
        with sqlite3.connect(DB_PATH) as conn:
            manual_items = conn.execute(
                "SELECT appid, name FROM steam_manual_watchlist WHERE user_id=?",
                (user_id,),
            ).fetchall()

        watch_items: dict[int, str] = {}
        for appid_str, info in (wishlist or {}).items():
            try:
                appid = int(appid_str)
            except ValueError:
                continue
            watch_items[appid] = info.get("name", f"App {appid}")
        for appid, name in manual_items:
            watch_items[int(appid)] = str(name)

        if not watch_items:
            continue

        for appid, name in list(watch_items.items())[:80]:
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
                    sent_public = await _public_or_dm(bot, user_id, emb, fallback_channel_id=notify_ch_id)
                    if not sent_public:
                        await ch.send(embed=emb)
                except Exception:
                    pass


async def _send_daily_game_prompts(bot: commands.Bot):
    api_key = _get_api_key()
    if not api_key:
        return
    today_key = _period_key("daily")
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT p.user_id, p.steam_id, s.random_enabled, s.challenge_enabled
            FROM steam_profiles p
            LEFT JOIN steam_auto_settings s ON s.user_id=p.user_id
            WHERE COALESCE(s.random_enabled, 1)=1 OR COALESCE(s.challenge_enabled, 1)=1
            """
        ).fetchall()
        fallback_channels = {guild_id: channel_id for guild_id, channel_id, _ in _steam_notify_configs(bot)}

    fallback_channel_id = next(iter(fallback_channels.values()), None)
    for user_id, steam_id, random_enabled, challenge_enabled in rows:
        if _auto_log_exists(int(user_id), "daily_prompt", today_key):
            continue
        games = await _sync_owned_games(int(user_id), str(steam_id), api_key)
        candidates = [
            g for g in games
            if int(g.get("playtime_forever", 0)) >= 30 and int(g.get("appid", 0)) > 0
        ] or [g for g in games if int(g.get("appid", 0)) > 0]
        if not candidates:
            continue
        weights = []
        for game in candidates:
            played = int(game.get("playtime_forever", 0))
            recent = int(game.get("playtime_2weeks", 0))
            weights.append(max(1, 3000 - min(played, 3000) + recent))
        picked = random.choices(candidates, weights=weights, k=1)[0]
        appid = int(picked.get("appid", 0))
        name = picked.get("name") or f"App {appid}"
        store_url = f"https://store.steampowered.com/app/{appid}"

        lines = []
        if random_enabled is None or int(random_enabled):
            lines.append(f"🎲 Сегодня выпала [{name}]({store_url}).")
            lines.append("Причина: она есть в библиотеке и подходит для внезапного захода.")
        if challenge_enabled is None or int(challenge_enabled):
            lines.append(f"⚔️ Челлендж: {_challenge_text(name)}")
        emb = discord.Embed(
            title="Steam-пинок дня",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        sent = await _public_or_dm(bot, int(user_id), emb, fallback_channel_id=fallback_channel_id)
        if sent:
            _mark_auto_log(int(user_id), "daily_prompt", today_key, appid)


async def _send_weekly_backlog_prompts(bot: commands.Bot):
    api_key = _get_api_key()
    if not api_key:
        return
    week_key = _period_key("backlog")
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT p.user_id, p.steam_id, COALESCE(s.backlog_enabled, 1), COALESCE(s.backlog_tone, 'soft')
            FROM steam_profiles p
            LEFT JOIN steam_auto_settings s ON s.user_id=p.user_id
            WHERE COALESCE(s.backlog_enabled, 1)=1
            """
        ).fetchall()
        fallback_channels = [channel_id for _, channel_id, _ in _steam_notify_configs(bot)]
    fallback_channel_id = fallback_channels[0] if fallback_channels else None

    for user_id, steam_id, backlog_enabled, tone in rows:
        if not backlog_enabled or _auto_log_exists(int(user_id), "backlog", week_key):
            continue
        games = await _sync_owned_games(int(user_id), str(steam_id), api_key)
        backlog = [
            g for g in games
            if int(g.get("appid", 0)) > 0 and int(g.get("playtime_forever", 0)) <= 30
        ]
        if not backlog:
            continue
        picked = random.choice(backlog)
        appid = int(picked.get("appid", 0))
        name = picked.get("name") or f"App {appid}"
        emb = discord.Embed(
            title="Бэклог-позор недели",
            description=_backlog_text(name, str(tone)),
            url=f"https://store.steampowered.com/app/{appid}",
            color=discord.Color.dark_gold(),
        )
        sent = await _public_or_dm(bot, int(user_id), emb, fallback_channel_id=fallback_channel_id)
        if sent:
            _mark_auto_log(int(user_id), "backlog", week_key, appid)


# ── Cog ───────────────────────────────────────────────────────────────────────
class Steam(commands.Cog):
    steam_group = app_commands.Group(
        name="steam",
        description="Steam-профиль, watchlist, рандом-игра и челленджи"
    )
    releases_group = app_commands.Group(
        name="релизы",
        description="Уведомления о релизах и скидках Steam"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        if not scheduler.running:
            scheduler.start()
        scheduler.add_job(
            _check_releases, "interval", hours=12,
            args=[bot], id="steam_releases", replace_existing=True
        )
        scheduler.add_job(
            _send_daily_game_prompts, "cron",
            hour=12, minute=0, timezone=MSK,
            args=[bot], id="steam_daily_prompts", replace_existing=True,
            misfire_grace_time=3 * 3600
        )
        # Weekly backlog prompts stay disabled for now; manual buttons still call steam_рандом and steam_челлендж.

    async def _link_profile(self, interaction: discord.Interaction, profile: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        api_key = _get_api_key()
        if not api_key:
            await interaction.followup.send(
                "❌ Steam API ключ не настроен. Добавь `STEAM_API_KEY` в `KGTD.env`.",
                ephemeral=True)
            return

        steam_id = await _resolve_steam_id(profile, api_key)
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
                "❌ Профиль найден, но недоступен. Проверь приватность Steam.",
                ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO steam_profiles(user_id, steam_id, added_at) VALUES(?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET steam_id=excluded.steam_id, added_at=excluded.added_at",
                (interaction.user.id, steam_id, datetime.now(UTC).isoformat())
            )
            conn.execute(
                "INSERT OR IGNORE INTO steam_auto_settings(user_id) VALUES(?)",
                (interaction.user.id,),
            )
            conn.commit()
        await _sync_owned_games(interaction.user.id, steam_id, api_key)

        name = player.get("personaname", "Неизвестно")
        avatar = player.get("avatarfull", "")
        emb = discord.Embed(
            title="✅ Steam профиль привязан",
            description=(
                f"**{name}**\nSteamID64: `{steam_id}`\n\n"
                "Автоматически включены: скидки watchlist, рандом-игра, челленджи и мягкий бэклог. "
                "Пинки будут видны в общем Steam-канале, если он настроен."
            ),
            color=discord.Color.blue()
        )
        if avatar:
            emb.set_thumbnail(url=avatar)
        await interaction.followup.send(embed=emb, ephemeral=True)

    async def _send_profile(self, interaction: discord.Interaction, пользователь: discord.Member | None = None):
        await interaction.response.defer(thinking=True)
        target = пользователь or interaction.user
        api_key = _get_api_key()
        if not api_key:
            await interaction.followup.send("❌ Steam API ключ не настроен.", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT steam_id FROM steam_profiles WHERE user_id=?", (target.id,)
            ).fetchone()
        if not row:
            name = "у тебя" if target == interaction.user else f"у {target.display_name}"
            await interaction.followup.send(
                f"❌ Steam профиль не привязан {name}.\n"
                f"Используй `/steam привязать`", ephemeral=True)
            return

        steam_id = row[0]
        player = await _get_player_summary(steam_id, api_key)
        games = await _sync_owned_games(target.id, steam_id, api_key) if api_key else []

        if not player:
            await interaction.followup.send("❌ Не удалось получить данные профиля.")
            return

        games_sorted = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
        top5 = games_sorted[:5]

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
        emb.add_field(name="Статус", value=f"{state}{f' · {game_now}' if game_now else ''}", inline=False)
        emb.add_field(name="Игр", value=f"**{len(games)}**", inline=True)
        emb.add_field(name="Всего часов", value=f"**{total_hours}ч**", inline=True)
        emb.add_field(name="SteamID64", value=f"`{steam_id}`", inline=True)

        if top5:
            lines = [
                f"**{i+1}.** {g.get('name','?')} — {_fmt_minutes(g.get('playtime_forever',0))}"
                for i, g in enumerate(top5)
            ]
            emb.add_field(name="Топ игр по часам", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=emb)

    async def _unlink_profile(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT steam_id FROM steam_profiles WHERE user_id=?",
                (interaction.user.id,)
            ).fetchone()
            if not row:
                await interaction.response.send_message(
                    "❌ У тебя нет привязанного профиля.", ephemeral=True)
                return
            for table in (
                "steam_profiles",
                "steam_wishlist_cache",
                "steam_manual_watchlist",
                "steam_owned_games_cache",
                "steam_auto_settings",
                "steam_auto_log",
            ):
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (interaction.user.id,))
            conn.commit()
        await interaction.response.send_message("✅ Steam профиль отвязан.", ephemeral=True)

    @steam_group.command(name="привязать", description="Привязать Steam профиль по ссылке, vanity или SteamID64")
    @app_commands.describe(профиль="Ссылка Steam, vanity-ник или SteamID64")
    async def steam_привязать(self, interaction: discord.Interaction, профиль: str):
        await self._link_profile(interaction, профиль)

    @steam_group.command(name="отвязать", description="Отвязать Steam профиль")
    async def steam_отвязать(self, interaction: discord.Interaction):
        await self._unlink_profile(interaction)

    @steam_group.command(name="профиль", description="Показать Steam-профиль")
    @app_commands.describe(пользователь="Чей профиль посмотреть")
    async def steam_профиль(self, interaction: discord.Interaction, пользователь: discord.Member | None = None):
        await self._send_profile(interaction, пользователь)

    @steam_group.command(name="watchlist", description="Ручной watchlist скидок: добавить, удалить или показать")
    @app_commands.describe(
        действие="Что сделать",
        игра="Название игры или appid для добавления/удаления"
    )
    @app_commands.choices(действие=[
        app_commands.Choice(name="добавить", value="add"),
        app_commands.Choice(name="удалить", value="remove"),
        app_commands.Choice(name="список", value="list"),
    ])
    async def steam_watchlist(self, interaction: discord.Interaction, действие: str, игра: str | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if действие in {"add", "remove"} and not игра:
            await interaction.followup.send("❌ Укажи название игры или appid.", ephemeral=True)
            return

        if действие == "list":
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT appid, name FROM steam_manual_watchlist WHERE user_id=? ORDER BY added_at DESC LIMIT 25",
                    (interaction.user.id,),
                ).fetchall()
            if not rows:
                await interaction.followup.send("📭 Ручной watchlist пуст.", ephemeral=True)
                return
            lines = [f"**{i}.** [{name}](https://store.steampowered.com/app/{appid})" for i, (appid, name) in enumerate(rows, start=1)]
            await interaction.followup.send(
                embed=discord.Embed(title="Steam watchlist", description="\n".join(lines), color=discord.Color.gold()),
                ephemeral=True,
            )
            return

        app = await _search_store_app(игра or "")
        if not app:
            await interaction.followup.send("❌ Не нашёл игру в Steam Store.", ephemeral=True)
            return

        if действие == "add":
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    """
                    INSERT INTO steam_manual_watchlist(user_id, appid, name, added_at)
                    VALUES(?,?,?,?)
                    ON CONFLICT(user_id, appid) DO UPDATE SET name=excluded.name, added_at=excluded.added_at
                    """,
                    (interaction.user.id, app["appid"], app["name"], datetime.now(UTC).isoformat()),
                )
                conn.commit()
            await interaction.followup.send(
                f"✅ Добавил **{app['name']}** в watchlist скидок. Если будет скидка/релиз, напишу автоматически.",
                ephemeral=True,
            )
            return

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "DELETE FROM steam_manual_watchlist WHERE user_id=? AND appid=?",
                (interaction.user.id, app["appid"]),
            )
            conn.commit()
        await interaction.followup.send(f"✅ Убрал **{app['name']}** из watchlist.", ephemeral=True)

    @steam_group.command(name="настройки", description="Настроить автоматические Steam-пинки")
    @app_commands.describe(
        рандом="Автоматическая рандом-игра в общий Steam-канал",
        челленджи="Автоматические челленджи в общий Steam-канал",
        бэклог="Еженедельный бэклог-пинок",
        тон="Тон бэклога"
    )
    @app_commands.choices(тон=[
        app_commands.Choice(name="мягко", value="soft"),
        app_commands.Choice(name="жёстко", value="hard"),
    ])
    async def steam_настройки(
        self,
        interaction: discord.Interaction,
        рандом: bool | None = None,
        челленджи: bool | None = None,
        бэклог: bool | None = None,
        тон: str | None = None,
    ):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR IGNORE INTO steam_auto_settings(user_id) VALUES(?)", (interaction.user.id,))
            if рандом is not None:
                conn.execute("UPDATE steam_auto_settings SET random_enabled=? WHERE user_id=?", (int(рандом), interaction.user.id))
            if челленджи is not None:
                conn.execute("UPDATE steam_auto_settings SET challenge_enabled=? WHERE user_id=?", (int(челленджи), interaction.user.id))
            if бэклог is not None:
                conn.execute("UPDATE steam_auto_settings SET backlog_enabled=? WHERE user_id=?", (int(бэклог), interaction.user.id))
            if тон is not None:
                conn.execute("UPDATE steam_auto_settings SET backlog_tone=? WHERE user_id=?", (тон, interaction.user.id))
            row = conn.execute(
                "SELECT random_enabled, challenge_enabled, backlog_enabled, backlog_tone FROM steam_auto_settings WHERE user_id=?",
                (interaction.user.id,),
            ).fetchone()
            conn.commit()
        status = (
            f"🎲 Рандом: {'вкл' if row[0] else 'выкл'}\n"
            f"⚔️ Челленджи: {'вкл' if row[1] else 'выкл'}\n"
            f"📚 Бэклог: {'вкл' if row[2] else 'выкл'}\n"
            f"Тон: {'жёстко' if row[3] == 'hard' else 'мягко'}"
        )
        await interaction.response.send_message(status, ephemeral=True)

    @steam_group.command(name="рандом", description="Выдать случайную игру из твоей Steam-библиотеки")
    async def steam_рандом(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        api_key = _get_api_key()
        if not api_key:
            await interaction.followup.send("❌ Steam API ключ не настроен.")
            return
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT steam_id FROM steam_profiles WHERE user_id=?", (interaction.user.id,)).fetchone()
        if not row:
            await interaction.followup.send("❌ Сначала привяжи Steam через `/steam привязать`.")
            return
        games = await _sync_owned_games(interaction.user.id, row[0], api_key)
        candidates = [g for g in games if int(g.get("appid", 0)) > 0]
        if not candidates:
            await interaction.followup.send("📭 Не вижу игр в библиотеке. Возможно, профиль закрыт.")
            return
        picked = random.choice(candidates)
        appid = int(picked.get("appid", 0))
        name = picked.get("name") or f"App {appid}"
        await interaction.followup.send(
            f"🎲 Для {interaction.user.mention} сегодня выпала **{name}**\nhttps://store.steampowered.com/app/{appid}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @steam_group.command(name="челлендж", description="Выдать игровой челлендж по Steam-библиотеке")
    async def steam_челлендж(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        api_key = _get_api_key()
        if not api_key:
            await interaction.followup.send("❌ Steam API ключ не настроен.")
            return
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT steam_id FROM steam_profiles WHERE user_id=?", (interaction.user.id,)).fetchone()
        if not row:
            await interaction.followup.send("❌ Сначала привяжи Steam через `/steam привязать`.")
            return
        games = await _sync_owned_games(interaction.user.id, row[0], api_key)
        candidates = [g for g in games if int(g.get("appid", 0)) > 0]
        if not candidates:
            await interaction.followup.send("📭 Не вижу игр в библиотеке. Возможно, профиль закрыт.")
            return
        picked = random.choice(candidates)
        name = picked.get("name") or "случайную игру"
        await interaction.followup.send(
            f"⚔️ Челлендж для {interaction.user.mention}: {_challenge_text(name)}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @steam_group.command(name="вишлист", description="Показать Steam-вишлист участника")
    @app_commands.describe(пользователь="Чей вишлист (по умолчанию свой)")
    async def steam_вишлист(self, interaction: discord.Interaction,
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

    @steam_group.command(name="общие", description="Общие Steam-игры с другим участником")
    @app_commands.describe(пользователь="С кем сравнить библиотеку")
    async def steam_общие(self, interaction: discord.Interaction,
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
    @releases_group.command(name="канал",
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
        set_feature_channel(interaction.guild.id, FEATURE_STEAM, канал.id, "output", "Discord command")
        set_feature_payload(interaction.guild.id, FEATURE_STEAM, {"discount_min_pct": int(минимальная_скидка)})
        await interaction.response.send_message(
            f"✅ Уведомления о релизах и скидках ≥ **{минимальная_скидка}%** "
            f"будут постить в {канал.mention}.",
            ephemeral=True)

    # ── /релизы_проверить ─────────────────────────────────────────────────────
    @releases_group.command(name="проверить",
                            description="(Админ) Проверить вишлисты прямо сейчас")
    @app_commands.checks.has_permissions(administrator=True)
    async def релизы_проверить(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _check_releases(self.bot)
        await interaction.followup.send(
            "✅ Проверка вишлистов завершена.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Steam(bot))
