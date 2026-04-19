from __future__ import annotations

from html import escape
import ipaddress

from aiohttp import web

from config import REMOTE_MODEL_API_URL, REMOTE_MODEL_API_TOKEN
from core.runtime_policy import (
    DAILY_MARKOV_RETRAIN_HOUR,
    DAILY_MARKOV_RETRAIN_MINUTE,
    WEB_ADMIN_ENABLED,
    WEB_ADMIN_HOST,
    WEB_ADMIN_PORT,
    WEB_ADMIN_ALLOWED_IPS,
    WEB_ADMIN_TITLE,
    WEB_ADMIN_TOKEN,
    is_daily_markov_collection_enabled,
    is_daily_markov_retrain_enabled,
    is_full_maintenance_allowed,
    is_gpt_training_allowed,
    is_remote_model_inference_enabled,
    policy_summary,
    set_remote_model_inference_enabled,
)


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
    token = (
        request.headers.get("X-Admin-Token")
        or request.cookies.get("vipik_admin_token")
        or ""
    ).strip()
    return bool(WEB_ADMIN_TOKEN and token == WEB_ADMIN_TOKEN and _ip_matches_allowed(_client_ip(request)))


def _status_chip(ok: bool, on_text: str = "ON", off_text: str = "OFF") -> str:
    color = "#1f8f4d" if ok else "#9b2c2c"
    text = on_text if ok else off_text
    return (
        f"<span style=\"display:inline-block;padding:6px 10px;border-radius:999px;"
        f"background:{color};color:#fff;font-weight:700;\">{escape(text)}</span>"
    )


def _render_page(bot, request: web.Request | None = None, message: str = "") -> str:
    summary = policy_summary()
    if request is not None:
        summary["client_ip"] = _client_ip(request)
    remote_configured = bool(REMOTE_MODEL_API_URL and REMOTE_MODEL_API_TOKEN)
    bot_name = escape(str(bot.user) if bot.user else "bot not ready")
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
    .muted {{ color:#64748b; }}
    code {{ background:#eef2ff; padding:2px 6px; border-radius:8px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{escape(WEB_ADMIN_TITLE)}</h1>
      <p class="help">Панель работает только по токену и ничего не включает на VPS автоматически, кроме безопасного переключателя удалённой тяжёлой модели.</p>
      {notice}
      <div class="grid">
        <div class="item"><div class="label">Бот</div><div class="value">{bot_name}</div></div>
        <div class="item"><div class="label">Хост</div><div class="value">{escape(summary["hostname"])}</div></div>
        <div class="item"><div class="label">Режим сервера</div><div class="value">{_status_chip(summary["is_server_runtime"], "VPS", "Local")}</div></div>
        <div class="item"><div class="label">Удалённый bridge</div><div class="value">{_status_chip(remote_configured, "Configured", "Not set")}</div></div>
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
      <h2>Быстрый переключатель</h2>
      <form method="post" action="/remote-models">
        <input type="hidden" name="action" value="{action}">
        <button type="submit">{escape(action_label)}</button>
      </form>
      <p class="help" style="margin-top:12px;">Этот переключатель управляет только использованием уже подключённой удалённой модели. Он не запускает обучение и не меняет файлы на диске.</p>
    </div>

    <div class="card">
      <h2>Что делать администратору</h2>
      <p class="help">
        Для локального обучения используй <code>train_models.bat</code> на ПК.<br>
        Для раздачи тяжёлой модели с ПК на VPS используй <code>enable_heavy_models.bat</code> и <code>disable_heavy_models.bat</code>.<br>
        Эту панель лучше открывать только по Tailscale или через firewall-ограничение IP.
      </p>
    </div>
  </div>
</body>
</html>"""


async def _index(request: web.Request) -> web.Response:
    _assert_ip_allowed(request)
    if not _is_authorized(request):
        return web.Response(
            text="""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Admin Login</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f7fb;padding:32px;">
<form method="post" action="/login" style="max-width:420px;margin:40px auto;background:#fff;padding:24px;border-radius:16px;box-shadow:0 12px 32px rgba(15,23,42,.08);">
<h2 style="margin-top:0;">Вход в панель</h2>
<p>Введи токен администратора.</p>
<input type="password" name="token" placeholder="WEB_ADMIN_TOKEN" style="width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px;margin-bottom:12px;">
<button type="submit" style="border:0;border-radius:10px;padding:12px 16px;background:#1d4ed8;color:#fff;font-weight:700;">Открыть панель</button>
</form></body></html>""",
            status=401,
            content_type="text/html",
        )
    response = web.Response(text=_render_page(request.app["bot"], request=request), content_type="text/html")
    response.set_cookie("vipik_admin_token", WEB_ADMIN_TOKEN, httponly=True, samesite="Strict")
    return response


async def _login(request: web.Request) -> web.StreamResponse:
    _assert_ip_allowed(request)
    data = await request.post()
    token = (data.get("token") or "").strip()
    if not WEB_ADMIN_TOKEN or token != WEB_ADMIN_TOKEN:
        raise web.HTTPUnauthorized(text="Invalid admin token")
    response = web.HTTPFound("/")
    response.set_cookie("vipik_admin_token", WEB_ADMIN_TOKEN, httponly=True, samesite="Strict")
    return response


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


async def start_admin_panel(bot, log) -> None:
    if not WEB_ADMIN_ENABLED:
        log.bind(src="admin-web").info("Веб-панель отключена")
        return
    if not WEB_ADMIN_TOKEN:
        log.bind(src="admin-web").warning("Веб-панель не запущена: WEB_ADMIN_TOKEN пустой")
        return

    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", _index)
    app.router.add_post("/login", _login)
    app.router.add_post("/remote-models", _remote_models)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_ADMIN_HOST, WEB_ADMIN_PORT)
    await site.start()

    bot.admin_panel_runner = runner
    log.bind(src="admin-web").info(
        f"✅ admin panel: http://{WEB_ADMIN_HOST}:{WEB_ADMIN_PORT}/"
    )
