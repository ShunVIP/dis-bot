from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.data_catalog import audit_all, ml_data_manifest, write_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit of ViPik SQLite databases")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    if args.output:
        report = write_audit(args.output)
    else:
        report = audit_all()
        report["ml_manifest"] = ml_data_manifest(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(db["integrity"] in {"ok", "missing"} for db in report["databases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
