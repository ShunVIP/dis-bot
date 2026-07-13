from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def snapshot(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create consistent SQLite snapshots for offline ML training")
    parser.add_argument("pairs", nargs="+", help="SOURCE=TARGET")
    args = parser.parse_args()
    for pair in args.pairs:
        source_raw, separator, target_raw = pair.partition("=")
        if not separator:
            raise SystemExit(f"invalid pair: {pair}")
        source = Path(source_raw).resolve()
        target = Path(target_raw).resolve()
        snapshot(source, target)
        print(f"[snapshot] {source} -> {target}")


if __name__ == "__main__":
    main()
