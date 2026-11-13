# T5 — Local source and input validation

Read `.agent-briefs/_common.md` first and follow it. Also read `docs/ADR/002-data-sources-and-reports.md` §3, `docs/ADR/000-main-architecture.md` §8 ("Explicit types when reading CSV"), ADR-004 §2.

## Editable files (only these)

- `app/src/map_qa/sources/__init__.py` (new)
- `app/src/map_qa/sources/local.py` (new)
- `app/src/map_qa/inputs.py` (new)
- `app/tests/integration/test_inputs.py` (new)

Concurrent tasks (do not touch their files): T4 `osm_speed.py`, `compare.py`; T6 `review.py`; T7 `export.py`; T8 `config.py`.

## `sources/__init__.py`

```python
@dataclass
class RoadsResult:
    rows: list[dict]          # each: {"osm_id": int, "maxspeed": str | None, "tags": str | None (JSON text)}
    total_rows: int           # row count of the WHOLE table (AC-43)
    meta: SourceMeta          # from models.py; rows = len(rows) read, snapshot_rows filled by inputs
    sql_log: list[dict]       # empty for local

class RoadsSource(Protocol):
    def read_roads(self, way_ids: list[int]) -> RoadsResult: ...
```

## `sources/local.py`

```python
class LocalRoadsSource:
    def __init__(self, path: Path, chosen_by: str = "default"): ...
    def read_roads(self, way_ids: list[int]) -> RoadsResult
```

- DuckDB 1.5.6 (`duckdb.connect()` in memory). Check the file exists (else `MapQaError("osm_roads: file not found: <path>")`) and that columns `osm_id`, `maxspeed`, `tags` exist (else `MapQaError("osm_roads: missing column 'tags' in <file name>")`).
- `total_rows`: `SELECT count(*)` of the file. `rows`: only the requested ids, projected columns, no geometry; pass ids as a parameter (`WHERE osm_id IN (SELECT unnest($ids))` or a list parameter), never pasted in SQL text.
- `meta`: `SourceMeta(dataset="osm_roads", source_kind="local", location=<path as given>, chosen_by=chosen_by, rows=len(rows), read_at=<UTC ISO 8601>)`.

## `inputs.py`

```python
SIGNS_COLUMNS = ("photo_id", "source", "area", "sequence", "creator", "license", "source_url", "mapillary_value")
NEAREST_COLUMNS = ("photo_id", "osm_way_id", "distance_m", "match_flags", "candidate_count")

def implicit_limit(tags: dict[str, str]) -> tuple[str | None, str | None]
def load_inputs(data_dir: Path, roads_source: RoadsSource, chosen_by: dict[str, str] | None = None) -> InputBundle
```

- `implicit_limit`: first key of `models.IMPLICIT_TAGS` (in that order) whose value matches `^[A-Za-z]{2}:` → `(key, value)`; else `(None, None)`. So `source:maxspeed=sign` → `(None, None)`, `maxspeed:type=PL:rural` → `("maxspeed:type", "PL:rural")`.
- Files (relative to `data_dir`): `signs/signs.csv`, `signs/nearest_osm.csv`, `sources.json`, crops `signs/crops/<photo_id>.jpg`, photos `signs/photos/<photo_id>.jpg`.
- Read CSVs with DuckDB `read_csv(..., all_varchar=true, header=true)` (explicit types: everything text, then convert in Python): empty → `None` (never 0); `osm_way_id` and `candidate_count` → `int` or `None`; `distance_m` stays text; `match_flags` → tuple split on `;` (empty → `()`). Keep Unicode (area `Kraków`) intact.
- Validation, each raising `MapQaError` before anything else happens:
  - missing file → `"<dataset>: file not found: <relative path>"` (datasets: `signs`, `nearest_osm`, `source_meta`, `crops`);
  - missing column → `"<dataset>: missing column '<col>' in <file name>"`;
  - duplicate `photo_id` in either CSV → `"<dataset>: duplicate photo_id '<id>'"`;
  - a sign without a `nearest_osm` row → `"nearest_osm: no row for photo_id '<id>'"`;
  - missing crop file → `"crops: file not found: signs/crops/<id>.jpg"`.
  Validate all columns of both CSVs before reading roads.
- `Sign`: `crop_path`/`photo_path` absolute strings; `crop_sha256` = SHA-256 of the crop bytes; `mapillary_value` from the CSV (may be None).
- Roads: call `roads_source.read_roads(sorted unique non-null way ids)`; build `OsmSpeedTags` per row: `maxspeed` from the column; `maxspeed_forward` / `_backward` / `_conditional` from the parsed `tags` JSON keys `maxspeed:forward`, `maxspeed:backward`, `maxspeed:conditional`; implicit from `implicit_limit(tags)`. Empty strings → None. Ways not returned are simply absent from `roads` (they become `abstain` later).
- `sources.json`: `osm.rows` is the snapshot count for `osm_roads`; `osm.extract_date` → `InputBundle.osm_snapshot`. If `total_rows != osm.rows` append the warning `"osm_roads: row count <total> differs from snapshot <snapshot> in sources.json"` (AC-43); the run continues.
- `sources` list (in this order): `signs`, `nearest_osm`, `crops`, `osm_roads`, each a `SourceMeta`; local ones get `source_kind="local"`, `location` = relative path (`signs/signs.csv`, …, `signs/crops/`), `chosen_by` from the `chosen_by` dict (default `"default"`), `rows`, `read_at`. `osm_roads` meta comes from the `RoadsResult` with `snapshot_rows` set.
- `input_hashes`: SHA-256 of every local file read (CSV files, `sources.json`, every crop, and the parquet when the roads source is `LocalRoadsSource` — expose the path via an attribute `path`), keyed by path relative to `data_dir` with `/` separators.
- `sql_log` from the `RoadsResult`.

## Tests (`tests/integration/test_inputs.py`, real files; use the `data_dir` fixture from `tests/conftest.py`)

- Real load: 119 signs, 119 links, area of a known sign is `Kraków` (`mapillary_1931009810597411`), its link `osm_way_id == 187014751` and `distance_m == "7.844"`; 95 roads found of the referenced ids (24 of the 119 links point to ways absent from `osm_roads`; count distinct way ids yourself with DuckDB in the test and assert `len(roads) == found`); no warning for the real snapshot; `osm_snapshot == "2026-11-01T21:21:36Z"`.
- Known tags: way `557114409` has `maxspeed_forward == "50"`, `maxspeed_backward == "70"`; way `1266239200` has `implicit_tag == "maxspeed:type"`, `implicit_value == "PL:rural"`; way `612268589` (`source:maxspeed=sign`) has `implicit_tag is None`.
- Input SHA-256 hashes of all files under `data_dir` before == after the load (AC-01).
- Errors with copies in `tmp_path` (copy the needed files; never modify the originals): missing `signs.csv`; `nearest_osm.csv` without `match_flags` column; duplicate photo_id; missing crop. Each → `MapQaError` whose message names dataset and item.
- AC-43: a `sources.json` copy with `osm.rows` changed to 1 → one warning containing both numbers.
- `implicit_limit` unit cases.

Note on the 95: verify it yourself; if the real count differs, assert the computed count and report the number.

Verification: `uv run --frozen pytest tests/integration/test_inputs.py -q`.
