from __future__ import annotations

import argparse
import json

from core.social_chat_service import (
    FEATURE_SOCIAL_CHAT,
    migrate_social_chat_consent_policy,
    normalize_social_chat_payload,
)
from core.settings_store import list_feature_settings


def build_report() -> dict[str, object]:
    rows = []
    for row in list_feature_settings(FEATURE_SOCIAL_CHAT):
        stored = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        normalized = normalize_social_chat_payload(stored)
        rows.append({
            "guild_id": int(row["guild_id"]),
            "stored": stored,
            "normalized": normalized,
            "needs_migration": stored != normalized,
        })
    return {
        "feature": FEATURE_SOCIAL_CHAT,
        "rows": rows,
        "inspected": len(rows),
        "needs_migration": sum(1 for row in rows if row["needs_migration"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize explicit-consent social chat policy")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    before = build_report()
    migration = migrate_social_chat_consent_policy() if args.apply else {"inspected": before["inspected"], "migrated": 0}
    after = build_report()
    print(json.dumps({"applied": args.apply, "before": before, "migration": migration, "after": after}, ensure_ascii=False, indent=2))
    return 0 if not after["needs_migration"] or not args.apply else 1


if __name__ == "__main__":
    raise SystemExit(main())
