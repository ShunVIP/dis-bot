from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from aiohttp import ClientSession

from core.web_app_store import create_session, upsert_web_user


FIRST_ID = 170_192_388_013_293_569
SECOND_ID = 379_371_451_079_327_748
OUTSIDER_ID = 444_444_444_444_444_444


async def _json_request(
    session: ClientSession,
    method: str,
    url: str,
    *,
    cookie: str,
    origin: str,
    expected: int = 200,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Cookie": f"vipik_session={cookie}", "Origin": origin}
    async with session.request(method, url, json=payload, headers=headers) as response:
        body = await response.json()
        if response.status != expected:
            raise RuntimeError(f"{method} {url}: expected {expected}, got {response.status}: {body}")
        return body


async def smoke(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    for user_id, username, global_name in (
        (FIRST_ID, "vipik-smoke-first", "Smoke First"),
        (SECOND_ID, "vipik-smoke-second", "Smoke Second"),
        (OUTSIDER_ID, "vipik-smoke-outsider", "Smoke Outsider"),
    ):
        upsert_web_user(user_id, username, global_name=global_name)
    first_session = create_session(FIRST_ID)
    second_session = create_session(SECOND_ID)
    outsider_session = create_session(OUTSIDER_ID)

    checks: list[str] = []
    async with ClientSession() as session:
        me = await _json_request(session, "GET", f"{base}/api/me", cookie=first_session, origin=base)
        if me.get("user", {}).get("id") != str(FIRST_ID):
            raise RuntimeError(f"snowflake was not preserved: {me.get('user', {}).get('id')!r}")
        checks.append("auth_snowflake")

        updated = await _json_request(
            session,
            "PATCH",
            f"{base}/api/profile",
            cookie=first_session,
            origin=base,
            payload={
                "community": {
                    "display_name": "Smoke First",
                    "status_text": "two-account smoke",
                    "bio": "temporary isolated smoke profile",
                    "accent_color": "#4fc3b1",
                },
                "birthday": "03.08",
                "economy": {"gender": "male", "age_confirmed": True},
                "ai": {"memory_opt_in": False, "training_opt_in": False, "gamer_tags": "mmo, souls"},
            },
        )
        profile = updated.get("profile", {})
        if profile.get("community", {}).get("status_text") != "two-account smoke":
            raise RuntimeError("profile update was not persisted")
        checks.append("unified_profile")

        bootstrap = await _json_request(
            session, "GET", f"{base}/api/platform/bootstrap", cookie=first_session, origin=base
        )
        member_ids = {item.get("id") for item in bootstrap.get("members", [])}
        if not {str(FIRST_ID), str(SECOND_ID), str(OUTSIDER_ID)}.issubset(member_ids):
            raise RuntimeError(f"bootstrap snowflakes missing: {sorted(member_ids)}")
        checks.append("platform_bootstrap")

        dm_created = await _json_request(
            session,
            "POST",
            f"{base}/api/platform/dms",
            cookie=first_session,
            origin=base,
            payload={"peer_id": str(SECOND_ID)},
        )
        thread = dm_created.get("thread", {})
        if thread.get("peer_id") != str(SECOND_ID):
            raise RuntimeError("DM peer snowflake was not preserved")
        thread_id = int(thread["id"])

        await _json_request(
            session,
            "POST",
            f"{base}/api/platform/messages",
            cookie=first_session,
            origin=base,
            payload={"scope": "dm", "target_id": thread_id, "content": "two-account-live-smoke"},
        )
        await _json_request(
            session,
            "GET",
            f"{base}/api/platform/messages?scope=dm&target_id={thread_id}",
            cookie=outsider_session,
            origin=base,
            expected=403,
        )
        second_bootstrap = await _json_request(
            session, "GET", f"{base}/api/platform/bootstrap", cookie=second_session, origin=base
        )
        dm = next(item for item in second_bootstrap.get("dms", []) if int(item["id"]) == thread_id)
        if dm.get("peer_id") != str(FIRST_ID) or int(dm.get("unread_count") or 0) != 1:
            raise RuntimeError(f"unexpected DM unread state: {dm}")
        messages = await _json_request(
            session,
            "GET",
            f"{base}/api/platform/messages?scope=dm&target_id={thread_id}",
            cookie=second_session,
            origin=base,
        )
        if messages.get("messages", [{}])[0].get("content") != "two-account-live-smoke":
            raise RuntimeError("DM content was not visible to the peer")
        await _json_request(
            session,
            "POST",
            f"{base}/api/platform/dms/{thread_id}/read",
            cookie=second_session,
            origin=base,
            payload={},
        )
        second_bootstrap = await _json_request(
            session, "GET", f"{base}/api/platform/bootstrap", cookie=second_session, origin=base
        )
        dm = next(item for item in second_bootstrap.get("dms", []) if int(item["id"]) == thread_id)
        if int(dm.get("unread_count") or 0) != 0:
            raise RuntimeError(f"DM read marker failed: {dm}")
        checks.extend(["dm_membership", "dm_unread"])

        room_payload = await _json_request(
            session,
            "POST",
            f"{base}/api/voice/rooms",
            cookie=first_session,
            origin=base,
            payload={"name": "Smoke Private Voice", "private": True},
        )
        room_id = int(room_payload["room"]["id"])
        await _json_request(
            session,
            "POST",
            f"{base}/api/voice/token",
            cookie=outsider_session,
            origin=base,
            expected=403,
            payload={"room_id": room_id},
        )
        invite_payload = await _json_request(
            session,
            "POST",
            f"{base}/api/voice/invite",
            cookie=first_session,
            origin=base,
            payload={"room_id": room_id},
        )
        token_payload = await _json_request(
            session,
            "POST",
            f"{base}/api/voice/token",
            cookie=second_session,
            origin=base,
            payload={"room_id": room_id, "invite": invite_payload["invite"]},
        )
        if token_payload.get("identity") != str(SECOND_ID):
            raise RuntimeError("voice identity snowflake was not preserved")
        if token_payload.get("configured") and not token_payload.get("token"):
            raise RuntimeError("configured LiveKit did not issue a token")
        checks.extend(["voice_private_boundary", "voice_invite_token"])

    return {"ok": True, "checks": checks, "checks_passed": len(checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-account smoke for an isolated ViPik web runtime")
    parser.add_argument("--base-url", default="http://127.0.0.1:3300")
    parser.add_argument(
        "--allow-production-data",
        action="store_true",
        help="Required when DATABASE_DIR does not clearly point to a temporary smoke directory",
    )
    args = parser.parse_args()

    from core.paths import DATABASE_DIR

    normalized = str(DATABASE_DIR).replace("\\", "/").lower()
    if "vipik-web-smoke" not in normalized and not args.allow_production_data:
        parser.error("refusing to create smoke users outside a vipik-web-smoke temporary DATABASE_DIR")
    print(json.dumps(asyncio.run(smoke(args.base_url)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
