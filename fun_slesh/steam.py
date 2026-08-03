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

from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core import steam_service, steam_store
from core.settings_store import get_feature_policy, set_feature_channel, set_feature_payload

FEATURE_STEAM = "steam"
MSK     = ZoneInfo("Europe/Moscow")

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

_ensure_tables = steam_store.ensure_tables
_resolve_steam_id = steam_service.resolve_steam_id
_get_player_summary = steam_service.get_player_summary
_get_owned_games = steam_service.get_owned_games
_get_wishlist = steam_service.get_wishlist
_get_app_details = steam_service.get_app_details
_search_store_app = steam_service.search_store_app
_get_api_key = steam_service.get_api_key
_fmt_minutes = steam_service.format_minutes
_period_key = steam_service.period_key
_auto_log_exists = steam_store.auto_log_exists
_mark_auto_log = steam_store.mark_auto_log


def _steam_notify_configs(bot: commands.Bot) -> list[tuple[int, int, int]]:
    by_guild: dict[int, tuple[int, int, int]] = {}
    for guild in bot.guilds:
        policy = get_feature_policy(guild.id, FEATURE_STEAM)
        if not policy.enabled or not policy.output_channel_id:
            continue
        payload = policy.extra or {}
        try:
            min_pct = int(payload.get("discount_min_pct", 50))
        except (TypeError, ValueError):
            min_pct = 50
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


_sync_owned_games = steam_service.sync_owned_games
_challenge_text = steam_service.challenge_text
_backlog_text = steam_service.backlog_text


# ── Проверка релизов / скидок ─────────────────────────────────────────────────
async def _check_releases(bot: commands.Bot):
    api_key = _get_api_key()
    if not api_key:
        return

    profiles = steam_store.list_profiles()
    guild_cfgs = _steam_notify_configs(bot)

    if not guild_cfgs:
        return

    for user_id, steam_id in profiles:
        wishlist = await _get_wishlist(steam_id)
        manual_items = steam_store.list_manual_watchlist(user_id)

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

            old = steam_store.upsert_wishlist_state(
                user_id, appid, name, released, discount, price_rub
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
    rows = steam_store.list_daily_prompt_profiles()
    fallback_channels = {guild_id: channel_id for guild_id, channel_id, _ in _steam_notify_configs(bot)}

    fallback_channel_id = next(iter(fallback_channels.values()), None)
    for user_id, steam_id, random_enabled, challenge_enabled in rows:
        if _auto_log_exists(int(user_id), "daily_prompt", today_key):
            continue
        games = await _sync_owned_games(int(user_id), str(steam_id), api_key)
        picked = steam_service.choose_game(games, weighted=True)
        if not picked:
            continue
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
    rows = steam_store.list_backlog_profiles()
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
        picked = steam_service.choose_game(backlog)
        if not picked:
            continue
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

        steam_store.upsert_profile(interaction.user.id, steam_id)
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

        steam_id = steam_store.get_steam_id(target.id)
        if not steam_id:
            name = "у тебя" if target == interaction.user else f"у {target.display_name}"
            await interaction.followup.send(
                f"❌ Steam профиль не привязан {name}.\n"
                f"Используй `/steam привязать`", ephemeral=True)
            return

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
        if not steam_store.unlink_profile(interaction.user.id):
            await interaction.response.send_message(
                "❌ У тебя нет привязанного профиля.", ephemeral=True)
            return
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
            rows = steam_store.list_manual_watchlist(interaction.user.id, limit=25)
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
            steam_store.upsert_manual_watch(
                interaction.user.id, app["appid"], app["name"]
            )
            await interaction.followup.send(
                f"✅ Добавил **{app['name']}** в watchlist скидок. Если будет скидка/релиз, напишу автоматически.",
                ephemeral=True,
            )
            return

        steam_store.remove_manual_watch(interaction.user.id, app["appid"])
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
        settings = steam_store.update_auto_settings(
            interaction.user.id,
            random_enabled=рандом,
            challenge_enabled=челленджи,
            backlog_enabled=бэклог,
            backlog_tone=тон,
        )
        status = (
            f"🎲 Рандом: {'вкл' if settings['random_enabled'] else 'выкл'}\n"
            f"⚔️ Челленджи: {'вкл' if settings['challenge_enabled'] else 'выкл'}\n"
            f"📚 Бэклог: {'вкл' if settings['backlog_enabled'] else 'выкл'}\n"
            f"Тон: {'жёстко' if settings['backlog_tone'] == 'hard' else 'мягко'}"
        )
        await interaction.response.send_message(status, ephemeral=True)

    @steam_group.command(name="рандом", description="Выдать случайную игру из твоей Steam-библиотеки")
    async def steam_рандом(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        api_key = _get_api_key()
        if not api_key:
            await interaction.followup.send("❌ Steam API ключ не настроен.")
            return
        steam_id = steam_store.get_steam_id(interaction.user.id)
        if not steam_id:
            await interaction.followup.send("❌ Сначала привяжи Steam через `/steam привязать`.")
            return
        games = await _sync_owned_games(interaction.user.id, steam_id, api_key)
        picked = steam_service.choose_game(games)
        if not picked:
            await interaction.followup.send("📭 Не вижу игр в библиотеке. Возможно, профиль закрыт.")
            return
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
        steam_id = steam_store.get_steam_id(interaction.user.id)
        if not steam_id:
            await interaction.followup.send("❌ Сначала привяжи Steam через `/steam привязать`.")
            return
        games = await _sync_owned_games(interaction.user.id, steam_id, api_key)
        picked = steam_service.choose_game(games)
        if not picked:
            await interaction.followup.send("📭 Не вижу игр в библиотеке. Возможно, профиль закрыт.")
            return
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

        steam_id = steam_store.get_steam_id(target.id)
        if not steam_id:
            await interaction.followup.send(
                "❌ Steam профиль не привязан.", ephemeral=True)
            return

        wishlist = await _get_wishlist(steam_id)
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

        ids: dict[int, str] = {}
        for uid in [interaction.user.id, пользователь.id]:
            steam_id = steam_store.get_steam_id(uid)
            if not steam_id:
                name = "у тебя" if uid == interaction.user.id else f"у {пользователь.display_name}"
                await interaction.followup.send(
                    f"❌ Steam профиль не привязан {name}.", ephemeral=True)
                return
            ids[uid] = steam_id

        games1 = await _get_owned_games(ids[interaction.user.id], api_key)
        games2 = await _get_owned_games(ids[пользователь.id], api_key)

        common_count, common = steam_service.common_games(games1, games2)
        if not common_count:
            await interaction.followup.send(
                f"😢 Общих игр с {пользователь.display_name} не найдено.")
            return

        emb = discord.Embed(
            title=f"🎮 Общие игры: {interaction.user.display_name} & {пользователь.display_name}",
            description=f"Всего общих: **{common_count}**",
            color=discord.Color.green()
        )
        lines = []
        for first_game, second_game in common:
            appid = int(first_game.get("appid", 0))
            name = first_game.get("name", f"App {appid}")
            h1 = _fmt_minutes(first_game.get("playtime_forever", 0))
            h2 = _fmt_minutes(second_game.get("playtime_forever", 0))
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
