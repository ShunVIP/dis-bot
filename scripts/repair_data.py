from __future__ import annotations

import argparse
import json

from core.data_catalog import audit_database, repair_wwm_orphan_features
from core.paths import WWM_DB


def main() -> int:
    parser = argparse.ArgumentParser(description="Narrow, reversible repairs for ViPik SQLite data")
    parser.add_argument("repair", choices=["wwm-orphan-features"])
    args = parser.parse_args()

    if args.repair == "wwm-orphan-features":
        before = audit_database("wwm", WWM_DB)
        repair = repair_wwm_orphan_features(WWM_DB)
        after = audit_database("wwm", WWM_DB)
        result = {
            "repair": repair,
            "before_fk_violations": len(before["foreign_key_violations"]),
            "after_fk_violations": len(after["foreign_key_violations"]),
            "integrity": after["integrity"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["integrity"] == "ok" and result["after_fk_violations"] == 0 else 1
    return 2
