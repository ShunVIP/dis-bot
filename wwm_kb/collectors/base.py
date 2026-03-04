from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional


@dataclass
class RawRecord:
    source: str
    method: str
    entity_type: str
    external_id: str
    title: Optional[str]
    url: Optional[str]
    payload_json: str
    content_hash: Optional[str] = None


class Collector:
    """Base interface for data collectors."""

    source: str
    method: str

    async def collect(self) -> AsyncIterator[RawRecord]:
        raise NotImplementedError
