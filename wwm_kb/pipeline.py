from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .db import connect, ensure_schema, now_iso
from .collectors.base import Collector
from .postprocess_v1 import postprocess_v1
from .classify_and_snippet import main as build_entity_features

# IMPORTANT:
# This expects your project to expose an async events_bus emit function at:
#   utils.events_bus.emit(event_name: str, **payload)
# If your path differs, change the import below accordingly.
from utils.events_bus import emit  # type: ignore


async def run_collectors(collectors: List[Collector], *, run_id: str) -> dict:
    ensure_schema()
    inserted = 0
    errors: list[str] = []

    with connect() as conn:
        cur = conn.cursor()
        for col in collectors:
            try:
                async for rec in col.collect():
                    cur.execute(
                        """
                        INSERT INTO raw_records(
                            source, method, entity_type, external_id,
                            title, url, payload_json, content_hash, fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec.source,
                            rec.method,
                            rec.entity_type,
                            rec.external_id,
                            rec.title,
                            rec.url,
                            rec.payload_json,
                            rec.content_hash,
                            now_iso(),
                        ),
                    )
                    inserted += 1
                conn.commit()
            except Exception as e:
                errors.append(f"{getattr(col, 'source', '?')}/{getattr(col, 'method', '?')}: {e}")

    return {"run_id": run_id, "inserted": inserted, "errors": errors}


async def postprocess_after_refresh(run_id: str) -> dict:
    """Normalize RAW records and refresh lightweight KB features."""
    try:
        postprocess_v1(run_id=run_id)
        build_entity_features()
        return {"run_id": run_id, "status": "ok"}
    except Exception as e:
        return {"run_id": run_id, "status": f"error: {e}"}


async def daily_refresh(collectors: List[Collector]) -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    result = await run_collectors(collectors, run_id=run_id)
    post = await postprocess_after_refresh(run_id)

    # Emit completion event for subscribers (logging, notifications, ML, etc.)
    await emit(
        "wwm_kb_refresh_completed",
        run_id=run_id,
        inserted=result["inserted"],
        errors=result["errors"],
        postprocess_status=post["status"],
    )
