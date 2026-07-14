from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.conversation_service import SYSTEM_PROMPT
from core.conversation_store import list_training_examples
from core.conversation_training import build_sft_records, dataset_metadata


def build(database: str, output: str, metadata_output: str | None = None) -> dict[str, object]:
    source = str(Path(database).resolve())
    records = build_sft_records(list_training_examples(source), SYSTEM_PROMPT)
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    metadata = dataset_metadata(records, source_database=source)
    meta_target = Path(metadata_output).resolve() if metadata_output else target.with_suffix(".metadata.json")
    meta_target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**metadata, "output": str(target), "metadata_output": str(meta_target)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build consent-filtered local LLM SFT dataset")
    parser.add_argument("--database", required=True, help="Offline social.db snapshot")
    parser.add_argument("--output", required=True, help="Output JSONL")
    parser.add_argument("--metadata-output")
    args = parser.parse_args()
    print(json.dumps(build(args.database, args.output, args.metadata_output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
