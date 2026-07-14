from __future__ import annotations

import argparse
import json

from core.reputation_store import inspect_reputation_storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only reputation/mood storage report.")
    parser.add_argument("--database", help="Optional social.db or backup path")
    args = parser.parse_args()
    print(json.dumps(inspect_reputation_storage(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
