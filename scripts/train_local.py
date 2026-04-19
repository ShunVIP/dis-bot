import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fun_slesh.parody_collector import get_all_user_ids, get_user_messages, get_user_stats
from fun_slesh.parody_engine import train_user_all_qualities
from fun_slesh.parody_persona import PERSONA_DB, PERSONA_OK, build_persona, save_persona
from fun_slesh.parody_gpt import GPT_OK, fine_tune_user


async def train_one(user_id: int, modes: str) -> bool:
    stats = get_user_stats(user_id)
    username = stats.get("username", str(user_id))
    messages = get_user_messages(user_id)
    count = len(messages)

    print(f"[train] {username} ({user_id}) -> {count} messages")
    if count < 50:
        print("[train] skip: not enough messages")
        return False

    if modes in ("all", "markovify"):
        mk = train_user_all_qualities(user_id, messages)
        ready = [name for name, ok in mk.items() if ok]
        print(f"[train] markovify: {', '.join(ready) if ready else 'none'}")

    if modes in ("all", "persona"):
        if PERSONA_OK:
            profile = build_persona(user_id, messages)
            save_persona(user_id, username, profile, count)
            print("[train] persona: ok")
        else:
            print("[train] persona: unavailable")

    if modes in ("all", "gpt"):
        if not GPT_OK:
            print("[train] gpt: unavailable")
        elif count < 200:
            print("[train] gpt: skipped, need at least 200 messages")
        else:
            ok = await fine_tune_user(user_id, messages, epochs=3)
            print(f"[train] gpt: {'ok' if ok else 'failed'}")

    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Local training helper for parody models")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Train all users")
    scope.add_argument("--user-id", type=int, help="Train one user by Discord user id")
    parser.add_argument("--modes", choices=["all", "markovify", "persona", "gpt"], default="all")
    args = parser.parse_args()

    print(f"[train] project: {PROJECT_ROOT}")
    print(f"[train] persona db: {PERSONA_DB}")
    print(f"[train] modes: {args.modes}")

    if args.user_id:
        await train_one(args.user_id, args.modes)
        return

    user_ids = get_all_user_ids()
    print(f"[train] users found: {len(user_ids)}")
    trained = 0
    for idx, user_id in enumerate(user_ids, start=1):
        print(f"[train] {idx}/{len(user_ids)}")
        if await train_one(user_id, args.modes):
            trained += 1

    print(f"[train] done: trained {trained} users")


if __name__ == "__main__":
    asyncio.run(main())
