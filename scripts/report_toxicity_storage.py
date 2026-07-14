from __future__ import annotations

import argparse
import json

from core import toxicity_store


def build_report(database: str | None = None) -> dict[str, object]:
    return toxicity_store.inspect_toxicity_storage(database)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report toxicity tables without changing their contents.")
    parser.add_argument("--database", help="Optional social.db or backup path")
    args = parser.parse_args()
    print(json.dumps(build_report(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
