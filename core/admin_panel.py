from __future__ import annotations

import asyncio
import os
import secrets
import time
from datetime import datetime
import json
import sqlite3
from html import escape
import ipaddress
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web

from config import DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET
from core.birthday_store import (
    get_birthday,
    list_birthdays,
    remove_birthday,
    set_birthday,
    validate_birthday,
)
from core.runtime_policy import (
    DAILY_MARKOV_RETRAIN_HOUR,
    DAILY_MARKOV_RETRAIN_MINUTE,
    WEB_ADMIN_ENABLED,
    WEB_ADMIN_HOST,
    WEB_ADMIN_PORT,
    WEB_ADMIN_ALLOWED_IPS,
    WEB_ADMIN_TITLE,
    WEB_ADMIN_TOKEN,
    get_web_admin_discord_redirect_uri,
    is_daily_markov_collection_enabled,
    is_daily_markov_retrain_enabled,
    is_full_maintenance_allowed,
    policy_summary,
)
from core.settings_store import (
    clear_feature_channel,
    get_feature_policy,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)
from core.paths import BIRTHDAYS_DB, SOCIAL_DB


DISCORD_API = "https://discord.com/api/v10"
ADMIN_STATE_COOKIE = "vipik_admin_oauth_state"
ADMIN_SESSION_COOKIE = "vipik_admin_session"
ADMIN_SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


def _client_ip(request: web.Request) -> str:
    peer = request.remote or ""
    if peer.startswith("::ffff:"):
        peer = peer[7:]
    return peer


def _ip_matches_allowed(client_ip: str) -> bool:
    allowed_raw = (WEB_ADMIN_ALLOWED_IPS or "").strip()
    if not allowed_raw:
        return True
    if not client_ip:
        return False

    try:
        ip_obj = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for chunk in allowed_raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            if "/" in item:
                if ip_obj in ipaddress.ip_network(item, strict=False):
                    return True
            else:
                if ip_obj == ipaddress.ip_address(item):
                    return True
        except ValueError:
            continue

    return False


def _assert_ip_allowed(request: web.Request) -> None:
    client_ip = _client_ip(request)
    if not _ip_matches_allowed(client_ip):
        raise web.HTTPForbidden(text="Этот IP не разрешен для админ-панели")


def _is_authorized(request: web.Request) -> bool:
    if _current_admin_session(request):
        return True

    token = (
        request.headers.get("X-Admin-Token")
        or request.cookies.get("vipik_admin_token")
        or ""
    ).strip()
    return bool(WEB_ADMIN_TOKEN and token == WEB_ADMIN_TOKEN and _ip_matches_allowed(_client_ip(request)))


def _current_admin_session(request: web.Request) -> dict | None:
    if not _ip_matches_allowed(_client_ip(request)):
        return None
    session_id = (request.cookies.get(ADMIN_SESSION_COOKIE) or "").strip()
    if not session_id:
        return None
    sessions = request.app.get("admin_sessions", {})
    data = sessions.get(session_id)
    if not data:
        return None
    if float(data.get("expires_at") or 0) <= time.time():
        sessions.pop(session_id, None)
        return None
    return data


def _admin_display_name(session: dict | None) -> str:
    if not session:
        return "Discord admin"
    username = session.get("global_name") or session.get("username") or session.get("discord_user_id") or "Discord admin"
    return str(username)


def _admin_avatar_url(session: dict | None) -> str:
    if not session:
        return ""
    user_id = str(session.get("discord_user_id") or "")
    avatar_hash = str(session.get("avatar") or "")
    if user_id and avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=96"
    username = str(session.get("username") or "")
    fallback = sum(ord(ch) for ch in username) % 5 if username else 0
    return f"https://cdn.discordapp.com/embed/avatars/{fallback}.png"


def _active_guild(bot) -> discord.Guild | None:
    guilds = list(getattr(bot, "guilds", []) or [])
    return guilds[0] if guilds else None


async def _fetch_admin_member(bot, discord_user_id: int) -> discord.Member | None:
    guild = _active_guild(bot)
    if guild is None:
        return None
    member = guild.get_member(discord_user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(discord_user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def _member_has_admin_access(member: discord.Member | None) -> bool:
    if member is None:
        return False
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


def _discord_client_id(bot) -> str:
    if DISCORD_CLIENT_ID:
        return DISCORD_CLIENT_ID
    application_id = getattr(bot, "application_id", None)
    if application_id:
        return str(application_id)
    user = getattr(bot, "user", None)
    user_id = getattr(user, "id", None)
    return str(user_id or "")


def _create_admin_session_response(request: web.Request, user_data: dict, member: discord.Member) -> web.HTTPFound:
    session_id = secrets.token_urlsafe(32)
    request.app["admin_sessions"][session_id] = {
        "discord_user_id": int(user_data["id"]),
        "username": user_data.get("username", ""),
        "global_name": user_data.get("global_name") or "",
        "avatar": user_data.get("avatar") or "",
        "guild_id": int(member.guild.id),
        "expires_at": time.time() + ADMIN_SESSION_TTL_SECONDS,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    response = web.HTTPFound("/")
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="Lax",
        max_age=ADMIN_SESSION_TTL_SECONDS,
    )
    response.del_cookie(ADMIN_STATE_COOKIE)
    response.del_cookie("vipik_admin_token")
    return response


def _status_chip(ok: bool, on_text: str = "ON", off_text: str = "OFF") -> str:
    color = "#1f8f4d" if ok else "#9b2c2c"
    text = on_text if ok else off_text
    return (
        f"<span style=\"display:inline-block;padding:6px 10px;border-radius:999px;"
        f"background:{color};color:#fff;font-weight:700;\">{escape(text)}</span>"
    )


def _feature_requires_restart(feature: dict) -> bool:
    return bool(feature.get("restart_on_change"))


def _mark_restart_required(request: web.Request, feature: dict, action: str):
    if not _feature_requires_restart(feature):
        return
    reason = f"{feature['title']}: {action}"
    request.app["restart_required"] = True
    reasons = request.app.setdefault("restart_reasons", [])
    if reason not in reasons:
        reasons.append(reason)


def _render_restart_card(request: web.Request | None) -> str:
    if request is None or not request.app.get("restart_required"):
        return ""
    reasons = request.app.get("restart_reasons") or []
    reason_items = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons)
    reason_block = f"<ul>{reason_items}</ul>" if reason_items else ""
    return f"""
    <div class="card">
      <h2>Сохранить и перезапустить</h2>
      <p class="help">Есть изменения, которые применятся только после чистого старта бота.</p>
      {reason_block}
      <form method="post" action="/maintenance/restart" onsubmit="return confirm('Перезапустить бота сейчас?');">
        <button type="submit" class="button-danger">Сохранить и перезапустить бота</button>
      </form>
    </div>
    """


async def _delayed_process_restart(delay_seconds: float = 1.5):
    await asyncio.sleep(delay_seconds)
    os._exit(0)


FEATURE_REGISTRY = (
    {
        "id": "daily_summary",
        "title": "Итоги сервера",
        "group": "Настройки сервера",
        "description": "Автопостинг итогов дня, недели и месяца.",
        "channel_modes": ("output",),
        "settings_help": "Канал, куда бот отправляет итоги. Расписание пока хранится в коде планировщика.",
    },
    {
        "id": "birthday",
        "title": "Дни рождения",
        "group": "Настройки сервера",
        "description": "Поздравления, канал поздравлений и пользовательские даты.",
        "channel_modes": ("output",),
        "settings_help": "Канал поздравлений и общий список дат рождения ниже на этой странице.",
    },
    {
        "id": "wwm_guild",
        "title": "WWM гильдия",
        "group": "Настройки сервера",
        "description": "Ники WWM, карточки, приветствие и приемная.",
        "channel_modes": ("output", "allow", "exclude"),
        "settings_help": "Канал приветствия, приемная и ограничения по каналам для WWM-сценариев.",
    },
    {
        "id": "steam",
        "title": "Steam-релизы",
        "group": "Настройки сервера",
        "description": "Steam-профили, вишлисты, релизы и уведомления.",
        "channel_modes": ("output",),
        "settings_help": "Канал уведомлений и минимальная скидка для подборок.",
    },
    {
        "id": "toxicity",
        "title": "Токсичность",
        "group": "Модерация",
        "description": "Детектор токсичности, пороги и исключения каналов.",
        "channel_modes": ("allow", "exclude"),
        "settings_help": "Где проверять сообщения и где проверку отключить. Порог можно поправить в дополнительных настройках.",
    },
    {
        "id": "social_chat",
        "title": "Болтовня",
        "group": "Модерация",
        "description": "Случайные ответы бота, шанс ответа и режимы.",
        "channel_modes": ("allow", "exclude"),
        "settings_help": "Каналы, где бот может отвечать сам, и каналы-исключения.",
    },
    {
        "id": "voice_roles",
        "title": "Голосовые роли",
        "group": "Экономика и роли",
        "description": "Авто-роли по голосовым каналам и исключения.",
        "channel_modes": ("allow", "exclude"),
        "settings_help": "Ограничения для автоматических ролей, связанных с голосовыми каналами.",
    },
    {
        "id": "economy",
        "title": "Экономика",
        "group": "Экономика и роли",
        "description": "Налоги, магазин, награды и персональная валюта.",
        "channel_modes": (),
        "settings_help": "Налоги, магазин ролей, награды активности и персональная валюта.",
    },
    {
        "id": "parody_training",
        "title": "Пародии и модели",
        "group": "Модели и пародии",
        "description": "Markov-модели, фильтры корпуса и безопасное обучение.",
        "channel_modes": ("allow", "exclude"),
        "restart_on_change": True,
        "settings_help": "Каналы для сбора/использования пародийных ответов и безопасные флаги моделей.",
    },
    {
        "id": "maintenance",
        "title": "Обслуживание",
        "group": "Обслуживание",
        "description": "Сбор сообщений, индексация, профилактика и ручные проверки.",
        "channel_modes": (),
        "restart_on_change": True,
        "settings_help": "Тяжелые сервисные действия. Опасные операции оставлены за подтверждением и перезапуском.",
    },
    {
        "id": "fallback_platform",
        "title": "Сайт и запасной чат",
        "group": "Сайт и app",
        "description": "Сайт/app, чат, комнаты, голосовые комнаты и демонстрации экрана.",
        "channel_modes": (),
        "restart_on_change": True,
        "settings_help": "Настройки запасной площадки, когда Discord недоступен или нужен веб-чат.",
    },
)

FEATURES_BY_ID = {item["id"]: item for item in FEATURE_REGISTRY}

SUMMARY_TEXT_FIELDS = (
    (
        "daily_title_template",
        "Заголовок итога дня",
        "🌙 Итог дня — {date}",
        "Можно вставить: {date} - дата итога, {guild} - сервер, {haiku} - автоматическое хокку.",
    ),
    (
        "daily_description_template",
        "Текст под заголовком дня",
        "*{haiku}*",
        "Можно вставить: {haiku} - автоматическое хокку, {date} - дата, {guild} - сервер.",
    ),
    (
        "daily_footer_template",
        "Подпись итога дня",
        "Увидимся завтра 👋",
        "Можно вставить: {date} - дата, {guild} - сервер, {haiku} - хокку.",
    ),
    (
        "weekly_title_template",
        "Заголовок недели",
        "🏆 Итоги недели — {start}–{end}",
        "Можно вставить: {start} - начало периода, {end} - конец периода, {guild} - сервер, {period} - тип периода.",
    ),
    (
        "monthly_title_template",
        "Заголовок месяца",
        "📅 Итоги месяца — {start}–{end}",
        "Можно вставить: {start} - начало периода, {end} - конец периода, {guild} - сервер, {period} - тип периода.",
    ),
    (
        "period_footer_template",
        "Подпись недели и месяца",
        "Итоги за {period}. Канал и автопостинг настраиваются в админ-панели.",
        "Можно вставить: {period} - неделя или месяц, {start} - начало, {end} - конец, {guild} - сервер.",
    ),
    (
        "weekly_champion_message_template",
        "Сообщение с упоминанием чемпионов недели",
        "🏆 Поздравляем чемпионов недели: {mentions}",
        "Можно вставить: {mentions} - упоминания чемпионов, {guild} - сервер, {period} - период.",
    ),
    (
        "game_spotlight_title_template",
        "Заголовок выбранной игры",
        "{label}: {game}",
        "Можно вставить: {label} - твоё название, {game} - выбранная игра, {period}, {start}, {end}, {guild}.",
    ),
    (
        "game_spotlight_empty_template",
        "Если никто не играл в выбранную игру",
        "За этот период никто не отметился в {game}.",
        "Можно вставить: {game} - выбранная игра, {label} - твоё название, {period}, {start}, {end}, {guild}.",
    ),
)

SUMMARY_DAILY_BLOCKS = (
    ("daily_block_stats", "За день", "Общая сумма сообщений, войса и времени в играх."),
    ("daily_block_tracked", "Что трекалось", "Пояснение, какие данные бот учитывал."),
    ("daily_block_voice_games", "Играли", "Голосовые игровые каналы, где была активность."),
    ("daily_block_top_chatters", "Самые активные", "Топ участников по сообщениям."),
    ("daily_block_top_voice", "Топ войса", "Топ участников по времени в голосе."),
    ("daily_block_top_words", "Слова дня", "Топ слов за день."),
    ("daily_block_top_emojis", "Эмодзи дня", "Топ эмодзи за день."),
    ("daily_block_top_games", "Игры дня", "Топ игр по времени."),
    ("daily_block_user_games", "Кто во что играл", "Участники и игры, в которых они отметились."),
    ("daily_block_game_users", "Топ игроков дня", "Топ участников по игровому времени."),
    ("daily_block_game_winner", "Игровой победитель дня", "Первое место среди игроков дня."),
    ("daily_block_winners", "Победители дня", "Сводка победителей по чату, войсу и играм."),
    ("daily_block_misc", "Прочее", "Токсичность и дополнительные события дня."),
)

SUMMARY_PERIOD_BLOCKS = (
    ("period_block_main_people", "Главные люди", "Топ участников по сообщениям за неделю или месяц."),
    ("period_block_voice", "Войс", "Топ участников по времени в голосе."),
    ("period_block_rep", "Размер", "Топ по репутации/Размеру."),
    ("period_block_words", "О чём шумели", "Слова и эмодзи периода."),
    ("period_block_game_overview", "Игровой блок", "Heroes, топ игр и топ игроков."),
    ("period_block_game_spotlight", "Выбранная игра", "Отдельный блок по игре из списка, например Where Winds Meet."),
    ("period_block_user_games", "Кто во что играл", "Участники и игры периода."),
    ("period_block_other_activities", "Другие активности", "Стримы, слушает, смотрит и другие Discord-активности."),
    ("period_block_balance", "Баланс", "Топ по валюте."),
    ("period_block_streaks", "Серии", "Топ серий активности."),
    ("period_block_toxic", "Токсичность", "Топ токсичности и цитата."),
    ("period_block_champion_congrats", "Поздравления чемпионам", "Текстовый блок поздравлений победителей."),
)

SUMMARY_THEME_OPTIONS = (
    ("neon", "Неон", "Контрастный игровой стиль: фиолетовый, синий, яркие акценты."),
    ("royal", "Премиум", "Золотой акцент для недельных и месячных итогов."),
    ("forest", "Спокойный", "Зеленый и бирюзовый, меньше визуального шума."),
    ("fire", "Жаркий", "Красный/оранжевый акцент для соревновательных итогов."),
)

SUMMARY_FILTER_OPTIONS = (
    ("all", "Показывать все игры"),
    ("spotlight", "Все игры + отдельный блок выбранной игры"),
    ("only_selected", "Только выбранная игра в игровых блоках"),
)

SUMMARY_RENDER_OPTIONS = (
    ("embed", "Embed + кнопки"),
    ("components_v2", "Components v2 beta"),
)

SUMMARY_TEMPLATE_HELP = (
    ("{date}", "Дата итога дня"),
    ("{haiku}", "Автоматическое хокку дня"),
    ("{guild}", "Название сервера"),
    ("{start}", "Начало периода недели или месяца"),
    ("{end}", "Конец периода недели или месяца"),
    ("{period}", "Тип периода: неделю или месяц"),
    ("{mentions}", "Упоминания чемпионов недели"),
    ("{label}", "Твоё название группы, например “Задроты недели”"),
    ("{game}", "Выбранная игра из активности сервера"),
)


def _admin_guild_id(bot) -> int:
    guilds = list(getattr(bot, "guilds", []) or [])
    return int(guilds[0].id) if guilds else 0


def _channel_label(bot, channel_id: int | None) -> str:
    if not channel_id:
        return "Не выбран"
    guild = _active_guild(bot)
    channel = guild.get_channel(int(channel_id)) if guild else None
    name = getattr(channel, "name", None)
    if name:
        return f"#{name}"
    return f"ID {channel_id}"


def _channel_badges(bot, values: tuple[int, ...] | list[int]) -> str:
    if not values:
        return '<span class="empty-pill">Не задано</span>'
    return "".join(
        f'<span class="channel-pill">{escape(_channel_label(bot, int(channel_id)))}'
        f'<small>{int(channel_id)}</small></span>'
        for channel_id in values
    )


def _channel_options(bot) -> str:
    guild = _active_guild(bot)
    if not guild:
        return ""
    options = []
    text_channels = sorted(getattr(guild, "text_channels", []) or [], key=lambda ch: (ch.category.name if ch.category else "", ch.position))
    for channel in text_channels:
        category = f"{channel.category.name} / " if channel.category else ""
        label = f"#{category}{channel.name}"
        options.append(f'<option value="{int(channel.id)}">{escape(label)}</option>')
    return "\n".join(options)


def _member_options(bot) -> tuple[str, int]:
    guild = _active_guild(bot)
    if not guild:
        return "", 0
    members = [
        member
        for member in (getattr(guild, "members", []) or [])
        if not getattr(member, "bot", False)
    ]
    members.sort(key=lambda member: (str(getattr(member, "display_name", "") or "").lower(), int(member.id)))
    options = []
    for member in members:
        name = getattr(member, "display_name", None) or getattr(member, "name", None) or str(member.id)
        username = getattr(member, "name", "")
        suffix = f" @{username}" if username and username != name else ""
        options.append(f'<option value="{int(member.id)}">{escape(name + suffix)}</option>')
    return "\n".join(options), len(members)


def _render_mode_form(bot, feature_id: str, mode: str, current_ids: tuple[int, ...] | list[int]) -> str:
    labels = {
        "output": "Канал публикаций",
        "allow": "Разрешить в канале",
        "exclude": "Запретить в канале",
    }
    helper = {
        "output": "Куда бот пишет сам.",
        "allow": "Если список заполнен, функция работает только там.",
        "exclude": "Там функция всегда выключена.",
    }
    options = _channel_options(bot)
    selector = (
        f"""
          <select name="channel_id" required>
            <option value="">{escape(labels.get(mode, mode))}</option>
            {options}
          </select>
        """
        if options
        else f'<input name="channel_id" inputmode="numeric" placeholder="{escape(labels.get(mode, mode))}">'
    )
    return f"""
        <div class="setting-row">
          <div>
            <b>{escape(labels.get(mode, mode))}</b>
            <span class="setting-help">{escape(helper.get(mode, ""))}</span>
            <div class="channel-pills">{_channel_badges(bot, current_ids)}</div>
          </div>
          <form method="post" action="/features/{escape(feature_id)}/channel" class="channel-form">
            <input type="hidden" name="mode" value="{escape(mode)}">
            {selector}
            <input name="reason" placeholder="Комментарий">
            <button type="submit">Добавить</button>
            <button type="submit" formaction="/features/{escape(feature_id)}/channel/delete" class="button-secondary">Убрать</button>
          </form>
        </div>
    """


def _render_template_help() -> str:
    rows = "".join(
        f"<tr><td><code>{escape(token)}</code></td><td>{escape(description)}</td></tr>"
        for token, description in SUMMARY_TEMPLATE_HELP
    )
    return f"""
      <details class="template-help">
        <summary>Что можно вставлять в текст</summary>
        <table class="mini-table">
          <tbody>{rows}</tbody>
        </table>
      </details>
    """


def _block_enabled(payload: dict, key: str) -> bool:
    value = payload.get(key)
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}


def _render_summary_block_controls(payload: dict) -> str:
    def render_group(title: str, blocks: tuple[tuple[str, str, str], ...]) -> str:
        items = []
        for key, label, description in blocks:
            title_key = f"{key}_title"
            limit_key = f"{key}_limit"
            custom_title = str(payload.get(title_key) or label)
            custom_limit = str(payload.get(limit_key) or "")
            limit_input = (
                f"""
                  <label class="block-mini-field">
                    <span>Лимит топа</span>
                    <input name="{escape(limit_key)}" value="{escape(custom_limit)}" inputmode="numeric" placeholder="По умолчанию">
                  </label>
                """
                if "top" in key or "users" in key or "people" in key or "voice" in key or "games" in key
                else ""
            )
            items.append(
                f"""
                <div class="block-toggle">
                  <label class="check-setting compact">
                    <input type="checkbox" name="{escape(key)}" value="1" {'checked' if _block_enabled(payload, key) else ''}>
                    <span>
                      <b>{escape(label)}</b>
                      <small>{escape(description)}</small>
                    </span>
                  </label>
                  <label class="block-mini-field">
                    <span>Название в Discord</span>
                    <input name="{escape(title_key)}" value="{escape(custom_title)}" placeholder="{escape(label)}">
                  </label>
                  {limit_input}
                </div>
                """
            )
        return f"""
          <div class="block-group">
            <h3>{escape(title)}</h3>
            <div class="block-grid">{''.join(items)}</div>
          </div>
        """

    return f"""
      <details class="block-settings" open>
        <summary>Состав сообщения</summary>
        <p class="setting-help">Выключи блоки, которые не должны попадать в итог. Данные всё равно собираются, меняется только то, что бот показывает в сообщении.</p>
        {render_group("Итог дня", SUMMARY_DAILY_BLOCKS)}
        {render_group("Итог недели и месяца", SUMMARY_PERIOD_BLOCKS)}
      </details>
    """


def _render_summary_style_controls(payload: dict) -> str:
    theme = str(payload.get("summary_theme") or "neon")
    filter_mode = str(payload.get("game_filter_mode") or "all")
    render_mode = str(payload.get("summary_render_mode") or "embed")
    theme_options = "".join(
        f'<option value="{escape(value)}" {"selected" if value == theme else ""}>{escape(label)} — {escape(description)}</option>'
        for value, label, description in SUMMARY_THEME_OPTIONS
    )
    filter_options = "".join(
        f'<option value="{escape(value)}" {"selected" if value == filter_mode else ""}>{escape(label)}</option>'
        for value, label in SUMMARY_FILTER_OPTIONS
    )
    render_options = "".join(
        f'<option value="{escape(value)}" {"selected" if value == render_mode else ""}>{escape(label)}</option>'
        for value, label in SUMMARY_RENDER_OPTIONS
    )
    buttons_enabled = str(payload.get("summary_buttons_enabled") if "summary_buttons_enabled" in payload else "1").strip().lower() not in {"0", "false", "no", "off", "нет", "выкл"}
    compact_enabled = str(payload.get("summary_compact_mode") or "").strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}
    return f"""
      <details class="style-settings" open>
        <summary>Оформление и поведение</summary>
        <div class="style-grid">
          <label class="text-setting">
            <span>Тема итогов</span>
            <select name="summary_theme">{theme_options}</select>
            <small>Меняет основной цвет embed и общий вайб сообщения.</small>
          </label>
          <label class="text-setting">
            <span>Формат Discord</span>
            <select name="summary_render_mode">{render_options}</select>
            <small>Components v2 дает контейнеры нового Discord UI. Если Discord не примет формат, бот откатится на embed.</small>
          </label>
          <label class="text-setting">
            <span>Свой цвет</span>
            <input name="summary_accent_color" value="{escape(str(payload.get("summary_accent_color") or ""))}" placeholder="#8b5cf6">
            <small>Если заполнено, перекроет цвет темы. Формат: #RRGGBB.</small>
          </label>
          <label class="text-setting">
            <span>Картинка/обложка</span>
            <input name="summary_thumbnail_url" value="{escape(str(payload.get("summary_thumbnail_url") or ""))}" placeholder="https://...">
            <small>URL картинки для правого верхнего угла embed.</small>
          </label>
          <label class="text-setting">
            <span>Режим игровых блоков</span>
            <select name="game_filter_mode">{filter_options}</select>
            <small>Можно оставить все игры или показывать только выбранную игру.</small>
          </label>
          <label class="text-setting">
            <span>Лимит дневных топов</span>
            <input name="daily_top_limit" value="{escape(str(payload.get("daily_top_limit") or "3"))}" inputmode="numeric">
            <small>Сколько людей показывать в дневных коротких топах.</small>
          </label>
          <label class="text-setting">
            <span>Лимит больших списков</span>
            <input name="period_top_limit" value="{escape(str(payload.get("period_top_limit") or "5"))}" inputmode="numeric">
            <small>Сколько строк показывать в недельных/месячных топах.</small>
          </label>
        </div>
        <label class="check-setting">
          <input type="checkbox" name="summary_buttons_enabled" value="1" {'checked' if buttons_enabled else ''}>
          <span>Добавлять интерактивные кнопки под итогом</span>
        </label>
        <label class="check-setting">
          <input type="checkbox" name="summary_compact_mode" value="1" {'checked' if compact_enabled else ''}>
          <span>Компактный режим: меньше второстепенных строк</span>
        </label>
      </details>
    """


def _render_daily_summary_text_form(payload: dict) -> str:
    fields = []
    for key, label, default, hint in SUMMARY_TEXT_FIELDS:
        value = str(payload.get(key) or default)
        rows = 3 if "description" in key or "message" in key else 2
        fields.append(
            f"""
            <label class="text-setting">
              <span>{escape(label)}</span>
              <textarea name="{escape(key)}" rows="{rows}">{escape(value)}</textarea>
              <small>{escape(hint)}</small>
            </label>
            """
        )
    selected_game = str(payload.get("game_spotlight_game") or "")
    game_options, game_count = _recent_game_options(selected_game)
    spotlight_enabled = str(payload.get("game_spotlight_enabled") or "").strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}
    game_selector = (
        f"""
          <select name="game_spotlight_game">
            <option value="">Не добавлять отдельный игровой блок</option>
            {game_options}
          </select>
        """
        if game_options
        else '<input name="game_spotlight_game" placeholder="Название игры, если список пока пуст">'
    )
    game_hint = (
        f"Игры подтянуты из активности сервера: {game_count}."
        if game_options
        else "Список игр пока пуст: можно ввести название вручную, а позже выбрать из накопленной активности."
    )
    return f"""
      <details class="friendly-settings" open>
        <summary>Текст итогов</summary>
        <form method="post" action="/features/daily_summary/text" class="text-settings-form">
          <div class="spotlight-settings">
            <label class="check-setting">
              <input type="checkbox" name="game_spotlight_enabled" value="1" {'checked' if spotlight_enabled else ''}>
              <span>Добавить отдельный блок по выбранной игре</span>
            </label>
            <label class="text-setting">
              <span>Как назвать участников</span>
              <input name="game_spotlight_label" value="{escape(str(payload.get("game_spotlight_label") or "Задроты"))}" placeholder="Например: Задроты недели">
              <small>Это слово попадёт в заголовок блока. Например: “Задроты недели: Where Winds Meet”.</small>
            </label>
            <label class="text-setting">
              <span>Игра из активности сервера</span>
              {game_selector}
              <small>{escape(game_hint)}</small>
            </label>
          </div>
          {_render_summary_style_controls(payload)}
          {_render_summary_block_controls(payload)}
          {_render_template_help()}
          {''.join(fields)}
          <button type="submit">Сохранить текст итогов</button>
        </form>
      </details>
    """


def _render_feature_registry(bot, guild_id: int = 0) -> str:
    cards = []
    for feature in FEATURE_REGISTRY:
        feature_id = feature["id"]
        policy = get_feature_policy(guild_id, feature_id)
        output_ids = (policy.output_channel_id,) if policy.output_channel_id else ()
        modes = []
        for mode in feature["channel_modes"]:
            current = {
                "output": output_ids,
                "allow": policy.allowed_channel_ids,
                "exclude": policy.excluded_channel_ids,
            }.get(mode, ())
            modes.append(_render_mode_form(bot, feature_id, mode, current))
        restart_badge = (
            '<span class="meta-pill warn">применится после перезапуска</span>'
            if _feature_requires_restart(feature)
            else ""
        )
        friendly_settings = (
            _render_daily_summary_text_form(policy.extra or {})
            if feature_id == "daily_summary"
            else ""
        )
        cards.append(
            f"""
            <article class="feature-card" id="feature-{escape(feature_id)}">
              <div class="feature-head">
                <div>
                  <div class="area-title">{escape(feature["title"])}</div>
                  <div class="area-desc">{escape(feature["description"])}</div>
                </div>
                <form method="post" action="/features/{escape(feature_id)}/enabled">
                  <input type="hidden" name="enabled" value="{'0' if policy.enabled else '1'}">
                  <button class="{'button-secondary' if policy.enabled else ''}" type="submit">{'Выключить' if policy.enabled else 'Включить'}</button>
                </form>
              </div>
              <div class="feature-meta">
                <span>{_status_chip(policy.enabled, "Включено", "Выключено")}</span>
                <span class="meta-pill">{escape(feature["group"])}</span>
                {restart_badge}
              </div>
              <p class="setting-help">{escape(feature.get("settings_help") or "")}</p>
              {''.join(modes) if modes else '<p class="muted">У этой функции пока нет отдельных настроек в панели.</p>'}
              {friendly_settings}
            </article>
            """
        )
    return "\n".join(cards)


def _render_birthdays_panel(bot) -> str:
    member_options, member_count = _member_options(bot)
    user_selector = (
        f"""
        <select name="user_id" required>
          <option value="">Выбери участника сервера</option>
          {member_options}
        </select>
        """
        if member_options
        else '<input name="user_id" inputmode="numeric" placeholder="ID участника в Discord">'
    )
    member_hint = (
        f"Список участников подтянут с сервера: {member_count}."
        if member_options
        else "Список участников сейчас недоступен, поэтому можно ввести Discord ID вручную."
    )
    rows = list_birthdays()
    if rows:
        items = []
        for row in rows[:80]:
            user_id = int(row["user_id"])
            guild = _active_guild(bot)
            member = guild.get_member(user_id) if guild else None
            name = member.display_name if member else str(user_id)
            source = str(row.get("source") or "unknown")
            updated_by = int(row.get("updated_by") or user_id)
            items.append(
                "<tr>"
                f"<td>{escape(name)}<br><span class=\"tech-id\">Discord ID: {user_id}</span></td>"
                f"<td><b>{escape(str(row['birthday']))}</b></td>"
                f"<td>{escape(source)}<br><span class=\"tech-id\">Обновил: {updated_by}</span></td>"
                f"<td>"
                f"<form method=\"post\" action=\"/user-data/birthdays/delete\" onsubmit=\"return confirm('Удалить ДР пользователя?');\">"
                f"<input type=\"hidden\" name=\"user_id\" value=\"{user_id}\">"
                f"<button type=\"submit\" class=\"button-secondary\">Удалить</button>"
                f"</form>"
                f"</td>"
                "</tr>"
            )
        rows_html = "".join(items)
    else:
        rows_html = "<tr><td colspan=\"4\" class=\"muted\">Дни рождения пока не заполнены.</td></tr>"

    return f"""
    <section class="card" id="birthdays">
      <h2>Пользовательские ДР</h2>
      <p class="help">Админка и команда <code>/др</code> пишут в одну базу. Админ может заполнить дату за участника, но сам участник всегда может исправить её через бота. {escape(member_hint)}</p>
      <form method="post" action="/user-data/birthdays" class="inline-form">
        {user_selector}
        <input name="birthday" placeholder="ДД.ММ">
        <input name="reason" placeholder="Комментарий">
        <button type="submit">Сохранить ДР</button>
      </form>
      <table class="data-table">
        <thead><tr><th>Участник</th><th>Дата</th><th>Источник</th><th></th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </section>
    """


def _safe_count(db_path: str, table: str) -> int | None:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return None


def _table_exists(db_path: str, table: str) -> bool:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _recent_game_options(selected: str = "") -> tuple[str, int]:
    if not _table_exists(SOCIAL_DB, "activity_sessions"):
        return "", 0
    try:
        with sqlite3.connect(SOCIAL_DB) as conn:
            rows = conn.execute(
                """
                SELECT activity_name, COALESCE(SUM(seconds), 0) AS total_seconds
                FROM activity_sessions
                WHERE activity_type='game' AND COALESCE(activity_name, '') <> ''
                GROUP BY activity_name
                HAVING total_seconds > 0
                ORDER BY total_seconds DESC, activity_name COLLATE NOCASE
                LIMIT 80
                """
            ).fetchall()
    except sqlite3.Error:
        return "", 0
    options = []
    selected_clean = selected.strip().casefold()
    selected_seen = False
    for name, seconds in rows:
        clean_name = str(name or "").strip()
        if not clean_name:
            continue
        is_selected = clean_name.casefold() == selected_clean
        selected_seen = selected_seen or is_selected
        selected_attr = " selected" if is_selected else ""
        hours = int(seconds or 0) // 3600
        label = f"{clean_name} ({hours} ч)" if hours else clean_name
        options.append(f'<option value="{escape(clean_name)}"{selected_attr}>{escape(label)}</option>')
    if selected and not selected_seen:
        options.insert(0, f'<option value="{escape(selected)}" selected>{escape(selected)} (сохранено)</option>')
    return "\n".join(options), len(rows)


def _render_database_panel(bot, guild_id: int) -> str:
    settings_count = _safe_count(SOCIAL_DB, "feature_settings")
    channels_count = _safe_count(SOCIAL_DB, "feature_channels")
    birthdays_count = _safe_count(BIRTHDAYS_DB, "birthdays")
    db_cards = (
        ("Настройки функций", "feature_settings", settings_count, "Включение функций и дополнительные параметры."),
        ("Каналы функций", "feature_channels", channels_count, "Куда бот пишет, где работает и где выключен."),
        ("Дни рождения", "birthdays", birthdays_count, "Даты участников для поздравлений."),
    )
    cards = "".join(
        f"""
        <div class="db-card">
          <div class="area-title">{escape(title)}</div>
          <div class="area-desc">{escape(desc)}</div>
          <div class="db-count">{'нет данных' if count is None else count}</div>
          <span class="tech-id">{escape(table)}</span>
        </div>
        """
        for title, table, count, desc in db_cards
    )

    rows = []
    for feature in FEATURE_REGISTRY:
        policy = get_feature_policy(guild_id, feature["id"])
        channel_bits = []
        if policy.output_channel_id:
            channel_bits.append(f"публикации: {_channel_label(bot, policy.output_channel_id)}")
        if policy.allowed_channel_ids:
            channel_bits.append("разрешено: " + ", ".join(_channel_label(bot, item) for item in policy.allowed_channel_ids))
        if policy.excluded_channel_ids:
            channel_bits.append("запрещено: " + ", ".join(_channel_label(bot, item) for item in policy.excluded_channel_ids))
        rows.append(
            "<tr>"
            f"<td><a href=\"#feature-{escape(feature['id'])}\">{escape(feature['title'])}</a><br><span class=\"tech-id\">{escape(feature['id'])}</span></td>"
            f"<td>{'Включено' if policy.enabled else 'Выключено'}</td>"
            f"<td>{escape('; '.join(channel_bits) if channel_bits else 'Каналы не заданы')}</td>"
            f"<td>{escape(', '.join(sorted((policy.extra or {}).keys())) if policy.extra else 'Нет')}</td>"
            "</tr>"
        )

    return f"""
    <section class="card" id="databases">
      <h2>Базы и списки</h2>
      <p class="help">Здесь видно, что именно сейчас лежит в известных таблицах админки. Для изменения функции открой её карточку в разделе настроек.</p>
      <div class="db-grid">{cards}</div>
      <details class="db-details" open>
        <summary>Текущие настройки функций</summary>
        <table class="data-table">
          <thead><tr><th>Функция</th><th>Состояние</th><th>Каналы</th><th>Доп. параметры</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </details>
    </section>
    """


def _render_toxicity_ml_panel() -> str:
    try:
        with sqlite3.connect(SOCIAL_DB) as conn:
            rows = conn.execute(
                """
                SELECT s.message_id,s.rule_level,s.ml_level,s.ml_confidence,s.model_version,s.msg_snippet
                FROM toxicity_ml_shadow s
                LEFT JOIN toxicity_ml_feedback f ON f.message_id=s.message_id
                WHERE f.message_id IS NULL
                ORDER BY (s.rule_level != s.ml_level) DESC, s.ml_confidence DESC, s.logged_at DESC
                LIMIT 20
                """
            ).fetchall()
            reviewed = conn.execute("SELECT COUNT(*) FROM toxicity_ml_feedback").fetchone()[0]
    except sqlite3.Error:
        rows, reviewed = [], 0
    body = []
    for message_id, rule_level, ml_level, confidence, version, snippet in rows:
        options = "".join(
            f'<option value="{level}"{" selected" if level == rule_level else ""}>{level}</option>'
            for level in range(4)
        )
        body.append(
            "<tr>"
            f"<td>{escape(str(snippet))}<br><span class=\"tech-id\">{escape(str(message_id))}</span></td>"
            f"<td>{rule_level}</td><td>{ml_level} ({float(confidence):.0%})<br><span class=\"tech-id\">{escape(str(version))}</span></td>"
            "<td><form method=\"post\" action=\"/toxicity-ml/feedback\">"
            f"<input type=\"hidden\" name=\"message_id\" value=\"{message_id}\">"
            f"<select name=\"level\">{options}</select> <button type=\"submit\">Разметить</button>"
            "</form></td></tr>"
        )
    rows_html = "".join(body) or '<tr><td colspan="4">Новых shadow-примеров пока нет.</td></tr>'
    return f"""
    <section class="card" id="toxicity-ml">
      <h2>ML токсичности — shadow</h2>
      <p class="help">Модель только сравнивается с правилами и не применяет санкции. Проверенных примеров: <b>{reviewed}</b>.</p>
      <table class="data-table"><thead><tr><th>Текст</th><th>Правила</th><th>ML</th><th>Верный уровень</th></tr></thead>
      <tbody>{rows_html}</tbody></table>
    </section>
    """

def _render_page(bot, request: web.Request | None = None, message: str = "") -> str:
    summary = policy_summary()
    if request is not None:
        summary["client_ip"] = _client_ip(request)
    admin_session = _current_admin_session(request) if request is not None else None
    bot_name = escape(str(bot.user) if bot.user else "bot not ready")
    admin_name = escape(_admin_display_name(admin_session))
    avatar_url = escape(_admin_avatar_url(admin_session))
    client_ip = escape(str(summary.get("client_ip", "")))
    allowed_ips = escape(str(summary.get("web_admin_allowed_ips", "") or "не ограничены"))
    notice = (
        f"<div class=\"notice\">{escape(message)}</div>"
        if message
        else ""
    )
    active_guild_id = _admin_guild_id(bot)
    guild = _active_guild(bot)
    guild_name = escape(guild.name if guild else "Сервер не найден")
    feature_registry = _render_feature_registry(bot, active_guild_id)
    database_panel = _render_database_panel(bot, active_guild_id)
    birthdays_panel = _render_birthdays_panel(bot)
    toxicity_ml_panel = _render_toxicity_ml_panel()
    restart_card = _render_restart_card(request)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(WEB_ADMIN_TITLE)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg:#0d1117;
      --panel:#161b22;
      --panel-2:#1f2630;
      --line:#303844;
      --text:#edf2f7;
      --muted:#9aa7b5;
      --blue:#3b82f6;
      --green:#19a25b;
      --red:#c24141;
      --amber:#d9a441;
    }}
    * {{ box-sizing:border-box; }}
    body {{ font-family: Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); margin:0; }}
    a {{ color:#93c5fd; }}
    .layout {{ display:grid; grid-template-columns:minmax(0,1fr) 280px; min-height:100vh; }}
    #navToggle {{ position:absolute; opacity:0; pointer-events:none; }}
    .sidebar {{ grid-column:2; grid-row:1; position:sticky; top:0; height:100vh; padding:18px 12px; background:#0f141b; border-left:1px solid var(--line); overflow:auto; }}
    .toggle-label {{ display:flex; width:42px; height:42px; align-items:center; justify-content:center; border:1px solid var(--line); border-radius:10px; cursor:pointer; background:var(--panel); font-size:22px; margin-bottom:14px; }}
    .profile {{ display:flex; gap:12px; align-items:center; padding:10px; border-radius:12px; background:var(--panel); border:1px solid var(--line); margin-bottom:14px; }}
    .avatar {{ width:46px; height:46px; border-radius:50%; background:#334155; flex:0 0 auto; object-fit:cover; }}
    .profile-name {{ font-weight:800; line-height:1.2; }}
    .profile-sub {{ color:var(--muted); font-size:13px; margin-top:3px; }}
    .nav {{ display:grid; gap:6px; }}
    .nav a, .nav button {{ display:flex; align-items:center; gap:10px; width:100%; text-decoration:none; color:var(--text); background:transparent; border:0; border-radius:10px; padding:11px 10px; font:inherit; font-weight:700; cursor:pointer; text-align:left; }}
    .nav a:hover, .nav button:hover {{ background:var(--panel); }}
    .nav-icon {{ width:24px; text-align:center; color:#cbd5e1; }}
    .main {{ grid-column:1; grid-row:1; justify-self:center; width:min(1280px, calc(100% - 48px)); padding:24px 0; }}
    .topbar {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:18px; }}
    h1, h2 {{ margin:0 0 12px; }}
    h1 {{ font-size:28px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin-bottom:16px; box-shadow:0 12px 30px rgba(0,0,0,.22); }}
    .notice {{ padding:12px 14px; background:#10223b; border:1px solid #2b5d9a; border-radius:8px; margin-bottom:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; }}
    .item, .db-card {{ background:var(--panel-2); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }}
    .label {{ font-size:12px; text-transform:uppercase; color:var(--muted); margin-bottom:8px; }}
    .value {{ font-size:16px; font-weight:800; overflow-wrap:anywhere; }}
    .help, .area-desc, .setting-help {{ color:var(--muted); line-height:1.5; }}
    button {{ border:0; border-radius:8px; padding:11px 14px; font-size:14px; font-weight:800; cursor:pointer; background:var(--blue); color:#fff; }}
    input, textarea, select {{ width:100%; border:1px solid var(--line); border-radius:8px; padding:10px 12px; font:inherit; background:#111821; color:var(--text); }}
    textarea {{ min-height:96px; font-family:Consolas, monospace; }}
    .button-secondary {{ background:#465568; }}
    .button-danger {{ background:var(--red); }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .actions form {{ margin:0; }}
    .actions a {{ display:inline-block; text-decoration:none; border-radius:8px; padding:11px 14px; font-size:14px; font-weight:800; background:#465568; color:#fff; }}
    .muted {{ color:var(--muted); }}
    code, .tech-id {{ background:#111821; color:#cbd5e1; padding:2px 6px; border-radius:6px; font-size:12px; overflow-wrap:anywhere; }}
    .area-title {{ font-weight:900; margin-bottom:6px; }}
    .feature-stack {{ display:grid; gap:14px; }}
    .feature-card {{ border:1px solid var(--line); border-radius:8px; padding:16px; background:#141a22; }}
    .feature-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .feature-meta, .channel-pills {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:12px; }}
    .meta-pill, .empty-pill, .channel-pill {{ display:inline-flex; align-items:center; gap:8px; padding:6px 9px; border-radius:999px; background:#202a36; color:#dbeafe; font-size:13px; font-weight:700; }}
    .warn {{ color:#ffe7a3; background:#3a2d12; }}
    .channel-pill small {{ color:var(--muted); font-weight:600; }}
    .setting-row {{ display:grid; grid-template-columns:minmax(210px,.8fr) minmax(320px,1.2fr); gap:14px; padding:14px 0; border-top:1px solid var(--line); }}
    .setting-help {{ display:block; margin-top:4px; font-size:14px; }}
    .channel-form {{ display:grid; grid-template-columns:minmax(180px,1fr) minmax(160px,1fr) auto auto; gap:8px; align-items:center; }}
    .inline-form {{ display:grid; grid-template-columns:minmax(160px,1fr) minmax(110px,.5fr) minmax(160px,1fr) auto; gap:8px; align-items:center; margin:8px 0; }}
    .friendly-settings {{ border-top:1px solid var(--line); padding-top:12px; }}
    .text-settings-form {{ display:grid; gap:12px; margin-top:12px; }}
    .spotlight-settings {{ display:grid; gap:12px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#111821; }}
    .block-settings {{ padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:#111821; }}
    .style-settings {{ padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:#111821; }}
    .style-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin:10px 0; }}
    .block-group {{ margin-top:12px; }}
    .block-group h3 {{ margin:0 0 8px; font-size:16px; }}
    .block-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:8px; }}
    .block-toggle {{ display:grid; gap:8px; padding:10px; border:1px solid var(--line); border-radius:8px; background:#141a22; }}
    .block-toggle input {{ width:auto; margin-top:3px; }}
    .block-toggle span {{ display:grid; gap:3px; }}
    .block-toggle small {{ color:var(--muted); line-height:1.35; }}
    .check-setting.compact {{ align-items:flex-start; margin:0; }}
    .block-mini-field {{ display:grid; gap:4px; font-size:12px; color:var(--muted); }}
    .block-mini-field input {{ width:100%; min-height:34px; padding:7px 9px; }}
    .text-setting {{ display:grid; gap:6px; }}
    .text-setting span {{ font-weight:800; }}
    .text-setting small {{ color:var(--muted); }}
    .check-setting {{ display:flex; gap:10px; align-items:center; font-weight:800; }}
    .check-setting input {{ width:auto; }}
    .template-help {{ padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:#111821; }}
    .mini-table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
    .mini-table td {{ border-top:1px solid var(--line); padding:8px; vertical-align:top; }}
    .mini-table td:first-child {{ width:130px; }}
    .db-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin:12px 0; }}
    .db-count {{ font-size:30px; font-weight:900; margin:12px 0 6px; }}
    .data-table {{ width:100%; border-collapse:collapse; margin-top:14px; }}
    .data-table th, .data-table td {{ border-top:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }}
    .data-table th {{ color:var(--muted); font-size:13px; }}
    details {{ margin-top:12px; }}
    summary {{ cursor:pointer; font-weight:800; color:#dbeafe; }}
    .system-card {{ display:grid; grid-template-columns:1fr auto; gap:14px; align-items:center; }}
    #navToggle:checked ~ .layout {{ grid-template-columns:minmax(0,1fr) 72px; }}
    #navToggle:checked ~ .layout .sidebar {{ padding-left:10px; padding-right:10px; }}
    #navToggle:checked ~ .layout .profile {{ justify-content:center; padding:8px; }}
    #navToggle:checked ~ .layout .profile-text, #navToggle:checked ~ .layout .nav-label {{ display:none; }}
    #navToggle:checked ~ .layout .nav a, #navToggle:checked ~ .layout .nav button {{ justify-content:center; padding:11px 0; }}
    @media (max-width:820px) {{
      .layout {{ grid-template-columns:1fr; }}
      .sidebar {{ grid-column:1; grid-row:1; position:relative; height:auto; border-left:0; border-bottom:1px solid var(--line); }}
      .main {{ grid-column:1; grid-row:2; width:calc(100% - 28px); padding:14px 0; }}
      #navToggle:checked ~ .layout {{ grid-template-columns:1fr; }}
      #navToggle:checked ~ .layout .profile-text, #navToggle:checked ~ .layout .nav-label {{ display:inline; }}
      .topbar, .feature-head, .setting-row, .system-card {{ display:grid; grid-template-columns:1fr; }}
      .channel-form, .inline-form {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <input id="navToggle" type="checkbox">
  <div class="layout">
    <aside class="sidebar">
      <label class="toggle-label" for="navToggle" title="Свернуть меню">☰</label>
      <div class="profile">
        <img class="avatar" src="{avatar_url}" alt="">
        <div class="profile-text">
          <div class="profile-name">{admin_name}</div>
          <div class="profile-sub">{guild_name}</div>
        </div>
      </div>
      <nav class="nav">
        <a href="#overview"><span class="nav-icon">⌂</span><span class="nav-label">Обзор</span></a>
        <a href="#features"><span class="nav-icon">⚙</span><span class="nav-label">Функции бота</span></a>
        <a href="#databases"><span class="nav-icon">▦</span><span class="nav-label">Базы и списки</span></a>
        <a href="#birthdays"><span class="nav-icon">★</span><span class="nav-label">Дни рождения</span></a>
        <a href="#system"><span class="nav-icon">●</span><span class="nav-label">Система</span></a>
        <form method="post" action="/logout"><button type="submit"><span class="nav-icon">↩</span><span class="nav-label">Выйти</span></button></form>
      </nav>
    </aside>
    <main class="main">
      <div class="topbar" id="overview">
        <div>
          <h1>{escape(WEB_ADMIN_TITLE)}</h1>
          <p class="help">Рабочая панель управления ботом для сервера <b>{guild_name}</b>.</p>
        </div>
        <div class="actions">
          <a href="/">Обновить</a>
        </div>
      </div>
      {notice}

      <section class="card">
        <h2>Обзор</h2>
        <div class="grid">
          <div class="item"><div class="label">Бот</div><div class="value">{bot_name}</div></div>
          <div class="item"><div class="label">Аккаунт</div><div class="value">{admin_name}</div></div>
          <div class="item"><div class="label">Сервер</div><div class="value">{guild_name}<br><span class="tech-id">ID {active_guild_id or "-"}</span></div></div>
          <div class="item"><div class="label">Где запущено</div><div class="value">{_status_chip(summary["is_server_runtime"], "VPS", "Локально")}</div></div>
          <div class="item"><div class="label">Пародии</div><div class="value">Markov-only</div></div>
        </div>
      </section>

      <section class="card" id="features">
        <h2>Функции бота</h2>
        <p class="help">Здесь можно включать функции, выбирать каналы по названию и видеть текущие параметры без охоты за длинными Discord ID.</p>
        <div class="feature-stack">
          {feature_registry}
        </div>
      </section>

      {database_panel}
      {birthdays_panel}
      {toxicity_ml_panel}

      <section class="card" id="system">
        <h2>Система</h2>
        <div class="grid">
          <div class="item"><div class="label">Хост</div><div class="value">{escape(summary["hostname"])}</div></div>
          <div class="item"><div class="label">Твой IP</div><div class="value">{client_ip or "-"}</div></div>
          <div class="item"><div class="label">Разрешённые IP</div><div class="value">{allowed_ips}</div></div>
          <div class="item"><div class="label">Профилактика на VPS</div><div class="value">{_status_chip(is_full_maintenance_allowed(), "Разрешена", "Заблокирована")}</div></div>
          <div class="item"><div class="label">Ежедневный сбор</div><div class="value">{_status_chip(is_daily_markov_collection_enabled(), "Включён", "Выключен")}</div></div>
        </div>
      </section>

      {restart_card}
    </main>
  </div>
</body>
</html>"""


async def _index(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        return web.Response(
            text=f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Вход в админ-панель</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#0d1117;color:#edf2f7;padding:32px;">
<div style="max-width:420px;margin:40px auto;background:#161b22;border:1px solid #303844;padding:24px;border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,.28);">
<h2 style="margin-top:0;">Вход в админ-панель</h2>
<p style="line-height:1.5;color:#9aa7b5;">Войди через Discord. Панель откроется только если у тебя на сервере есть права администратора или управления сервером.</p>
<a href="/login" style="display:inline-block;text-decoration:none;border-radius:8px;padding:12px 16px;background:#5865f2;color:#fff;font-weight:700;">Войти через Discord</a>
<p style="margin-top:14px;color:#9aa7b5;font-size:13px;">Адрес возврата: <code style="background:#111821;color:#cbd5e1;padding:2px 6px;border-radius:6px;">{escape(get_web_admin_discord_redirect_uri())}</code></p>
</div></body></html>""",
            status=401,
            content_type="text/html",
        )
    response = web.Response(text=_render_page(request.app["bot"], request=request), content_type="text/html")
    return response


async def _login(request: web.Request) -> web.StreamResponse:
    _assert_ip_allowed(request)
    client_id = _discord_client_id(request.app["bot"])
    if not client_id:
        raise web.HTTPServiceUnavailable(text="Не настроен Discord OAuth client_id")

    state = secrets.token_urlsafe(24)
    redirect_uri = get_web_admin_discord_redirect_uri()
    response_type = "code" if DISCORD_CLIENT_SECRET else "token"
    response = web.HTTPFound(
        "https://discord.com/oauth2/authorize?"
        + urlencode(
            {
                "response_type": response_type,
                "client_id": client_id,
                "scope": "identify",
                "state": state,
                "redirect_uri": redirect_uri,
                "prompt": "none",
            }
        )
    )
    response.set_cookie(ADMIN_STATE_COOKIE, state, httponly=True, samesite="Lax", max_age=900)
    return response


async def _discord_callback(request: web.Request) -> web.StreamResponse:
    _assert_ip_allowed(request)
    if "access_token" not in request.query and "code" not in request.query:
        return web.Response(
            text="""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Вход через Discord</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#0d1117;color:#edf2f7;padding:32px;">
<div style="max-width:520px;margin:40px auto;background:#161b22;border:1px solid #303844;padding:24px;border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,.28);">
<h2 style="margin-top:0;">Завершаю вход через Discord</h2>
<p id="status" style="line-height:1.5;color:#9aa7b5;">Проверяю Discord-сессию...</p>
</div>
<script>
const params = new URLSearchParams(window.location.hash.slice(1));
const token = params.get("access_token");
const state = params.get("state");
const statusNode = document.getElementById("status");
if (!token || !state) {
  statusNode.textContent = "Discord не вернул access token. Попробуй открыть вход ещё раз.";
} else {
  fetch("/auth/discord/token", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({access_token: token, state})
  }).then((resp) => {
    if (resp.ok) {
      window.location = "/";
      return;
    }
    return resp.text().then((text) => { throw new Error(text || "Ошибка входа"); });
  }).catch((err) => {
    statusNode.textContent = err.message;
  });
}
</script>
</body></html>""",
            content_type="text/html",
        )

    if request.query.get("state") != request.cookies.get(ADMIN_STATE_COOKIE):
        raise web.HTTPBadRequest(text="Bad OAuth state")
    code = (request.query.get("code") or "").strip()
    if not code:
        raise web.HTTPBadRequest(text="Missing OAuth code")

    redirect_uri = get_web_admin_discord_redirect_uri()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=aiohttp.BasicAuth(DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status >= 400:
                raise web.HTTPUnauthorized(text="Discord token exchange failed")
            token_data = await resp.json()

        headers = {"Authorization": f"Bearer {token_data.get('access_token', '')}"}
        async with session.get(f"{DISCORD_API}/users/@me", headers=headers) as resp:
            if resp.status >= 400:
                raise web.HTTPUnauthorized(text="Discord user lookup failed")
            user_data = await resp.json()

    discord_user_id = int(user_data["id"])
    member = await _fetch_admin_member(request.app["bot"], discord_user_id)
    if not _member_has_admin_access(member):
        raise web.HTTPForbidden(text="Discord user is not a server admin")

    return _create_admin_session_response(request, user_data, member)


async def _discord_token_login(request: web.Request) -> web.StreamResponse:
    _assert_ip_allowed(request)
    data = await request.json()
    if data.get("state") != request.cookies.get(ADMIN_STATE_COOKIE):
        raise web.HTTPBadRequest(text="Bad OAuth state")
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise web.HTTPBadRequest(text="Missing Discord access token")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(f"{DISCORD_API}/users/@me", headers=headers) as resp:
            if resp.status >= 400:
                raise web.HTTPUnauthorized(text="Discord user lookup failed")
            user_data = await resp.json()

    discord_user_id = int(user_data["id"])
    member = await _fetch_admin_member(request.app["bot"], discord_user_id)
    if not _member_has_admin_access(member):
        raise web.HTTPForbidden(text="Discord user is not a server admin")

    return _create_admin_session_response(request, user_data, member)


async def _maintenance_restart(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    if request.app.get("restart_scheduled"):
        message = "Перезапуск уже запланирован. Подожди несколько секунд и обнови страницу."
    else:
        request.app["restart_scheduled"] = True
        request.app["restart_required"] = False
        request.app["restart_reasons"] = []
        request.app["log"].bind(src="admin-web").warning("Перезапуск бота запрошен из админ-панели")
        asyncio.create_task(_delayed_process_restart())
        message = "Сохранено. Бот перезапускается; страница может быть недоступна несколько секунд."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


def _feature_or_404(feature_id: str) -> dict:
    feature = FEATURES_BY_ID.get(feature_id)
    if not feature:
        raise web.HTTPNotFound(text="Неизвестная функция")
    return feature


def _mode_title(mode: str) -> str:
    return {
        "output": "канал публикаций",
        "allow": "разрешенный канал",
        "exclude": "запрещенный канал",
    }.get(mode, mode)


async def _feature_enabled(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    feature_id = request.match_info["feature"]
    feature = _feature_or_404(feature_id)
    data = await request.post()
    enabled = str(data.get("enabled") or "0").strip() in {"1", "true", "on", "yes"}
    guild_id = _admin_guild_id(request.app["bot"])
    set_feature_enabled(guild_id, feature_id, enabled)
    _mark_restart_required(request, feature, "enabled")
    message = f"{feature['title']} {'включена' if enabled else 'выключена'}."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _feature_channel(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    feature_id = request.match_info["feature"]
    feature = _feature_or_404(feature_id)
    data = await request.post()
    mode = str(data.get("mode") or "").strip()
    if mode not in feature["channel_modes"]:
        raise web.HTTPBadRequest(text="Этот тип канала не поддерживается для выбранной функции")
    channel_raw = str(data.get("channel_id") or "").strip()
    if not channel_raw.isdigit():
        raise web.HTTPBadRequest(text="ID канала должен быть числом")
    reason = str(data.get("reason") or "")
    guild_id = _admin_guild_id(request.app["bot"])
    set_feature_channel(guild_id, feature_id, int(channel_raw), mode, reason)
    _mark_restart_required(request, feature, f"{mode} channel")
    message = f"{feature['title']}: добавлен {_mode_title(mode)} {_channel_label(request.app['bot'], int(channel_raw))}."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _feature_channel_delete(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    feature_id = request.match_info["feature"]
    feature = _feature_or_404(feature_id)
    data = await request.post()
    mode = str(data.get("mode") or "").strip()
    if mode not in feature["channel_modes"]:
        raise web.HTTPBadRequest(text="Этот тип канала не поддерживается для выбранной функции")
    channel_raw = str(data.get("channel_id") or "").strip()
    if not channel_raw.isdigit():
        raise web.HTTPBadRequest(text="ID канала должен быть числом")
    guild_id = _admin_guild_id(request.app["bot"])
    deleted = clear_feature_channel(guild_id, feature_id, int(channel_raw), mode)
    _mark_restart_required(request, feature, f"{mode} channel delete")
    message = f"{feature['title']}: удалено записей для канала {_channel_label(request.app['bot'], int(channel_raw))}: {deleted}."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _feature_payload(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    feature_id = request.match_info["feature"]
    feature = _feature_or_404(feature_id)
    data = await request.post()
    raw = str(data.get("payload") or "").strip()
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text=f"Ошибка в JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Дополнительные параметры должны быть JSON-объектом")
    else:
        payload = {}
    guild_id = _admin_guild_id(request.app["bot"])
    set_feature_payload(guild_id, feature_id, payload)
    _mark_restart_required(request, feature, "payload")
    message = f"Дополнительные параметры для {feature['title']} сохранены."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _daily_summary_text(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    payload = {}
    for key, _, default, _ in SUMMARY_TEXT_FIELDS:
        value = str(data.get(key) or "").strip()
        payload[key] = value or default
    payload["game_spotlight_enabled"] = str(data.get("game_spotlight_enabled") or "").strip() in {"1", "true", "on", "yes"}
    payload["game_spotlight_label"] = str(data.get("game_spotlight_label") or "").strip() or "Задроты"
    payload["game_spotlight_game"] = str(data.get("game_spotlight_game") or "").strip()
    payload["summary_theme"] = str(data.get("summary_theme") or "neon").strip() or "neon"
    payload["summary_render_mode"] = str(data.get("summary_render_mode") or "embed").strip() or "embed"
    payload["summary_accent_color"] = str(data.get("summary_accent_color") or "").strip()
    payload["summary_thumbnail_url"] = str(data.get("summary_thumbnail_url") or "").strip()
    payload["summary_buttons_enabled"] = str(data.get("summary_buttons_enabled") or "").strip() in {"1", "true", "on", "yes"}
    payload["summary_compact_mode"] = str(data.get("summary_compact_mode") or "").strip() in {"1", "true", "on", "yes"}
    payload["game_filter_mode"] = str(data.get("game_filter_mode") or "all").strip() or "all"
    payload["daily_top_limit"] = str(data.get("daily_top_limit") or "3").strip() or "3"
    payload["period_top_limit"] = str(data.get("period_top_limit") or "5").strip() or "5"
    for key, _, _ in (*SUMMARY_DAILY_BLOCKS, *SUMMARY_PERIOD_BLOCKS):
        payload[key] = str(data.get(key) or "").strip() in {"1", "true", "on", "yes"}
        payload[f"{key}_title"] = str(data.get(f"{key}_title") or "").strip()
        payload[f"{key}_limit"] = str(data.get(f"{key}_limit") or "").strip()
    guild_id = _admin_guild_id(request.app["bot"])
    set_feature_payload(guild_id, "daily_summary", payload)
    message = "Текст итогов сервера сохранен."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _birthday_save(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    user_raw = str(data.get("user_id") or "").strip()
    birthday_raw = str(data.get("birthday") or "").strip()
    if not user_raw.isdigit():
        raise web.HTTPBadRequest(text="ID участника должен быть числом")
    try:
        birthday = validate_birthday(birthday_raw)
    except ValueError as exc:
        raise web.HTTPBadRequest(text="birthday must use DD.MM format") from exc
    session = _current_admin_session(request)
    updated_by = int((session or {}).get("discord_user_id") or 0) or int(user_raw)
    set_birthday(int(user_raw), birthday, updated_by=updated_by, source="admin_panel")
    current = get_birthday(int(user_raw))
    message = f"ДР пользователя {user_raw} сохранен: {current['birthday'] if current else birthday}. Пользователь может исправить его через /др."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _toxicity_ml_feedback(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    message_raw = str(data.get("message_id") or "").strip()
    level_raw = str(data.get("level") or "").strip()
    if not message_raw.isdigit() or level_raw not in {"0", "1", "2", "3"}:
        raise web.HTTPBadRequest(text="Invalid toxicity feedback")
    session = _current_admin_session(request)
    reviewer_id = int((session or {}).get("discord_user_id") or 0)
    with sqlite3.connect(SOCIAL_DB) as conn:
        row = conn.execute(
            "SELECT msg_snippet FROM toxicity_ml_shadow WHERE message_id=?",
            (int(message_raw),),
        ).fetchone()
        if not row:
            raise web.HTTPNotFound(text="Shadow sample not found")
        conn.execute(
            """
            INSERT INTO toxicity_ml_feedback(message_id,msg_snippet,corrected_level,reviewer_id,reviewed_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(message_id) DO UPDATE SET corrected_level=excluded.corrected_level,
                reviewer_id=excluded.reviewer_id,reviewed_at=excluded.reviewed_at
            """,
            (int(message_raw), str(row[0]), int(level_raw), reviewer_id, datetime.utcnow().isoformat()),
        )
    message = f"Пример {message_raw} размечен уровнем {level_raw}."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _birthday_delete(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    user_raw = str(data.get("user_id") or "").strip()
    if not user_raw.isdigit():
        raise web.HTTPBadRequest(text="ID участника должен быть числом")
    remove_birthday(int(user_raw))
    message = f"ДР пользователя {user_raw} удален."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _logout(request: web.Request) -> web.StreamResponse:
    _assert_ip_allowed(request)
    session_id = (request.cookies.get(ADMIN_SESSION_COOKIE) or "").strip()
    if session_id:
        request.app.get("admin_sessions", {}).pop(session_id, None)
    response = web.HTTPFound("/")
    response.del_cookie(ADMIN_SESSION_COOKIE)
    response.del_cookie(ADMIN_STATE_COOKIE)
    response.del_cookie("vipik_admin_token")
    return response


async def start_admin_panel(bot, log) -> None:
    if not WEB_ADMIN_ENABLED:
        log.bind(src="admin-web").info("Веб-панель отключена")
        return
    if not DISCORD_CLIENT_ID:
        log.bind(src="admin-web").info("Discord OAuth для веб-панели использует application id бота")
    if not DISCORD_CLIENT_SECRET:
        log.bind(src="admin-web").info("Discord OAuth для веб-панели использует token-flow без client secret")

    app = web.Application()
    app["bot"] = bot
    app["log"] = log
    app["admin_sessions"] = {}
    app["restart_required"] = False
    app["restart_reasons"] = []
    app["restart_scheduled"] = False
    app.router.add_get("/", _index)
    app.router.add_get("/login", _login)
    app.router.add_get("/auth/discord/callback", _discord_callback)
    app.router.add_post("/auth/discord/token", _discord_token_login)
    app.router.add_post("/maintenance/restart", _maintenance_restart)
    app.router.add_post("/features/{feature}/enabled", _feature_enabled)
    app.router.add_post("/features/{feature}/channel", _feature_channel)
    app.router.add_post("/features/{feature}/channel/delete", _feature_channel_delete)
    app.router.add_post("/features/{feature}/payload", _feature_payload)
    app.router.add_post("/features/daily_summary/text", _daily_summary_text)
    app.router.add_post("/user-data/birthdays", _birthday_save)
    app.router.add_post("/user-data/birthdays/delete", _birthday_delete)
    app.router.add_post("/toxicity-ml/feedback", _toxicity_ml_feedback)
    app.router.add_post("/logout", _logout)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_ADMIN_HOST, WEB_ADMIN_PORT)
    await site.start()

    bot.admin_panel_runner = runner
    log.bind(src="admin-web").info(
        f"✅ admin panel: http://{WEB_ADMIN_HOST}:{WEB_ADMIN_PORT}/"
    )
