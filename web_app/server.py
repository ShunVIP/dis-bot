# web_app/server.py
from __future__ import annotations

import json
import os
import secrets
import base64
import hashlib
import hmac
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import aiohttp
from aiohttp import web

from config import (
    APP_API_HOST,
    APP_API_PORT,
    BOT_API_TOKEN,
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_OAUTH_SCOPES,
    DISCORD_REDIRECT_URI,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    RIOT_API_KEY,
    RIOT_PLATFORM_REGION,
    RIOT_REGIONAL_ROUTING,
    UPLOAD_MAX_MB,
)
from core.game_profiles import (
    GAME_LOL,
    PROVIDER_RIOT,
    get_game_account,
    get_player_model_profile,
    save_lol_match_features,
    save_lol_snapshot,
    save_player_model_profile,
    unlink_game_account,
    upsert_game_account,
)
from core.community_store import (
    ensure_first_owner,
    get_profile,
    get_user_roles,
    has_admin_access,
    list_members,
    list_roles,
    upsert_profile,
    upsert_role,
)
from core.profile_service import get_unified_profile, update_unified_profile
from core.lol_player_model import classify_lol_player, extract_lol_match_features
from core.ml_artifacts import load_artifact_manifest
from core.ml_insights import build_ml_insights
from core.platform_store import (
    add_general_chat_message,
    add_platform_message,
    can_access_platform_target,
    create_text_channel,
    delete_general_chat_message,
    delete_platform_message,
    edit_general_chat_message,
    edit_platform_message,
    ensure_platform_tables,
    get_server,
    get_or_create_dm,
    get_platform_message_context,
    list_activities,
    list_dm_threads,
    list_general_chat_messages,
    list_platform_messages,
    mark_dm_read,
    list_servers,
    list_text_channels,
    toggle_general_chat_reaction,
    toggle_platform_reaction,
    update_server,
)
from core.paths import PROJECT_ROOT, UPLOADS_DIR
from core.riot_client import RiotClient, RiotRouting, split_riot_id
from core.settings_store import (
    clear_feature_channel,
    get_feature_policy,
    get_feature_payload,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)
from core.web_app_store import (
    authenticate_local_user,
    consume_login_code,
    create_session,
    delete_session,
    ensure_web_tables,
    get_session_user,
    get_web_user,
    upsert_login_profile,
    upsert_web_user,
)
from core.voice_store import (
    can_access_voice_room,
    create_voice_invite,
    create_voice_room,
    get_voice_room,
    list_voice_rooms,
    redeem_voice_invite,
)

DISCORD_API = "https://discord.com/api/v10"
STATIC_DIR = PROJECT_ROOT / "web_app" / "static"
try:
    MAX_UPLOAD_BYTES = max(1, min(int(UPLOAD_MAX_MB or "25"), 200)) * 1024 * 1024
except ValueError:
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024

ALLOWED_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".pdf", ".zip"}
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_ATTEMPT_LIMIT = 10


def _json(data, status: int = 200):
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        status=status,
        content_type="application/json",
    )


def _session_id(request: web.Request) -> str:
    return request.cookies.get("vipik_session", "")


def _current_user(request: web.Request):
    return get_session_user(_session_id(request))


def _secure_cookie(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def _client_ip(request: web.Request) -> str:
    return request.remote or "unknown"


@web.middleware
async def security_middleware(request: web.Request, handler):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.path != "/api/bot/chat":
        origin = request.headers.get("Origin", "")
        if not origin or urlparse(origin).netloc != request.host:
            return _json({"error": "bad_origin"}, 403)
    try:
        response = await handler(request)
    except json.JSONDecodeError:
        response = _json({"error": "bad_json"}, 400)
    except web.HTTPException as exc:
        response = exc
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
        "connect-src 'self' https: wss:; media-src 'self' blob:; form-action 'self'"
    )
    return response


def _require_user(request: web.Request):
    user = _current_user(request)
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "auth_required"}), content_type="application/json")
    return user


def _require_admin(request: web.Request):
    user = _require_user(request)
    if not has_admin_access(user["id"]):
        raise web.HTTPForbidden(text=json.dumps({"error": "admin_required"}), content_type="application/json")
    return user


def _is_bot_authorized(request: web.Request) -> bool:
    if not BOT_API_TOKEN:
        return False
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    return secrets.compare_digest(token, BOT_API_TOKEN)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _livekit_token(identity: str, name: str, room_name: str) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": LIVEKIT_API_KEY,
        "sub": identity,
        "nbf": now - 5,
        "exp": now + 15 * 60,
        "name": name,
        "video": {
            "room": room_name,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(LIVEKIT_API_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _safe_filename(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())[:120].strip("._")
    return clean or "upload.bin"


async def index(request: web.Request):
    return web.FileResponse(STATIC_DIR / "index.html")


async def health(request: web.Request):
    return _json({"ok": True, "time": datetime.now(timezone.utc).isoformat()})


async def auth_login(request: web.Request):
    if not DISCORD_CLIENT_ID or not DISCORD_REDIRECT_URI:
        return _json({"error": "Discord OAuth is not configured"}, 500)
    state = secrets.token_urlsafe(24)
    response = web.HTTPFound(
        "https://discord.com/oauth2/authorize?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": DISCORD_CLIENT_ID,
                "scope": DISCORD_OAUTH_SCOPES or "identify guilds connections",
                "state": state,
                "redirect_uri": DISCORD_REDIRECT_URI,
                "prompt": "consent",
            }
        )
    )
    response.set_cookie(
        "vipik_oauth_state", state, httponly=True, samesite="Lax",
        secure=_secure_cookie(request), max_age=900, path="/",
    )
    raise response


async def auth_callback(request: web.Request):
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not DISCORD_REDIRECT_URI:
        return _json({"error": "Discord OAuth is not configured"}, 500)
    if request.query.get("state") != request.cookies.get("vipik_oauth_state"):
        return _json({"error": "bad_state"}, 400)
    code = request.query.get("code", "")
    if not code:
        return _json({"error": "missing_code"}, 400)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            auth=aiohttp.BasicAuth(DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status >= 400:
                return _json({"error": "token_exchange_failed", "status": resp.status}, 400)
            token_data = await resp.json()

        access_token = token_data.get("access_token", "")
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(f"{DISCORD_API}/users/@me", headers=headers) as resp:
            if resp.status >= 400:
                return _json({"error": "discord_user_failed"}, 400)
            user_data = await resp.json()

        connections = []
        async with session.get(f"{DISCORD_API}/users/@me/connections", headers=headers) as resp:
            if resp.status < 400:
                raw_connections = await resp.json()
                if isinstance(raw_connections, list):
                    connections = raw_connections

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(token_data.get("expires_in") or 604800))
    ).isoformat()
    discord_user_id = int(user_data["id"])
    upsert_web_user(
        discord_user_id,
        username=user_data.get("username", ""),
        global_name=user_data.get("global_name") or "",
        avatar=user_data.get("avatar") or "",
        access_token=access_token,
        refresh_token=token_data.get("refresh_token", ""),
        token_expires_at=expires_at,
        connections=connections,
    )
    upsert_login_profile(
        discord_user_id,
        email=user_data.get("email") or "",
        login_name=user_data.get("global_name") or user_data.get("username", ""),
    )
    ensure_first_owner(discord_user_id)
    session_id = create_session(discord_user_id)
    response = web.HTTPFound("/")
    response.set_cookie(
        "vipik_session", session_id, httponly=True, samesite="Lax",
        secure=_secure_cookie(request), max_age=14 * 86400, path="/",
    )
    response.del_cookie("vipik_oauth_state")
    raise response


async def auth_logout(request: web.Request):
    delete_session(_session_id(request))
    response = web.HTTPFound("/")
    response.del_cookie("vipik_session")
    raise response


async def auth_local_login(request: web.Request):
    now = time.monotonic()
    attempts = request.app.setdefault("login_attempts", {}).setdefault(_client_ip(request), [])
    attempts[:] = [stamp for stamp in attempts if now - stamp < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
        return _json({"error": "rate_limited"}, 429)
    attempts.append(now)
    data = await request.json()
    user = authenticate_local_user(str(data.get("email") or ""), str(data.get("password") or ""))
    if not user:
        return _json({"error": "bad_login"}, 401)
    ensure_first_owner(user["id"])
    session_id = create_session(user["id"])
    response = _json({"ok": True, "user": user})
    request.app["login_attempts"].pop(_client_ip(request), None)
    response.set_cookie(
        "vipik_session", session_id, httponly=True, samesite="Lax",
        secure=_secure_cookie(request), max_age=14 * 86400, path="/",
    )
    return response


async def auth_code_login(request: web.Request):
    now = time.monotonic()
    attempts = request.app.setdefault("login_attempts", {}).setdefault(_client_ip(request), [])
    attempts[:] = [stamp for stamp in attempts if now - stamp < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
        return _json({"error": "rate_limited"}, 429)
    attempts.append(now)
    data = await request.json()
    user = consume_login_code(str(data.get("code") or ""))
    if not user:
        return _json({"error": "bad_or_expired_code"}, 401)
    ensure_first_owner(user["id"])
    session_id = create_session(user["id"])
    request.app["login_attempts"].pop(_client_ip(request), None)
    response = _json({"ok": True, "user": user})
    response.set_cookie(
        "vipik_session", session_id, httponly=True, samesite="Lax",
        secure=_secure_cookie(request), max_age=14 * 86400, path="/",
    )
    return response


async def api_me(request: web.Request):
    user = _current_user(request)
    if not user:
        return _json({"authenticated": False})
    riot_connections = [
        item for item in user.get("connections", [])
        if item.get("type") in {"riotgames", "leagueoflegends"}
    ]
    return _json({
        "authenticated": True,
        "user": user,
        "riot_connections": riot_connections,
        "roles": get_user_roles(user["id"]),
        "is_admin": has_admin_access(user["id"]),
    })


async def api_login_profile_update(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    password = str(data.get("password") or "")
    if password and len(password) < 10:
        return _json({"error": "password_too_short"}, 400)
    upsert_login_profile(
        user["id"],
        email=str(data.get("email") or ""),
        login_name=str(data.get("login_name") or ""),
        password=password or None,
    )
    return _json({"ok": True, "user": get_session_user(_session_id(request))})


async def api_community_me(request: web.Request):
    user = _require_user(request)
    return _json({
        "profile": get_profile(user["id"]),
        "roles": get_user_roles(user["id"]),
        "all_roles": list_roles(),
    })


async def api_profile(request: web.Request):
    user = _require_user(request)
    return _json({"profile": get_unified_profile(user["id"])})


async def api_profile_update(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    if not isinstance(data, dict):
        return _json({"error": "invalid_profile_payload"}, 400)
    try:
        profile = update_unified_profile(user["id"], data)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "profile": profile})


async def api_community_me_update(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    badges = data.get("badges")
    if badges is not None and not isinstance(badges, list):
        return _json({"error": "badges_must_be_list"}, 400)
    upsert_profile(
        user["id"],
        display_name=str(data.get("display_name") or ""),
        status_text=str(data.get("status_text") or ""),
        bio=str(data.get("bio") or ""),
        accent_color=str(data.get("accent_color") or "#4fc3b1"),
        banner_preset=str(data.get("banner_preset") or "midnight"),
        avatar_decoration=str(data.get("avatar_decoration") or ""),
        badges=[str(item)[:40] for item in badges] if badges is not None else None,
    )
    return _json({"ok": True, "profile": get_profile(user["id"]), "roles": get_user_roles(user["id"])})


async def api_community_members(request: web.Request):
    _require_user(request)
    return _json({"members": list_members(), "roles": list_roles()})


async def api_community_role_upsert(request: web.Request):
    _require_admin(request)
    data = await request.json()
    try:
        role = upsert_role(
            slug=str(data.get("slug") or ""),
            name=str(data.get("name") or ""),
            color=str(data.get("color") or "#9aa7b0"),
            position=int(data.get("position") or 0),
            source="web",
        )
    except ValueError as exc:
        return _json({"error": "bad_role", "detail": str(exc)}, 400)
    return _json({"ok": True, "role": role, "roles": list_roles()})


async def api_platform_bootstrap(request: web.Request):
    user = _require_user(request)
    return _json({
        "servers": list_servers(),
        "server": get_server(0),
        "channels": list_text_channels(0),
        "dms": list_dm_threads(user["id"]),
        "activities": list_activities(),
        "members": list_members(),
    })


async def api_platform_server(request: web.Request):
    _require_user(request)
    return _json({"server": get_server(0)})


async def api_platform_server_update(request: web.Request):
    _require_admin(request)
    data = await request.json()
    server = update_server(
        0,
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        icon=str(data.get("icon") or ""),
        banner=str(data.get("banner") or "midnight"),
    )
    return _json({"ok": True, "server": server})


async def api_platform_channel_create(request: web.Request):
    _require_admin(request)
    data = await request.json()
    channel = create_text_channel(
        int(data.get("server_id") or 0),
        str(data.get("category") or "Текстовые"),
        str(data.get("name") or ""),
        str(data.get("topic") or ""),
    )
    return _json({"ok": True, "channel": channel})


async def api_platform_dm_create(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    peer_id = int(data.get("peer_id") or 0)
    if not peer_id or peer_id == user["id"]:
        return _json({"error": "bad_peer"}, 400)
    if not get_web_user(peer_id):
        return _json({"error": "peer_not_registered"}, 404)
    thread = get_or_create_dm(user["id"], peer_id, str(data.get("title") or ""))
    return _json({"ok": True, "thread": thread})


async def api_platform_dm_read(request: web.Request):
    user = _require_user(request)
    thread_id = int(request.match_info["thread_id"])
    if not mark_dm_read(thread_id, user["id"]):
        return _json({"error": "dm_forbidden"}, 403)
    return _json({"ok": True, "thread_id": thread_id})


async def api_platform_messages(request: web.Request):
    user = _require_user(request)
    scope = str(request.query.get("scope") or "channel")
    target_id = int(request.query.get("target_id") or 0)
    if not target_id:
        return _json({"messages": []})
    if not can_access_platform_target(scope, target_id, user["id"], has_admin_access(user["id"])):
        return _json({"error": "target_forbidden"}, 403)
    return _json({"messages": list_platform_messages(scope, target_id)})


async def _sse_json(request: web.Request, producer):
    _require_user(request)
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    last_payload = None
    try:
        while True:
            payload = producer()
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last_payload:
                await response.write(f"data: {encoded}\n\n".encode("utf-8"))
                last_payload = encoded
            else:
                await response.write(b": keepalive\n\n")
            await response.drain()
            await asyncio.sleep(2)
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    return response


async def api_platform_messages_stream(request: web.Request):
    user = _require_user(request)
    scope = str(request.query.get("scope") or "channel")
    target_id = int(request.query.get("target_id") or 0)
    if not target_id:
        return _json({"error": "target_required"}, 400)
    if not can_access_platform_target(scope, target_id, user["id"], has_admin_access(user["id"])):
        return _json({"error": "target_forbidden"}, 403)
    return await _sse_json(
        request,
        lambda: {"messages": list_platform_messages(scope, target_id)},
    )


async def api_platform_message_post(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    scope = str(data.get("scope") or "channel")
    target_id = int(data.get("target_id") or 0)
    content = str(data.get("content") or "")
    attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
    if not target_id:
        return _json({"error": "target_required"}, 400)
    if not can_access_platform_target(scope, target_id, user["id"], has_admin_access(user["id"])):
        return _json({"error": "target_forbidden"}, 403)
    message_id = add_platform_message(
        scope,
        target_id,
        user["id"],
        user.get("global_name") or user.get("username") or str(user["id"]),
        content,
        attachments=attachments,
    )
    return _json({"ok": True, "id": message_id})


async def api_platform_message_edit(request: web.Request):
    user = _require_user(request)
    message_id = int(request.match_info["message_id"])
    data = await request.json()
    context = get_platform_message_context(message_id)
    if not context or not can_access_platform_target(
        context["scope"], context["target_id"], user["id"], has_admin_access(user["id"])
    ):
        return _json({"error": "message_forbidden"}, 403)
    ok = edit_platform_message(
        message_id,
        user["id"],
        str(data.get("content") or ""),
        can_admin=has_admin_access(user["id"]),
    )
    return _json({"ok": ok}, 200 if ok else 403)


async def api_platform_message_delete(request: web.Request):
    user = _require_user(request)
    message_id = int(request.match_info["message_id"])
    context = get_platform_message_context(message_id)
    if not context or not can_access_platform_target(
        context["scope"], context["target_id"], user["id"], has_admin_access(user["id"])
    ):
        return _json({"error": "message_forbidden"}, 403)
    ok = delete_platform_message(message_id, user["id"], can_admin=has_admin_access(user["id"]))
    return _json({"ok": ok}, 200 if ok else 403)


async def api_platform_message_reaction(request: web.Request):
    user = _require_user(request)
    message_id = int(request.match_info["message_id"])
    data = await request.json()
    context = get_platform_message_context(message_id)
    if not context or not can_access_platform_target(
        context["scope"], context["target_id"], user["id"], has_admin_access(user["id"])
    ):
        return _json({"error": "message_forbidden"}, 403)
    active = toggle_platform_reaction(message_id, user["id"], str(data.get("emoji") or "+"))
    return _json({"ok": True, "active": active})


async def api_settings(request: web.Request):
    _require_admin(request)
    guild_id = int(request.query.get("guild_id") or 0)
    features = request.query.get("features", "birthday,wwm_guild,steam,daily_summary,parody_training,lol_profile").split(",")
    result = {}
    for feature in [item.strip() for item in features if item.strip()]:
        policy = get_feature_policy(guild_id, feature)
        result[feature] = {
            "enabled": policy.enabled,
            "output_channel_id": policy.output_channel_id,
            "allowed_channel_ids": list(policy.allowed_channel_ids),
            "excluded_channel_ids": list(policy.excluded_channel_ids),
            "extra": policy.extra or {},
        }
    return _json({"guild_id": guild_id, "features": result})


async def api_ml_status(request: web.Request):
    _require_admin(request)
    manifest = load_artifact_manifest(verify_files=True)
    artifacts = manifest.get("artifacts", [])
    return _json(
        {
            "schema_version": manifest.get("schema_version", 0),
            "updated_at": manifest.get("updated_at", ""),
            "summary": {
                "artifacts": len(artifacts),
                "available": sum(1 for item in artifacts if item.get("available")),
                "missing": sum(1 for item in artifacts if not item.get("available")),
            },
            "artifacts": artifacts,
        }
    )


async def api_ml_insights(request: web.Request):
    _require_admin(request)
    guild_id = int(request.query.get("guild_id") or 0) or None
    return _json(build_ml_insights(guild_id=guild_id))


async def api_patch_feature(request: web.Request):
    _require_admin(request)
    guild_id = int(request.match_info["guild_id"])
    feature = request.match_info["feature"]
    data = await request.json()
    if "enabled" in data:
        set_feature_enabled(guild_id, feature, bool(data["enabled"]))
    if isinstance(data.get("payload"), dict):
        set_feature_payload(guild_id, feature, data["payload"])
    return _json({"ok": True, "feature": feature, "settings": get_feature_payload(guild_id, feature)})


async def api_put_channel(request: web.Request):
    _require_admin(request)
    guild_id = int(request.match_info["guild_id"])
    feature = request.match_info["feature"]
    mode = request.match_info["mode"]
    channel_id = int(request.match_info["channel_id"])
    data = await request.json() if request.can_read_body else {}
    set_feature_channel(guild_id, feature, channel_id, mode, str(data.get("reason", "")))
    return _json({"ok": True})


async def api_delete_channel(request: web.Request):
    _require_admin(request)
    guild_id = int(request.match_info["guild_id"])
    feature = request.match_info["feature"]
    mode = request.match_info["mode"]
    channel_id = int(request.match_info["channel_id"])
    deleted = clear_feature_channel(guild_id, feature, channel_id, mode)
    return _json({"ok": True, "deleted": deleted})


async def api_chat_list(request: web.Request):
    _require_user(request)
    return _json({"messages": list_general_chat_messages(int(request.query.get("limit") or 80))})


async def api_chat_stream(request: web.Request):
    limit = int(request.query.get("limit") or 80)
    return await _sse_json(request, lambda: {"messages": list_general_chat_messages(limit)})


async def api_chat_post(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    content = str(data.get("content", "")).strip()
    guild_id = int(data.get("guild_id") or 0)
    channel_id = int(data.get("channel_id") or 0)
    attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
    message_id = add_general_chat_message(
        user["id"],
        user.get("global_name") or user.get("username") or str(user["id"]),
        content,
        guild_id=guild_id,
        channel_id=channel_id,
        source="web",
        attachments=attachments,
    )
    return _json({"ok": True, "id": message_id})


async def api_upload(request: web.Request):
    _require_user(request)
    reader = await request.multipart()
    uploaded = []
    async for part in reader:
        if part.name != "file" or not part.filename:
            continue
        original_name = _safe_filename(part.filename)
        suffix = Path(original_name).suffix.lower()[:16]
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            return _json({"error": "file_type_not_allowed", "allowed": sorted(ALLOWED_UPLOAD_SUFFIXES)}, 415)
        if len(uploaded) >= 10:
            return _json({"error": "too_many_files", "limit": 10}, 413)
        stored_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d')}_{secrets.token_hex(12)}{suffix}"
        target = UPLOADS_DIR / stored_name
        size = 0
        with target.open("wb") as handle:
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    handle.close()
                    target.unlink(missing_ok=True)
                    return _json({"error": "file_too_large", "limit": MAX_UPLOAD_BYTES}, 413)
                handle.write(chunk)
        if size == 0:
            target.unlink(missing_ok=True)
            return _json({"error": "empty_file"}, 400)
        uploaded.append({
            "url": f"/uploads/{stored_name}",
            "name": original_name,
            "content_type": part.headers.get("Content-Type", "application/octet-stream"),
            "size": size,
        })
    return _json({"ok": True, "files": uploaded})


async def api_chat_edit(request: web.Request):
    user = _require_user(request)
    message_id = int(request.match_info["message_id"])
    data = await request.json()
    ok = edit_general_chat_message(
        message_id,
        user["id"],
        str(data.get("content") or ""),
        can_admin=has_admin_access(user["id"]),
    )
    return _json({"ok": ok}, 200 if ok else 403)


async def api_chat_delete(request: web.Request):
    user = _require_user(request)
    message_id = int(request.match_info["message_id"])
    ok = delete_general_chat_message(message_id, user["id"], can_admin=has_admin_access(user["id"]))
    return _json({"ok": ok}, 200 if ok else 403)


async def api_chat_reaction(request: web.Request):
    user = _require_user(request)
    message_id = int(request.match_info["message_id"])
    data = await request.json()
    try:
        active = toggle_general_chat_reaction(message_id, user["id"], str(data.get("emoji") or "+"))
    except ValueError:
        return _json({"error": "message_forbidden"}, 403)
    return _json({"ok": True, "active": active})


async def api_lol_profile(request: web.Request):
    user = _require_user(request)
    target_id = int(request.query.get("user_id") or user["id"])
    account = get_game_account(target_id, GAME_LOL, PROVIDER_RIOT)
    model = get_player_model_profile(target_id, GAME_LOL)
    return _json({"account": account, "model": model})


async def api_lol_link(request: web.Request):
    user = _require_user(request)
    if not RIOT_API_KEY:
        return _json({"error": "riot_api_not_configured"}, 500)
    data = await request.json()
    riot_id = str(data.get("riot_id") or "").strip()
    platform = str(data.get("platform") or RIOT_PLATFORM_REGION or "ru").strip()
    regional = str(data.get("regional") or RIOT_REGIONAL_ROUTING or "europe").strip()
    if not riot_id:
        return _json({"error": "riot_id_required"}, 400)

    try:
        game_name, tag_line = split_riot_id(riot_id)
        client = RiotClient(RIOT_API_KEY, RiotRouting(platform=platform, regional=regional))
        account = await client.account_by_riot_id(game_name, tag_line)
        puuid = account.get("puuid")
        if not puuid:
            return _json({"error": "riot_account_has_no_puuid"}, 502)
        display_name = f"{account.get('gameName', game_name)}#{account.get('tagLine', tag_line)}"
        upsert_game_account(
            user["id"],
            GAME_LOL,
            PROVIDER_RIOT,
            puuid,
            display_name,
            region=client.routing.platform,
            verified=True,
        )
        return _json({"ok": True, "account": get_game_account(user["id"], GAME_LOL, PROVIDER_RIOT)})
    except Exception as exc:
        return _json({"error": "lol_link_failed", "detail": str(exc)}, 400)


async def api_lol_refresh(request: web.Request):
    user = _require_user(request)
    if not RIOT_API_KEY:
        return _json({"error": "riot_api_not_configured"}, 500)
    data = await request.json() if request.can_read_body else {}
    match_count = max(5, min(int(data.get("matches") or 20), 50))
    account = get_game_account(user["id"], GAME_LOL, PROVIDER_RIOT)
    if not account:
        return _json({"error": "lol_profile_not_linked"}, 400)

    client = RiotClient(
        RIOT_API_KEY,
        RiotRouting(
            platform=account.get("region") or RIOT_PLATFORM_REGION or "ru",
            regional=RIOT_REGIONAL_ROUTING or "europe",
        ),
    )
    puuid = account["external_id"]
    try:
        summoner = await client.summoner_by_puuid(puuid)
        ranked = await client.ranked_entries_by_puuid(puuid)
        mastery = await client.champion_mastery_top(puuid, 5)
        match_ids = await client.match_ids_by_puuid(puuid, match_count)
    except Exception as exc:
        return _json({"error": "lol_refresh_failed", "detail": str(exc)}, 400)

    match_features = []
    for match_id in match_ids:
        try:
            match = await client.match(match_id)
            item = extract_lol_match_features(match, puuid)
            if item:
                match_features.append(item)
        except Exception:
            continue

    features, labels, explanation = classify_lol_player(match_features)
    save_lol_match_features(puuid, match_features)
    snapshot = {
        "account": account,
        "summoner": summoner,
        "ranked": ranked,
        "mastery": mastery,
        "features": features,
        "labels": labels,
        "explanation": explanation,
    }
    save_lol_snapshot(user["id"], puuid, client.routing.platform, snapshot)
    save_player_model_profile(user["id"], GAME_LOL, "lol_rules_v1", features, labels, explanation)
    return _json({
        "ok": True,
        "account": get_game_account(user["id"], GAME_LOL, PROVIDER_RIOT),
        "model": get_player_model_profile(user["id"], GAME_LOL),
    })


async def api_lol_unlink(request: web.Request):
    user = _require_user(request)
    deleted = unlink_game_account(user["id"], GAME_LOL, PROVIDER_RIOT)
    return _json({"ok": True, "deleted": deleted})


async def api_voice_rooms(request: web.Request):
    user = _require_user(request)
    guild_id = 0
    return _json({"rooms": list_voice_rooms(
        guild_id,
        user_id=user["id"],
        include_private=has_admin_access(user["id"]),
    )})


async def api_voice_room_create(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    guild_id = 0
    name = str(data.get("name") or "").strip()
    if not name:
        return _json({"error": "name_required"}, 400)
    try:
        room = create_voice_room(guild_id, name, created_by=user["id"], is_private=bool(data.get("private")))
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "room": room})


async def api_voice_invite(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    guild_id = 0
    room_id = int(data.get("room_id") or 0)
    room = get_voice_room(room_id, guild_id)
    if not room:
        return _json({"error": "room_not_found"}, 404)
    if not can_access_voice_room(room_id, user["id"], is_admin=has_admin_access(user["id"])):
        return _json({"error": "room_access_denied"}, 403)
    token = create_voice_invite(
        room_id,
        user["id"],
        max_uses=10,
        is_admin=has_admin_access(user["id"]),
    )
    return _json({"ok": True, "room_id": room_id, "invite": token, "expires_in": 24 * 60 * 60})


async def api_voice_token(request: web.Request):
    user = _require_user(request)
    data = await request.json()
    guild_id = 0
    room_id = int(data.get("room_id") or 0)
    room = get_voice_room(room_id, guild_id)
    if not room:
        return _json({"error": "room_not_found"}, 404)
    if room["is_private"] and not can_access_voice_room(
        room_id, user["id"], is_admin=has_admin_access(user["id"])
    ):
        if not redeem_voice_invite(room_id, user["id"], str(data.get("invite") or "")):
            return _json({"error": "room_access_denied"}, 403)

    identity = str(user["id"])
    display_name = user.get("global_name") or user.get("username") or identity
    livekit_room = f"vipik-g{guild_id}-r{room['id']}-{room['slug']}"
    configured = bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET)
    token = _livekit_token(identity, display_name, livekit_room) if configured else ""
    return _json({
        "ok": True,
        "configured": configured,
        "livekit_url": LIVEKIT_URL,
        "token": token,
        "room_name": livekit_room,
        "room": room,
        "identity": identity,
        "display_name": display_name,
    })


async def bot_chat_ingest(request: web.Request):
    if not _is_bot_authorized(request):
        return _json({"error": "unauthorized"}, 401)
    data = await request.json()
    message_id = add_general_chat_message(
        int(data.get("discord_user_id") or 0),
        str(data.get("author_name") or "Discord"),
        str(data.get("content") or ""),
        guild_id=int(data.get("guild_id") or 0),
        channel_id=int(data.get("channel_id") or 0),
        source="discord",
    )
    return _json({"ok": True, "id": message_id})


def create_app() -> web.Application:
    ensure_web_tables()
    ensure_platform_tables()
    app = web.Application(
        client_max_size=MAX_UPLOAD_BYTES + 1024 * 1024,
        middlewares=[security_middleware],
    )
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/auth/discord", auth_login)
    app.router.add_get("/auth/discord/callback", auth_callback)
    app.router.add_get("/auth/logout", auth_logout)
    app.router.add_post("/auth/local", auth_local_login)
    app.router.add_post("/auth/code", auth_code_login)
    app.router.add_get("/api/me", api_me)
    app.router.add_patch("/api/me/login-profile", api_login_profile_update)
    app.router.add_get("/api/community/me", api_community_me)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_patch("/api/profile", api_profile_update)
    app.router.add_patch("/api/community/me", api_community_me_update)
    app.router.add_get("/api/community/members", api_community_members)
    app.router.add_post("/api/community/roles", api_community_role_upsert)
    app.router.add_get("/api/platform/bootstrap", api_platform_bootstrap)
    app.router.add_get("/api/platform/server", api_platform_server)
    app.router.add_patch("/api/platform/server", api_platform_server_update)
    app.router.add_post("/api/platform/channels", api_platform_channel_create)
    app.router.add_post("/api/platform/dms", api_platform_dm_create)
    app.router.add_post("/api/platform/dms/{thread_id}/read", api_platform_dm_read)
    app.router.add_get("/api/platform/messages", api_platform_messages)
    app.router.add_get("/api/platform/messages/stream", api_platform_messages_stream)
    app.router.add_post("/api/platform/messages", api_platform_message_post)
    app.router.add_patch("/api/platform/messages/{message_id}", api_platform_message_edit)
    app.router.add_delete("/api/platform/messages/{message_id}", api_platform_message_delete)
    app.router.add_post("/api/platform/messages/{message_id}/reactions", api_platform_message_reaction)
    app.router.add_get("/api/settings", api_settings)
    app.router.add_get("/api/ml/status", api_ml_status)
    app.router.add_get("/api/ml/insights", api_ml_insights)
    app.router.add_patch("/api/guilds/{guild_id}/features/{feature}", api_patch_feature)
    app.router.add_put("/api/guilds/{guild_id}/features/{feature}/channels/{mode}/{channel_id}", api_put_channel)
    app.router.add_delete("/api/guilds/{guild_id}/features/{feature}/channels/{mode}/{channel_id}", api_delete_channel)
    app.router.add_get("/api/chat", api_chat_list)
    app.router.add_get("/api/chat/stream", api_chat_stream)
    app.router.add_post("/api/chat", api_chat_post)
    app.router.add_patch("/api/chat/{message_id}", api_chat_edit)
    app.router.add_delete("/api/chat/{message_id}", api_chat_delete)
    app.router.add_post("/api/chat/{message_id}/reactions", api_chat_reaction)
    app.router.add_post("/api/uploads", api_upload)
    app.router.add_get("/api/lol/profile", api_lol_profile)
    app.router.add_post("/api/lol/link", api_lol_link)
    app.router.add_post("/api/lol/refresh", api_lol_refresh)
    app.router.add_post("/api/lol/unlink", api_lol_unlink)
    app.router.add_get("/api/voice/rooms", api_voice_rooms)
    app.router.add_post("/api/voice/rooms", api_voice_room_create)
    app.router.add_post("/api/voice/invite", api_voice_invite)
    app.router.add_post("/api/voice/token", api_voice_token)
    app.router.add_post("/api/bot/chat", bot_chat_ingest)
    app.router.add_static("/uploads", UPLOADS_DIR, append_version=True)
    app.router.add_static("/static", STATIC_DIR, append_version=True)
    return app


def main():
    host = APP_API_HOST or "127.0.0.1"
    port = int(APP_API_PORT or os.getenv("APP_API_PORT", "3000"))
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
