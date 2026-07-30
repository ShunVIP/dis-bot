from __future__ import annotations

import argparse
import asyncio
import json
import os

import config  # noqa: F401 - loads KGTD.env for the production-style probe
from core.conversation_service import conversation_runtime_status, generate_reply


async def _probe(args: argparse.Namespace) -> dict[str, object]:
    if args.url:
        os.environ["LOCAL_CHAT_API_URL"] = args.url
    if args.model:
        os.environ["LOCAL_CHAT_MODEL"] = args.model
    os.environ["LOCAL_CHAT_TIMEOUT_SECONDS"] = str(args.timeout)
    reply = await generate_reply(
        guild_id=args.guild_id,
        channel_id=args.channel_id,
        user_id=args.user_id,
        display_name="runtime-probe",
        text=args.text,
    )
    return {
        "ok": reply is not None,
        "reply": None if reply is None else {
            "text": reply.text,
            "provider": reply.provider,
            "model": reply.model,
            "latency_ms": reply.latency_ms,
        },
        "status": conversation_runtime_status(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the configured private conversation model.")
    parser.add_argument("--url", default="", help="Override LOCAL_CHAT_API_URL for this probe.")
    parser.add_argument("--model", default="", help="Override LOCAL_CHAT_MODEL for this probe.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--guild-id", type=int, default=0)
    parser.add_argument("--channel-id", type=int, default=0)
    parser.add_argument("--user-id", type=int, default=0)
    parser.add_argument(
        "--text",
        default="Посоветуй одну игру любителю Souls-like. Ответь кратко по-русски.",
    )
    args = parser.parse_args()
    result = asyncio.run(_probe(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
