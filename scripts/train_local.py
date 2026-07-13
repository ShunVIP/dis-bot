import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fun_slesh.parody_collector import get_all_user_ids, get_user_messages, get_user_stats
from core.parody_model_service import train_user_all_qualities


def train_one(user_id: int) -> bool:
    stats = get_user_stats(user_id)
    username = stats.get("username", str(user_id))
    messages = get_user_messages(user_id)
    count = len(messages)

    print(f"[train] {username} ({user_id}) -> {count} messages")
    if count < 50:
        print("[train] skip: not enough messages")
        return False

    models = train_user_all_qualities(user_id, messages)
    ready = [name for name, ok in models.items() if ok]
    print(f"[train] markov: {', '.join(ready) if ready else 'none'}")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Markov training helper for parody models")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Train all users")
    scope.add_argument("--user-id", type=int, help="Train one user by Discord user id")
    parser.add_argument("--modes", choices=["markovify"], default="markovify", help=argparse.SUPPRESS)
    args = parser.parse_args()

    print(f"[train] project: {PROJECT_ROOT}")
    print("[train] pipeline: parody_markov")

    if args.user_id:
        train_one(args.user_id)
        return

    user_ids = get_all_user_ids()
    print(f"[train] users found: {len(user_ids)}")
    trained = 0
    for idx, user_id in enumerate(user_ids, start=1):
        print(f"[train] {idx}/{len(user_ids)}")
        if train_one(user_id):
            trained += 1

    print(f"[train] done: trained {trained} users")


if __name__ == "__main__":
    main()
