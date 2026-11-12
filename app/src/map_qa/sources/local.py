"""Local OSM roads source: ``osm_roads.parquet`` read with DuckDB (ADR-002 §3, ADR-004 §2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from map_qa.models import MapQaError, SourceMeta
from map_qa.sources import RoadsResult

REQUIRED_COLUMNS: tuple[str, ...] = ("osm_id", "maxspeed", "tags")


class LocalRoadsSource:
    def __init__(self, path: Path, chosen_by: str = "default") -> None:
        self.path = path
        self.chosen_by = chosen_by

    def read_roads(self, way_ids: list[int]) -> RoadsResult:
        if not self.path.is_file():
            raise MapQaError(f"osm_roads: file not found: {self.path}")

        con = duckdb.connect()
        try:
            source = str(self.path)
            columns = {
                row[0]
                for row in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [source]).fetchall()
            }
            for column in REQUIRED_COLUMNS:
                if column not in columns:
                    raise MapQaError(f"osm_roads: missing column '{column}' in {self.path.name}")

            total_rows = con.execute("SELECT count(*) FROM read_parquet(?)", [source]).fetchone()[0]

            ids = sorted({int(way_id) for way_id in way_ids})
            rows: list[dict[str, object]] = []
            if ids:
                result = con.execute(
                    "SELECT osm_id, maxspeed, tags FROM read_parquet(?) "
                    "WHERE osm_id IN (SELECT unnest(?)) ORDER BY osm_id",
                    [source, ids],
                ).fetchall()
                rows = [{"osm_id": osm_id, "maxspeed": maxspeed, "tags": tags} for osm_id, maxspeed, tags in result]
        finally:
            con.close()

        meta = SourceMeta(
            dataset="osm_roads",
            source_kind="local",
            location=str(self.path),
            chosen_by=self.chosen_by,
            rows=len(rows),
            read_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return RoadsResult(rows=rows, total_rows=int(total_rows), meta=meta, sql_log=[])
