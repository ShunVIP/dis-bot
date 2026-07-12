from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ml_artifacts import ensure_artifact_manifest, load_artifact_manifest, register_artifact
from core.paths import MESSAGES_DB, MODELS_DIR


MARKOV_NAME = re.compile(r"^(?P<user_id>\d+)_(?P<kind>.+)\.json$")


def _message_counts() -> dict[int, int]:
    try:
        uri = Path(MESSAGES_DB).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            return {
                int(user_id): int(count)
                for user_id, count in conn.execute(
                    "SELECT user_id, COUNT(*) FROM user_messages GROUP BY user_id"
                )
            }
    except sqlite3.Error:
        return {}


def build_manifest() -> dict[str, int]:
    ensure_artifact_manifest()
    counts = _message_counts()
    registered = 0
    for path in sorted(MODELS_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        match = MARKOV_NAME.match(path.name)
        if not match:
            continue
        user_id = int(match.group("user_id"))
        register_artifact(
            pipeline="parody_markov",
            user_id=user_id,
            kind=match.group("kind"),
            path=path,
            source_rows=counts.get(user_id, 0),
            execution_location="local_pc",
            metadata={"discovered": True},
        )
        registered += 1

    for path in sorted((MODELS_DIR / "gpt").glob("*/config.json")):
        try:
            user_id = int(path.parent.name)
        except ValueError:
            continue
        register_artifact(
            pipeline="parody_gpt",
            user_id=user_id,
            kind="config",
            path=path,
            source_rows=counts.get(user_id, 0),
            execution_location="local_pc",
            metadata={"model_directory": path.parent.relative_to(MODELS_DIR).as_posix()},
        )
        registered += 1

    manifest = load_artifact_manifest(verify_files=True)
    return {
        "registered": registered,
        "artifacts": len(manifest.get("artifacts", [])),
        "available": sum(1 for item in manifest.get("artifacts", []) if item.get("available")),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a portable registry for local ML artifacts")
    parser.add_argument("--require-artifacts", action="store_true")
    args = parser.parse_args()
    result = build_manifest()
    print(
        f"[ml-manifest] registered={result['registered']} "
        f"artifacts={result['artifacts']} available={result['available']}"
    )
    if args.require_artifacts and result["available"] == 0:
        print("[ml-manifest] abort: no local model artifacts are available", file=sys.stderr)
        raise SystemExit(2)
