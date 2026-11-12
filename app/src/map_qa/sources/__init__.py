"""Road source contract (ADR-002 §3). Adapters return the same table shape for every source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from map_qa.models import SourceMeta


@dataclass
class RoadsResult:
    rows: list[dict[str, Any]]  # each: {"osm_id": int, "maxspeed": str | None, "tags": str | None (JSON text)}
    total_rows: int  # row count of the WHOLE table (AC-43)
    meta: SourceMeta  # rows = len(rows) read; snapshot_rows is filled by inputs
    sql_log: list[dict[str, Any]]  # empty for local


class RoadsSource(Protocol):
    def read_roads(self, way_ids: list[int]) -> RoadsResult: ...
