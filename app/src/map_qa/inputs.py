"""Local input loader: signs, nearest OSM links, source metadata, crops and OSM road tags.

ADR-002 §3 (datasets, local paths), ADR-000 §8 (explicit types: every CSV is read as text and
converted here; empty means missing, never 0), ADR-004 §2 and §4. Every validation error is a
``MapQaError`` raised before roads are read.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from map_qa import models
from map_qa.models import InputBundle, MapQaError, OsmSpeedTags, RoadLink, Sign, SourceMeta
from map_qa.sources import RoadsSource
from map_qa.sources.local import LocalRoadsSource

SIGNS_COLUMNS = ("photo_id", "source", "area", "sequence", "creator", "license", "source_url", "mapillary_value")
NEAREST_COLUMNS = ("photo_id", "osm_way_id", "distance_m", "match_flags", "candidate_count")

SIGNS_REL = "signs/signs.csv"
NEAREST_REL = "signs/nearest_osm.csv"
SOURCES_REL = "sources.json"
CROPS_REL_DIR = "signs/crops"
PHOTOS_REL_DIR = "signs/photos"

_IMPLICIT_VALUE = re.compile(r"^[A-Za-z]{2}:")


def implicit_limit(tags: dict[str, Any]) -> tuple[str | None, str | None]:
    """First implicit-limit tag (ADR-004 §4 order) whose value starts with a country code, e.g. ``PL:rural``."""
    for key in models.IMPLICIT_TAGS:
        value = tags.get(key)
        if isinstance(value, str) and _IMPLICIT_VALUE.match(value):
            return key, value
    return None, None


def load_inputs(data_dir: Path, roads_source: RoadsSource, chosen_by: dict[str, str] | None = None) -> InputBundle:
    chosen = chosen_by or {}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hashes: dict[str, str] = {}

    signs_path = _require_file(data_dir, SIGNS_REL, "signs")
    nearest_path = _require_file(data_dir, NEAREST_REL, "nearest_osm")
    sources_path = _require_file(data_dir, SOURCES_REL, "source_meta")

    sign_columns, sign_rows = _read_csv(signs_path)
    _check_columns(sign_columns, SIGNS_COLUMNS, "signs", signs_path.name)
    near_columns, near_rows = _read_csv(nearest_path)
    _check_columns(near_columns, NEAREST_COLUMNS, "nearest_osm", nearest_path.name)

    hashes[SIGNS_REL] = _sha256(signs_path)
    hashes[NEAREST_REL] = _sha256(nearest_path)

    sign_dicts = [_row_dict(sign_columns, row) for row in sign_rows]
    near_dicts = [_row_dict(near_columns, row) for row in near_rows]
    _check_unique(sign_dicts, "signs")
    _check_unique(near_dicts, "nearest_osm")

    snapshot_rows, extract_date = _read_sources(sources_path)
    hashes[SOURCES_REL] = _sha256(sources_path)

    links: dict[str, RoadLink] = {}
    for row in near_dicts:
        photo_id = row["photo_id"]
        links[photo_id] = RoadLink(
            photo_id=photo_id,
            osm_way_id=_to_int(row["osm_way_id"], "nearest_osm", "osm_way_id", photo_id),
            distance_m=_text(row["distance_m"]),
            match_flags=_split_flags(_text(row["match_flags"])),
            candidate_count=_to_int(row["candidate_count"], "nearest_osm", "candidate_count", photo_id),
        )

    for row in sign_dicts:
        if row["photo_id"] not in links:
            raise MapQaError(f"nearest_osm: no row for photo_id '{row['photo_id']}'")
    links = {row["photo_id"]: links[row["photo_id"]] for row in sign_dicts}

    crops_dir = data_dir / CROPS_REL_DIR
    photos_dir = data_dir / PHOTOS_REL_DIR
    signs: list[Sign] = []
    for row in sign_dicts:
        photo_id = row["photo_id"]
        crop_rel = f"{CROPS_REL_DIR}/{photo_id}.jpg"
        crop_path = crops_dir / f"{photo_id}.jpg"
        if not crop_path.is_file():
            raise MapQaError(f"crops: file not found: {crop_rel}")
        crop_sha = _sha256(crop_path)
        hashes[crop_rel] = crop_sha
        signs.append(
            Sign(
                photo_id=photo_id,
                source=_text(row["source"]),
                area=_text(row["area"]),
                sequence=_text(row["sequence"]),
                crop_path=str(crop_path.resolve()),
                photo_path=str((photos_dir / f"{photo_id}.jpg").resolve()),
                crop_sha256=crop_sha,
                creator=_text(row["creator"]),
                license=_text(row["license"]),
                source_url=_text(row["source_url"]),
                mapillary_value=_text(row["mapillary_value"]),
            )
        )

    way_ids = sorted({link.osm_way_id for link in links.values() if link.osm_way_id is not None})
    result = roads_source.read_roads(way_ids)

    roads: dict[int, OsmSpeedTags] = {}
    for row in result.rows:
        tags = _parse_tags(row["tags"], row["osm_id"])
        implicit_tag, implicit_value = implicit_limit(tags)
        way_id = int(row["osm_id"])
        roads[way_id] = OsmSpeedTags(
            osm_way_id=way_id,
            maxspeed=_text(row["maxspeed"]),
            maxspeed_forward=_tag(tags, "maxspeed:forward"),
            maxspeed_backward=_tag(tags, "maxspeed:backward"),
            maxspeed_conditional=_tag(tags, "maxspeed:conditional"),
            implicit_tag=implicit_tag,
            implicit_value=implicit_value,
        )

    warnings: list[str] = []
    if result.total_rows != snapshot_rows:
        warnings.append(
            f"osm_roads: row count {result.total_rows} differs from snapshot {snapshot_rows} in sources.json"
        )

    if isinstance(roads_source, LocalRoadsSource):
        hashes[_relative_key(roads_source.path, data_dir)] = _sha256(roads_source.path)

    crop_count = len(signs)
    sources = [
        SourceMeta(
            dataset="signs",
            source_kind="local",
            location=SIGNS_REL,
            chosen_by=chosen.get("signs", "default"),
            rows=len(sign_dicts),
            read_at=now,
        ),
        SourceMeta(
            dataset="nearest_osm",
            source_kind="local",
            location=NEAREST_REL,
            chosen_by=chosen.get("nearest_osm", "default"),
            rows=len(near_dicts),
            read_at=now,
        ),
        SourceMeta(
            dataset="crops",
            source_kind="local",
            location=f"{CROPS_REL_DIR}/",
            chosen_by=chosen.get("crops", "default"),
            rows=crop_count,
            read_at=now,
        ),
        replace(result.meta, snapshot_rows=snapshot_rows),
    ]

    return InputBundle(
        signs=signs,
        links=links,
        roads=roads,
        sources=sources,
        warnings=warnings,
        input_hashes=hashes,
        osm_snapshot=extract_date,
        sql_log=list(result.sql_log),
    )


# --- helpers -----------------------------------------------------------------------------------


def _require_file(data_dir: Path, rel: str, dataset: str) -> Path:
    path = data_dir / rel
    if not path.is_file():
        raise MapQaError(f"{dataset}: file not found: {rel}")
    return path


def _read_csv(path: Path) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Every column as text (ADR-000 §8); conversion happens in Python."""
    con = duckdb.connect()
    try:
        cursor = con.execute(
            "SELECT * FROM read_csv(?, header=true, all_varchar=true, sample_size=-1)",
            [str(path)],
        )
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
    finally:
        con.close()
    return columns, rows


def _check_columns(found: list[str], required: tuple[str, ...], dataset: str, file_name: str) -> None:
    for column in required:
        if column not in found:
            raise MapQaError(f"{dataset}: missing column '{column}' in {file_name}")


def _row_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(columns, row, strict=True))


def _check_unique(rows: list[dict[str, Any]], dataset: str) -> None:
    seen: set[str] = set()
    for row in rows:
        photo_id = row["photo_id"]
        if photo_id in seen:
            raise MapQaError(f"{dataset}: duplicate photo_id '{photo_id}'")
        seen.add(photo_id)


def _read_sources(path: Path) -> tuple[int, str | None]:
    """Snapshot row count (AC-43) and OSM extract date from sources.json."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MapQaError(f"source_meta: invalid JSON in {SOURCES_REL}") from exc
    osm = data.get("osm") if isinstance(data, dict) else None
    if not isinstance(osm, dict) or not isinstance(osm.get("rows"), int):
        raise MapQaError(f"source_meta: missing key 'osm.rows' in {SOURCES_REL}")
    return osm["rows"], osm.get("extract_date")


def _parse_tags(raw: str | None, osm_id: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    try:
        tags = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MapQaError(f"osm_roads: invalid tags JSON for osm_id {osm_id}") from exc
    if not isinstance(tags, dict):
        raise MapQaError(f"osm_roads: tags for osm_id {osm_id} are not a JSON object")
    return tags


def _tag(tags: dict[str, Any], key: str) -> str | None:
    value = tags.get(key)
    if value is None:
        return None
    return _text(str(value))


def _text(value: Any) -> str | None:
    """Empty or whitespace-only text is missing (None), never an empty string."""
    if value is None:
        return None
    text = str(value)
    if text.strip() == "":
        return None
    return text


def _to_int(value: Any, dataset: str, column: str, photo_id: str) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise MapQaError(f"{dataset}: invalid integer '{text}' in column '{column}' for photo_id '{photo_id}'") from exc


def _split_flags(text: str | None) -> tuple[str, ...]:
    if text is None:
        return ()
    return tuple(text.split(";"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_key(path: Path, data_dir: Path) -> str:
    try:
        return path.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
