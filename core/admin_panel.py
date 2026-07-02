from __future__ import annotations

import asyncio
import os
import secrets
import time
from datetime import datetime
import json
from html import escape
import ipaddress
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web

from config import DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, REMOTE_MODEL_API_URL, REMOTE_MODEL_API_TOKEN
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
    is_gpt_training_allowed,
    is_remote_model_inference_enabled,
    policy_summary,
    set_remote_model_inference_enabled,
)
from core.settings_store import (
    clear_feature_channel,
    get_feature_policy,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)


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
        raise web.HTTPForbidden(text="Your IP is not allowed for this admin panel")


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


ADMIN_AREAS = (
    (
        "Настройки сервера",
        "Каналы, автопосты, WWM, Steam releases, voice roles, болтовня и токсичность.",
        "Move from Discord admin commands",
    ),
    (
        "Экономика и роли",
        "Налоги, магазин ролей, награды активности, Размер-роли и временные роли.",
        "Move from Discord admin commands",
    ),
    (
        "Модели и пародии",
        "Markov, Persona, GPT, фильтры, список пользователей, статус моделей.",
        "Admin/owner only",
    ),
    (
        "Maintenance",
        "Сбор сообщений, сброс чекпоинтов, индексация, профилактика и ручные проверки.",
        "Requires confirmation",
    ),
    (
        "Fallback platform",
        "Пользователи, комнаты, чат, voice rooms, screen share и модерация сайта/app.",
        "User app integration",
    ),
)

FEATURE_REGISTRY = (
    {
        "id": "daily_summary",
        "title": "Итоги сервера",
        "group": "Настройки сервера",
        "description": "Автопостинг итогов дня, недели и месяца.",
        "channel_modes": ("output",),
        "payload_hint": '{"schedule": "23:59 MSK"}',
    },
    {
        "id": "birthday",
        "title": "Дни рождения",
        "group": "Настройки сервера",
        "description": "Поздравления, канал поздравлений и пользовательские даты.",
        "channel_modes": ("output",),
        "payload_hint": '{"timezone": "Europe/Moscow"}',
    },
    {
        "id": "wwm_guild",
        "title": "WWM гильдия",
        "group": "Настройки сервера",
        "description": "Ники WWM, карточки, приветствие и приемная.",
        "channel_modes": ("output", "allow", "exclude"),
        "payload_hint": '{"reception_channel_id": 123, "auto_nickname": true, "nickname_template": "{game_nick}"}',
    },
    {
        "id": "steam",
        "title": "Steam",
        "group": "Настройки сервера",
        "description": "Steam-профили, вишлисты, релизы и уведомления.",
        "channel_modes": ("output",),
        "payload_hint": '{"discount_min_pct": 50}',
    },
    {
        "id": "toxicity",
        "title": "Токсичность",
        "group": "Модерация",
        "description": "Детектор токсичности, пороги и исключения каналов.",
        "channel_modes": ("allow", "exclude"),
        "payload_hint": '{"threshold": 2}',
    },
    {
        "id": "social_chat",
        "title": "Болтовня",
        "group": "Модерация",
        "description": "Случайные ответы бота, шанс ответа и режимы.",
        "channel_modes": ("allow", "exclude"),
        "payload_hint": '{"chance_percent": 12, "mention_only": false}',
    },
    {
        "id": "voice_roles",
        "title": "Voice roles",
        "group": "Экономика и роли",
        "description": "Авто-роли по голосовым каналам и исключения.",
        "channel_modes": ("allow", "exclude"),
        "payload_hint": '{"enabled_roles": []}',
    },
    {
        "id": "economy",
        "title": "Экономика",
        "group": "Экономика и роли",
        "description": "Налоги, магазин, награды и персональная валюта.",
        "channel_modes": (),
        "payload_hint": '{"tax_enabled": false, "tax_rate_pct": 10, "tax_interval_h": 168}',
    },
    {
        "id": "parody_training",
        "title": "Пародии и модели",
        "group": "Модели и пародии",
        "description": "Markov, Persona, GPT, фильтры и безопасное обучение.",
        "channel_modes": ("allow", "exclude"),
        "payload_hint": '{"allow_gpt_training": false}',
        "restart_on_change": True,
    },
    {
        "id": "maintenance",
        "title": "Maintenance",
        "group": "Maintenance",
        "description": "Сбор сообщений, индексация, профилактика и ручные проверки.",
        "channel_modes": (),
        "payload_hint": '{"requires_confirmation": true}',
        "restart_on_change": True,
    },
    {
        "id": "fallback_platform",
        "title": "Fallback platform",
        "group": "Fallback platform",
        "description": "Сайт/app, чат, комнаты, voice rooms и screen share.",
        "channel_modes": (),
        "payload_hint": '{"fallback_mode": false}',
        "restart_on_change": True,
    },
)

FEATURES_BY_ID = {item["id"]: item for item in FEATURE_REGISTRY}


def _render_admin_area_registry() -> str:
    items = []
    for title, description, status in ADMIN_AREAS:
        items.append(
            "<div class=\"area\">"
            f"<div class=\"area-title\">{escape(title)}</div>"
            f"<div class=\"area-desc\">{escape(description)}</div>"
            f"<div class=\"area-status\">{escape(status)}</div>"
            "</div>"
        )
    return "\n".join(items)


def _admin_guild_id(bot) -> int:
    guilds = list(getattr(bot, "guilds", []) or [])
    return int(guilds[0].id) if guilds else 0


def _channel_list(values: tuple[int, ...] | list[int]) -> str:
    return ", ".join(str(item) for item in values) if values else "-"


def _render_mode_form(feature_id: str, mode: str, current: str) -> str:
    labels = {
        "output": "Output channel",
        "allow": "Allow channel",
        "exclude": "Exclude channel",
    }
    return f"""
        <form method="post" action="/features/{escape(feature_id)}/channel" class="inline-form">
          <input type="hidden" name="mode" value="{escape(mode)}">
          <input name="channel_id" inputmode="numeric" placeholder="{escape(labels.get(mode, mode))}">
          <input name="reason" placeholder="Комментарий">
          <button type="submit">Добавить</button>
          <button type="submit" formaction="/features/{escape(feature_id)}/channel/delete" class="button-secondary">Удалить</button>
          <span class="muted">{escape(current)}</span>
        </form>
    """


def _render_feature_registry(guild_id: int = 0) -> str:
    cards = []
    for feature in FEATURE_REGISTRY:
        feature_id = feature["id"]
        policy = get_feature_policy(guild_id, feature_id)
        output = str(policy.output_channel_id) if policy.output_channel_id else "-"
        allow = _channel_list(policy.allowed_channel_ids)
        exclude = _channel_list(policy.excluded_channel_ids)
        modes = []
        for mode in feature["channel_modes"]:
            current = {"output": output, "allow": allow, "exclude": exclude}.get(mode, "-")
            modes.append(_render_mode_form(feature_id, mode, current))
        payload = escape(json.dumps(policy.extra or {}, ensure_ascii=False, indent=2))
        restart_badge = (
            '<span class="area-status restart-badge">нужен рестарт</span>'
            if _feature_requires_restart(feature)
            else ""
        )
        cards.append(
            f"""
            <div class="feature-card">
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
                <span>{_status_chip(policy.enabled, "Enabled", "Disabled")}</span>
                <span class="area-status">{escape(feature["group"])}</span>
                {restart_badge}
              </div>
              <div class="channel-grid">
                <div><b>Output</b><br><code>{escape(output)}</code></div>
                <div><b>Allow</b><br><code>{escape(allow)}</code></div>
                <div><b>Exclude</b><br><code>{escape(exclude)}</code></div>
              </div>
              {''.join(modes) if modes else '<p class="muted">У этой зоны пока нет channel policy.</p>'}
              <details>
                <summary>Payload</summary>
                <form method="post" action="/features/{escape(feature_id)}/payload" class="payload-form">
                  <textarea name="payload" placeholder="{escape(feature["payload_hint"])}">{payload}</textarea>
                  <button type="submit">Сохранить JSON</button>
                </form>
              </details>
            </div>
            """
        )
    return "\n".join(cards)


def _render_birthdays_panel(bot) -> str:
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
                f"<td>{escape(name)}<br><code>{user_id}</code></td>"
                f"<td><b>{escape(str(row['birthday']))}</b></td>"
                f"<td>{escape(source)}<br><code>{updated_by}</code></td>"
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
    <div class="card">
      <h2>Пользовательские ДР</h2>
      <p class="help">Админка и Discord-команда <code>/др</code> пишут в одну базу. Админ может заполнить дату за участника, но сам участник всегда может исправить свою дату через бота.</p>
      <form method="post" action="/user-data/birthdays" class="inline-form">
        <input name="user_id" inputmode="numeric" placeholder="Discord user id">
        <input name="birthday" placeholder="ДД.ММ">
        <input name="reason" placeholder="Комментарий">
        <button type="submit">Сохранить ДР</button>
      </form>
      <table class="data-table">
        <thead><tr><th>Участник</th><th>Дата</th><th>Источник</th><th></th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
    """


def _render_page(bot, request: web.Request | None = None, message: str = "") -> str:
    summary = policy_summary()
    if request is not None:
        summary["client_ip"] = _client_ip(request)
    admin_session = _current_admin_session(request) if request is not None else None
    remote_configured = bool(REMOTE_MODEL_API_URL and REMOTE_MODEL_API_TOKEN)
    bot_name = escape(str(bot.user) if bot.user else "bot not ready")
    admin_name = escape(_admin_display_name(admin_session))
    client_ip = escape(str(summary.get("client_ip", "")))
    allowed_ips = escape(str(summary.get("web_admin_allowed_ips", "") or "not set"))
    notice = (
        f"<div style=\"padding:12px 16px;background:#eef6ff;border:1px solid #c7def8;"
        f"border-radius:12px;margin-bottom:16px;\">{escape(message)}</div>"
        if message
        else ""
    )
    action = "disable" if is_remote_model_inference_enabled() else "enable"
    action_label = "Отключить удалённую тяжёлую модель" if action == "disable" else "Включить удалённую тяжёлую модель"
    admin_area_registry = _render_admin_area_registry()
    active_guild_id = _admin_guild_id(bot)
    feature_registry = _render_feature_registry(active_guild_id)
    birthdays_panel = _render_birthdays_panel(bot)
    restart_card = _render_restart_card(request)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(WEB_ADMIN_TITLE)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; background:#f4f7fb; color:#1a202c; margin:0; }}
    .wrap {{ max-width:920px; margin:32px auto; padding:0 16px; }}
    .card {{ background:#fff; border-radius:18px; padding:22px; box-shadow:0 12px 32px rgba(15,23,42,.08); margin-bottom:18px; }}
    h1, h2 {{ margin:0 0 12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
    .item {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:14px; padding:14px; }}
    .label {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:#64748b; margin-bottom:8px; }}
    .value {{ font-size:16px; font-weight:700; }}
    .help {{ color:#475569; line-height:1.5; }}
    button {{ border:0; border-radius:12px; padding:12px 16px; font-size:15px; font-weight:700; cursor:pointer; background:#1d4ed8; color:#fff; }}
    input, textarea {{ border:1px solid #cbd5e1; border-radius:10px; padding:10px 12px; font:inherit; }}
    textarea {{ min-height:88px; width:100%; box-sizing:border-box; font-family:Consolas, monospace; }}
    .button-secondary {{ background:#475569; }}
    .button-danger {{ background:#b91c1c; }}
    .actions {{ display:flex; gap:12px; flex-wrap:wrap; }}
    .actions form {{ margin:0; }}
    .actions a {{ display:inline-block; text-decoration:none; border-radius:12px; padding:12px 16px; font-size:15px; font-weight:700; background:#475569; color:#fff; }}
    .muted {{ color:#64748b; }}
    code {{ background:#eef2ff; padding:2px 6px; border-radius:8px; }}
    ul {{ margin:0; padding-left:20px; color:#475569; line-height:1.7; }}
    .area {{ border:1px solid #e2e8f0; border-radius:14px; padding:14px; background:#f8fafc; }}
    .area-title {{ font-weight:800; margin-bottom:6px; }}
    .area-desc {{ color:#475569; line-height:1.45; }}
    .area-status {{ display:inline-block; margin-top:10px; padding:4px 8px; border-radius:999px; background:#eef2ff; color:#3730a3; font-size:12px; font-weight:700; }}
    .feature-stack {{ display:grid; gap:14px; }}
    .feature-card {{ border:1px solid #e2e8f0; border-radius:14px; padding:16px; background:#fff; }}
    .feature-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .feature-meta {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:12px; }}
    .channel-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin:14px 0; }}
    .inline-form {{ display:grid; grid-template-columns:minmax(120px,1fr) minmax(120px,1fr) auto auto auto; gap:8px; align-items:center; margin:8px 0; }}
    .payload-form {{ display:grid; gap:10px; margin-top:10px; }}
    .data-table {{ width:100%; border-collapse:collapse; margin-top:14px; }}
    .data-table th, .data-table td {{ border-top:1px solid #e2e8f0; padding:10px; text-align:left; vertical-align:top; }}
    .data-table th {{ color:#475569; font-size:13px; }}
    @media (max-width:720px) {{ .feature-head, .inline-form {{ display:grid; grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{escape(WEB_ADMIN_TITLE)}</h1>
      <p class="help">Панель открывается через Discord OAuth и пускает только участников сервера с правами Administrator или Manage Server.</p>
      {notice}
      <div class="grid">
        <div class="item"><div class="label">Бот</div><div class="value">{bot_name}</div></div>
        <div class="item"><div class="label">Вход</div><div class="value">{admin_name}</div></div>
        <div class="item"><div class="label">Хост</div><div class="value">{escape(summary["hostname"])}</div></div>
        <div class="item"><div class="label">Режим сервера</div><div class="value">{_status_chip(summary["is_server_runtime"], "VPS", "Local")}</div></div>
        <div class="item"><div class="label">Удалённый bridge</div><div class="value">{_status_chip(remote_configured, "Configured", "Not set")}</div></div>
        <div class="item"><div class="label">Guild settings</div><div class="value"><code>{active_guild_id or "-"}</code></div></div>
        <div class="item"><div class="label">Твой IP</div><div class="value"><code>{client_ip or "-"}</code></div></div>
        <div class="item"><div class="label">Разрешённые IP</div><div class="value"><code>{allowed_ips}</code></div></div>
      </div>
    </div>

    <div class="card">
      <h2>Защитная политика</h2>
      <div class="grid">
        <div class="item"><div class="label">GPT-обучение на сервере</div><div class="value">{_status_chip(is_gpt_training_allowed(), "Allowed", "Blocked")}</div></div>
        <div class="item"><div class="label">Полная профилактика на сервере</div><div class="value">{_status_chip(is_full_maintenance_allowed(), "Allowed", "Blocked")}</div></div>
        <div class="item"><div class="label">Удалённая тяжёлая модель</div><div class="value">{_status_chip(is_remote_model_inference_enabled(), "Enabled", "Disabled")}</div></div>
        <div class="item"><div class="label">Ежедневный Markov</div><div class="value">{_status_chip(is_daily_markov_retrain_enabled(), "Enabled", "Disabled")}</div></div>
        <div class="item"><div class="label">Ежедневный сбор</div><div class="value">{_status_chip(is_daily_markov_collection_enabled(), "Enabled", "Disabled")}</div></div>
      </div>
      <p class="help">По умолчанию серверу запрещено GPT-дообучение и полная профилактика. Это защищает VPS от перегруза и случайного запуска тяжёлых задач из Discord. Безопасный цикл Markov можно включить отдельно на время {DAILY_MARKOV_RETRAIN_HOUR:02d}:{DAILY_MARKOV_RETRAIN_MINUTE:02d} МСК.</p>
    </div>

    <div class="card">
      <h2>Куда переезжают админские функции</h2>
      <p class="help">Пользовательский Discord-бот должен оставаться чистым. Эти зоны постепенно становятся полноценными разделами админ-панели/app, а не пунктами обычного меню.</p>
      <div class="grid">
        {admin_area_registry}
      </div>
    </div>

    <div class="card">
      <h2>Feature settings</h2>
      <p class="help">Первый рабочий слой будущей админки: здесь хранятся включение фич, каналы вывода, allow/exclude-каналы и JSON payload в общей таблице <code>core.settings_store</code>. Сейчас применяется к guild <code>{active_guild_id or "-"}</code>.</p>
      <div class="feature-stack">
        {feature_registry}
      </div>
    </div>

    {birthdays_panel}

    <div class="card">
      <h2>Быстрый переключатель</h2>
      <div class="actions">
        <form method="post" action="/remote-models">
          <input type="hidden" name="action" value="{action}">
          <button type="submit">{escape(action_label)}</button>
        </form>
        <a href="/">Обновить страницу</a>
        <form method="post" action="/logout">
          <button type="submit" class="button-secondary">Выйти</button>
        </form>
      </div>
      <p class="help" style="margin-top:12px;">Этот переключатель управляет только использованием уже подключённой удалённой модели. Он не запускает обучение и не меняет файлы на диске.</p>
    </div>

    {restart_card}

    <div class="card">
      <h2>Что можно делать здесь</h2>
      <ul>
        <li>Смотреть статус защиты, bridge и режима сервера.</li>
        <li>Включать и выключать использование удалённой тяжёлой модели для текущего процесса бота.</li>
        <li>Открывать панель через Discord OAuth с проверкой прав администратора сервера.</li>
      </ul>
    </div>

    <div class="card">
      <h2>Что остаётся в локальном GUI на ПК</h2>
      <ul>
        <li>Локальное обучение моделей на ПК.</li>
        <li>Синхронизация <code>messages.db</code> с VPS.</li>
        <li>Отправка лёгких моделей и баз на VPS.</li>
        <li>Git: <code>status</code>, <code>pull</code>, <code>commit</code>, <code>push</code>.</li>
        <li>Установка нового VPS с нуля и локальная диагностика bridge.</li>
      </ul>
      <p class="help" style="margin-top:12px;">
        Полный функционал центра нельзя безопасно перенести в эту веб-панель, потому что часть действий должна запускаться именно на твоём ПК, а не на VPS.
      </p>
    </div>

    <div class="card">
      <h2>Быстрые подсказки</h2>
      <ul>
        <li>Если нужен локальный training: запусти <code>scripts/bot_control_gui.py</code> и кнопку обучения.</li>
        <li>Если нужен Git update на ПК: используй локальный GUI и кнопку <code>Git pull</code>.</li>
        <li>Если нужно включить тяжёлую GPT с ПК для VPS: сначала включи bridge в локальном GUI, потом используй переключатель на этой панели.</li>
      </ul>
    </div>
  </div>
</body>
</html>"""


async def _index(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        return web.Response(
            text=f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Admin Login</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f7fb;padding:32px;">
<div style="max-width:420px;margin:40px auto;background:#fff;padding:24px;border-radius:16px;box-shadow:0 12px 32px rgba(15,23,42,.08);">
<h2 style="margin-top:0;">Вход в админ-панель</h2>
<p style="line-height:1.5;color:#475569;">Войди через Discord. Панель откроется только если у тебя на сервере есть права администратора или управления сервером.</p>
<a href="/login" style="display:inline-block;text-decoration:none;border-radius:10px;padding:12px 16px;background:#5865f2;color:#fff;font-weight:700;">Войти через Discord</a>
<p style="margin-top:14px;color:#64748b;font-size:13px;">Redirect URI: <code>{escape(get_web_admin_discord_redirect_uri())}</code></p>
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
        raise web.HTTPServiceUnavailable(text="Discord OAuth client_id is not configured")

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
            text="""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Discord Login</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f7fb;padding:32px;">
<div style="max-width:520px;margin:40px auto;background:#fff;padding:24px;border-radius:16px;box-shadow:0 12px 32px rgba(15,23,42,.08);">
<h2 style="margin-top:0;">Завершаю вход через Discord</h2>
<p id="status" style="line-height:1.5;color:#475569;">Проверяю Discord-сессию...</p>
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


async def _remote_models(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    action = (data.get("action") or "").strip().lower()
    if action == "enable":
        set_remote_model_inference_enabled(True)
        message = "Удалённая тяжёлая модель включена для текущего процесса бота."
    elif action == "disable":
        set_remote_model_inference_enabled(False)
        message = "Удалённая тяжёлая модель отключена для текущего процесса бота."
    else:
        message = "Неизвестное действие."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


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
        raise web.HTTPNotFound(text="Unknown feature")
    return feature


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
    message = f"Фича {feature_id} {'включена' if enabled else 'выключена'}."
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
        raise web.HTTPBadRequest(text="Unsupported channel mode for this feature")
    channel_raw = str(data.get("channel_id") or "").strip()
    if not channel_raw.isdigit():
        raise web.HTTPBadRequest(text="channel_id must be numeric")
    reason = str(data.get("reason") or "")
    guild_id = _admin_guild_id(request.app["bot"])
    set_feature_channel(guild_id, feature_id, int(channel_raw), mode, reason)
    _mark_restart_required(request, feature, f"{mode} channel")
    message = f"Канал {channel_raw} добавлен в {feature_id}:{mode}."
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
        raise web.HTTPBadRequest(text="Unsupported channel mode for this feature")
    channel_raw = str(data.get("channel_id") or "").strip()
    if not channel_raw.isdigit():
        raise web.HTTPBadRequest(text="channel_id must be numeric")
    guild_id = _admin_guild_id(request.app["bot"])
    deleted = clear_feature_channel(guild_id, feature_id, int(channel_raw), mode)
    _mark_restart_required(request, feature, f"{mode} channel delete")
    message = f"Удалено записей {feature_id}:{mode}: {deleted}."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _feature_payload(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    feature_id = request.match_info["feature"]
    _feature_or_404(feature_id)
    data = await request.post()
    raw = str(data.get("payload") or "").strip()
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text=f"Invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Payload must be a JSON object")
    else:
        payload = {}
    guild_id = _admin_guild_id(request.app["bot"])
    set_feature_payload(guild_id, feature_id, payload)
    _mark_restart_required(request, _feature_or_404(feature_id), "payload")
    message = f"Payload для {feature_id} сохранен."
    return web.Response(text=_render_page(request.app["bot"], request=request, message=message), content_type="text/html")


async def _birthday_save(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    user_raw = str(data.get("user_id") or "").strip()
    birthday_raw = str(data.get("birthday") or "").strip()
    if not user_raw.isdigit():
        raise web.HTTPBadRequest(text="user_id must be numeric")
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


async def _birthday_delete(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        raise web.HTTPUnauthorized(text="Admin token required")
    data = await request.post()
    user_raw = str(data.get("user_id") or "").strip()
    if not user_raw.isdigit():
        raise web.HTTPBadRequest(text="user_id must be numeric")
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
    app.router.add_post("/remote-models", _remote_models)
    app.router.add_post("/maintenance/restart", _maintenance_restart)
    app.router.add_post("/features/{feature}/enabled", _feature_enabled)
    app.router.add_post("/features/{feature}/channel", _feature_channel)
    app.router.add_post("/features/{feature}/channel/delete", _feature_channel_delete)
    app.router.add_post("/features/{feature}/payload", _feature_payload)
    app.router.add_post("/user-data/birthdays", _birthday_save)
    app.router.add_post("/user-data/birthdays/delete", _birthday_delete)
    app.router.add_post("/logout", _logout)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_ADMIN_HOST, WEB_ADMIN_PORT)
    await site.start()

    bot.admin_panel_runner = runner
    log.bind(src="admin-web").info(
        f"✅ admin panel: http://{WEB_ADMIN_HOST}:{WEB_ADMIN_PORT}/"
    )
