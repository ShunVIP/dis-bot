from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import MODELS_DIR


MANIFEST_VERSION = 1
MANIFEST_PATH = MODELS_DIR / "manifest.json"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_manifest() -> dict[str, Any]:
    return {"schema_version": MANIFEST_VERSION, "updated_at": "", "artifacts": []}


def _safe_artifact_path(path: str | Path) -> tuple[Path, str]:
    root = MODELS_DIR.resolve()
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("artifact must be inside MODELS_DIR") from exc
    return resolved, relative


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_unlocked() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return _empty_manifest()
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_manifest()
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        return _empty_manifest()
    return data


def load_artifact_manifest(*, verify_files: bool = False) -> dict[str, Any]:
    with _LOCK:
        result = json.loads(json.dumps(_read_unlocked()))
    if verify_files:
        for artifact in result["artifacts"]:
            try:
                path, _ = _safe_artifact_path(MODELS_DIR / str(artifact.get("path") or ""))
            except ValueError:
                artifact["available"] = False
                artifact["size_matches"] = False
                artifact["hash_matches"] = False
                continue
            artifact["size_matches"] = path.is_file() and path.stat().st_size == int(artifact.get("size_bytes") or 0)
            artifact["hash_matches"] = path.is_file() and _sha256_file(path) == str(artifact.get("sha256") or "")
            artifact["available"] = bool(artifact["size_matches"] and artifact["hash_matches"])
    return result


def ensure_artifact_manifest() -> dict[str, Any]:
    with _LOCK:
        if not MANIFEST_PATH.exists():
            manifest = _empty_manifest()
            _write_unlocked(manifest)
        return json.loads(json.dumps(_read_unlocked()))


def _write_unlocked(data: dict[str, Any]) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = MANIFEST_VERSION
    data["updated_at"] = _now()
    temporary = MANIFEST_PATH.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, MANIFEST_PATH)


def register_artifact(
    *,
    pipeline: str,
    user_id: int | None,
    kind: str,
    path: str | Path,
    source_rows: int,
    execution_location: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_path, relative_path = _safe_artifact_path(path)
    if not artifact_path.is_file():
        raise FileNotFoundError(artifact_path)
    digest = _sha256_file(artifact_path)
    record = {
        "pipeline": str(pipeline).strip()[:80],
        "user_id": int(user_id) if user_id is not None else None,
        "kind": str(kind).strip()[:80],
        "version": digest[:16],
        "path": relative_path,
        "sha256": digest,
        "size_bytes": artifact_path.stat().st_size,
        "source_rows": max(0, int(source_rows)),
        "execution_location": str(execution_location).strip()[:40],
        "metadata": metadata or {},
        "trained_at": _now(),
    }
    key = (record["pipeline"], record["user_id"], record["kind"])
    with _LOCK:
        manifest = _read_unlocked()
        artifacts = [
            item
            for item in manifest["artifacts"]
            if (item.get("pipeline"), item.get("user_id"), item.get("kind")) != key
        ]
        artifacts.append(record)
        manifest["artifacts"] = sorted(
            artifacts,
            key=lambda item: (str(item.get("pipeline")), int(item.get("user_id") or 0), str(item.get("kind"))),
        )
        _write_unlocked(manifest)
    return record


def remove_artifacts(*, pipeline: str, user_id: int | None, kinds: set[str] | None = None) -> int:
    key_user = int(user_id) if user_id is not None else None
    with _LOCK:
        manifest = _read_unlocked()
        kept = []
        removed = 0
        for item in manifest["artifacts"]:
            matches = item.get("pipeline") == pipeline and item.get("user_id") == key_user
            if matches and kinds is not None:
                matches = str(item.get("kind")) in kinds
            if matches:
                removed += 1
            else:
                kept.append(item)
        if removed:
            manifest["artifacts"] = kept
            _write_unlocked(manifest)
        return removed
