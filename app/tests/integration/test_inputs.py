"""Integration tests for the local input loader (ADR-002 §3, ADR-000 §8, ADR-004 §2).

Real files from ``course-materials/exercise-data/map-qa``. Error cases use copies in ``tmp_path``;
the originals are never modified. ``signs/labels.csv`` is never read (evaluation only), so the
hash walk skips it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import duckdb
import pytest

from map_qa.inputs import implicit_limit, load_inputs
from map_qa.models import MapQaError
from map_qa.sources.local import LocalRoadsSource

SIGN_WITH_LINK = "mapillary_1931009810597411"
LABELS_REL = "signs/labels.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel == LABELS_REL:
                continue
            result[rel] = _sha256(path)
    return result


@pytest.fixture
def real_bundle(data_dir: Path):
    return load_inputs(data_dir, LocalRoadsSource(data_dir / "osm_roads.parquet"))


@pytest.fixture
def copied_inputs(tmp_path: Path, data_dir: Path) -> Path:
    """Copy only the files the loader reads (never labels.csv, photos or offline)."""
    dst = tmp_path / "map-qa"
    (dst / "signs").mkdir(parents=True)
    shutil.copy(data_dir / "signs" / "signs.csv", dst / "signs" / "signs.csv")
    shutil.copy(data_dir / "signs" / "nearest_osm.csv", dst / "signs" / "nearest_osm.csv")
    shutil.copytree(data_dir / "signs" / "crops", dst / "signs" / "crops")
    shutil.copy(data_dir / "sources.json", dst / "sources.json")
    shutil.copy(data_dir / "osm_roads.parquet", dst / "osm_roads.parquet")
    return dst


def _load(root: Path):
    return load_inputs(root, LocalRoadsSource(root / "osm_roads.parquet"))


# --- implicit_limit (ADR-004 §4) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"maxspeed:type": "PL:rural"}, ("maxspeed:type", "PL:rural")),
        ({"source:maxspeed": "sign"}, (None, None)),
        ({"source:maxspeed": "PL:urban", "maxspeed:type": "sign"}, ("source:maxspeed", "PL:urban")),
        ({"maxspeed:type": "PL:rural", "zone:maxspeed": "DE:urban"}, ("maxspeed:type", "PL:rural")),
        ({"zone:maxspeed": "DE:urban"}, ("zone:maxspeed", "DE:urban")),
        ({"maxspeed:type": "50"}, (None, None)),
        ({}, (None, None)),
    ],
)
def test_implicit_limit_first_matching_tag(tags: dict[str, str], expected: tuple) -> None:
    assert implicit_limit(tags) == expected


# --- real snapshot -----------------------------------------------------------------------------


def test_real_load_counts_and_sign_link(real_bundle) -> None:
    assert len(real_bundle.signs) == 119
    assert len(real_bundle.links) == 119

    sign = next(s for s in real_bundle.signs if s.photo_id == SIGN_WITH_LINK)
    assert sign.area == "Kraków"

    link = real_bundle.links[SIGN_WITH_LINK]
    assert link.osm_way_id == 187014751
    assert link.distance_m == "7.844"


def test_real_load_roads_match_database_count(real_bundle, data_dir: Path) -> None:
    link_ways = {link.osm_way_id for link in real_bundle.links.values() if link.osm_way_id is not None}
    con = duckdb.connect()
    found = con.execute(
        "SELECT count(*) FROM read_parquet(?) WHERE osm_id IN (SELECT unnest(?))",
        [str(data_dir / "osm_roads.parquet"), sorted(link_ways)],
    ).fetchone()[0]
    # 119 links, 95 of them point to existing ways, 88 distinct way ids (verified with DuckDB)
    assert found == 88
    assert len(real_bundle.roads) == found


def test_real_load_no_warning_for_real_snapshot(real_bundle) -> None:
    assert not [w for w in real_bundle.warnings if "osm_roads" in w]
    assert real_bundle.osm_snapshot == "2026-11-01T21:21:36Z"


def test_real_load_sources_in_order(real_bundle) -> None:
    assert [s.dataset for s in real_bundle.sources] == ["signs", "nearest_osm", "crops", "osm_roads"]
    by_name = {s.dataset: s for s in real_bundle.sources}
    assert by_name["signs"].source_kind == "local"
    assert by_name["signs"].location == "signs/signs.csv"
    assert by_name["nearest_osm"].location == "signs/nearest_osm.csv"
    assert by_name["crops"].location == "signs/crops/"
    assert by_name["signs"].chosen_by == "default"
    assert by_name["osm_roads"].snapshot_rows == 99768
    assert by_name["osm_roads"].rows == 88


def test_real_load_signs_fields(real_bundle, data_dir: Path) -> None:
    sign = next(s for s in real_bundle.signs if s.photo_id == SIGN_WITH_LINK)
    crop = data_dir / "signs" / "crops" / f"{SIGN_WITH_LINK}.jpg"
    assert Path(sign.crop_path).is_absolute()
    assert Path(sign.crop_path) == crop
    assert sign.crop_sha256 == _sha256(crop)
    assert sign.mapillary_value == "regulatory--maximum-speed-limit-20--g1"


# --- OSM speed tags ----------------------------------------------------------------------------


def test_known_directional_tags(real_bundle) -> None:
    tags = real_bundle.roads[557114409]
    assert tags.maxspeed_forward == "50"
    assert tags.maxspeed_backward == "70"


def test_known_implicit_tag(real_bundle) -> None:
    tags = real_bundle.roads[1266239200]
    assert tags.implicit_tag == "maxspeed:type"
    assert tags.implicit_value == "PL:rural"


def test_source_maxspeed_sign_is_not_implicit(real_bundle) -> None:
    tags = real_bundle.roads[612268589]
    assert tags.implicit_tag is None
    assert tags.implicit_value is None
    assert tags.maxspeed == "30"


# --- input hashes (AC-01) ----------------------------------------------------------------------


def test_inputs_are_not_modified_by_load(data_dir: Path) -> None:
    before = _hash_tree(data_dir)
    load_inputs(data_dir, LocalRoadsSource(data_dir / "osm_roads.parquet"))
    assert _hash_tree(data_dir) == before


def test_input_hashes_cover_local_files(real_bundle) -> None:
    hashes = real_bundle.input_hashes
    assert "signs/signs.csv" in hashes
    assert "signs/nearest_osm.csv" in hashes
    assert "sources.json" in hashes
    assert "osm_roads.parquet" in hashes
    assert "signs/crops/mapillary_1931009810597411.jpg" in hashes
    assert all("\\" not in key for key in hashes)
    assert "signs/labels.csv" not in hashes


# --- validation errors (copies in tmp_path) ----------------------------------------------------


def test_missing_signs_csv_names_dataset_and_file(copied_inputs: Path) -> None:
    (copied_inputs / "signs" / "signs.csv").unlink()
    with pytest.raises(MapQaError, match=r"signs: file not found: signs/signs.csv"):
        _load(copied_inputs)


def test_nearest_osm_without_match_flags_column(copied_inputs: Path) -> None:
    path = copied_inputs / "signs" / "nearest_osm.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    idx = header.index("match_flags")
    rewritten = []
    for line in lines:
        cells = _split_csv_line(line)
        del cells[idx]
        rewritten.append(",".join(cells))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    with pytest.raises(MapQaError, match=r"nearest_osm: missing column 'match_flags' in nearest_osm.csv"):
        _load(copied_inputs)


def _split_csv_line(line: str) -> list[str]:
    # The nearest_osm.csv rows have no quoted commas, so a plain split is exact for this test copy.
    return line.split(",")


def test_duplicate_photo_id_in_signs(copied_inputs: Path) -> None:
    path = copied_inputs / "signs" / "signs.csv"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines + [lines[1]]), encoding="utf-8")
    with pytest.raises(MapQaError, match=rf"signs: duplicate photo_id '{SIGN_WITH_LINK}'"):
        _load(copied_inputs)


def test_sign_without_nearest_osm_row(copied_inputs: Path) -> None:
    path = copied_inputs / "signs" / "nearest_osm.csv"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [lines[0]] + [line for line in lines[1:] if not line.startswith(SIGN_WITH_LINK + ",")]
    path.write_text("".join(kept), encoding="utf-8")
    with pytest.raises(MapQaError, match=rf"nearest_osm: no row for photo_id '{SIGN_WITH_LINK}'"):
        _load(copied_inputs)


def test_missing_crop_file(copied_inputs: Path) -> None:
    (copied_inputs / "signs" / "crops" / f"{SIGN_WITH_LINK}.jpg").unlink()
    with pytest.raises(MapQaError, match=rf"crops: file not found: signs/crops/{SIGN_WITH_LINK}.jpg"):
        _load(copied_inputs)


def test_missing_roads_file_names_dataset(copied_inputs: Path) -> None:
    (copied_inputs / "osm_roads.parquet").unlink()
    with pytest.raises(MapQaError, match=r"osm_roads: file not found"):
        _load(copied_inputs)


# --- AC-43 row count warning -------------------------------------------------------------------


def test_row_count_difference_is_a_warning_not_an_error(copied_inputs: Path) -> None:
    path = copied_inputs / "sources.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["osm"]["rows"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")

    bundle = _load(copied_inputs)

    assert len(bundle.signs) == 119
    matching = [w for w in bundle.warnings if w.startswith("osm_roads: row count")]
    assert len(matching) == 1
    assert "99768" in matching[0]
    assert "1" in matching[0].split("snapshot", 1)[1]
