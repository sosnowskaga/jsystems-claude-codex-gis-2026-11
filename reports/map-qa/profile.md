# Map QA input profile

Date: 2026-11-12. Scope: read-only profile of `course-materials/exercise-data/map-qa/` (Kraków bbox `19.80,49.95,20.15,50.15`). No input was modified, no paid API was called.

## 1. Commands and versions

| Item | Value |
|---|---|
| OS | Windows 11 Pro 10.0.26200 |
| uv | 0.13.7 |
| Python (uv-managed) | 3.12.16 |
| DuckDB (+ `spatial`) | 1.5.6 |
| pyarrow | 23.0.1 |
| Databricks CLI | v1.26.0 (installed; the count read used the REST client below) |
| Databricks access | SQL Statement Execution API via `query_databricks.py`, environment variables only |

Commands, run from the repository root (`PYTHONUTF8=1` avoids a cp1250 encoding error when DuckDB prints box-drawing tables in the Windows console):

```powershell
$env:PYTHONUTF8 = "1"
uv run --python 3.12 course-materials/exercise-data/map-qa/profile_data.py
uv run --python 3.12 <scratchpad>/profile_extra.py course-materials/exercise-data/map-qa
```

`profile_data.py` runs the shipped `profile.sql` (exit 0). A second run gave identical numbers; only the row order of the unordered `GROUP BY source` query changed. `profile_extra.py` is an ad-hoc, read-only DuckDB/pyarrow script kept outside the repository (CRS metadata, schemas and null rates, duplicates, key relations, `maxspeed` formats, Overture speed rules).

**Databricks:** one read-only `SELECT count(*)` over the five `workspace.map_qa` tables, run on 2026-11-12 with `DATABRICKS_HOST`, `DATABRICKS_TOKEN` and `DATABRICKS_SQL_WAREHOUSE_ID` set in the environment. Only their presence was checked, never their values. See §9.

## 2. Row counts and types

| Source | Rows | Unique key | Geometry |
|---|---:|---|---|
| `osm_roads.parquet` | 99 768 | `osm_id` 99 768 | LineString, `GEOMETRY('EPSG:4326')` |
| `overture_segments.parquet` | 85 405 | `id` (GERS) 85 405 | LineString, `GEOMETRY('OGC:CRS84')` |
| `photos.csv` | 561 (Panoramax 461, Mapillary 100) | `photo_id` 561 | `longitude`/`latitude` DOUBLE |
| `photo_road_links.csv` | 561 | `photo_id` 561 | — |
| `reference_labels.csv` | 320 (100 photos) | (`photo_id`, `question`) 320 | — |
| `traffic_signs.geojson` | 1 730 | `id` 1 730 | Point |

Key column types:

- **OSM:** `osm_id` BIGINT, `node_ids` BIGINT[], `tags` VARCHAR (JSON text, not a struct), every promoted tag column (`maxspeed`, `lanes`, `lit`, `surface`, …) is VARCHAR, `boundary_crossing` BOOLEAN, `osm_timestamp` VARCHAR.
- **Overture:** nested typed lists: `speed_limits` STRUCT[] with `max_speed {value INTEGER, unit VARCHAR}`, `min_speed`, `is_max_speed_variable`, `when`, `between DOUBLE[]`; also `road_surface`, `sources`, `connectors`, `access_restrictions`, `level_rules`, `bbox`.
- **photos:** `captured_at` TIMESTAMP WITH TIME ZONE, `heading` DOUBLE, `is_panorama` BOOLEAN, `horizontal_accuracy_m` DOUBLE.
- **links:** `osm_way_id` BIGINT, `overture_id` VARCHAR, `distance_m`, `bearing_diff`, `directed_bearing_diff` DOUBLE, `candidate_flags` VARCHAR (`;`-separated).

OSM `highway` distribution (rows / with `maxspeed`): service 72 113 / 1 997, residential 11 799 / 5 467, track 4 296 / 2, tertiary 3 838 / 2 839, secondary 2 073 / 1 897, living_street 1 938 / 1 140, primary 1 586 / 1 539, unclassified 880 / 316, motorway 459 / 453, other classes ≤ 229 rows each. Overture has 11 classes, including 844 `unknown`.

## 3. CRS

| Source | Declared CRS | Evidence |
|---|---|---|
| OSM | EPSG:4326 | GeoParquet 1.1.0 `crs.id = EPSG:4326`; DuckDB type `GEOMETRY('EPSG:4326')` |
| Overture | OGC:CRS84 | GeoParquet 1.0.0 with no `crs` member, which means the CRS84 default; DuckDB type `GEOMETRY('OGC:CRS84')` |
| photos | WGS84 lon/lat implied | CSV columns, no CRS metadata |
| Databricks | none in data | Geometry is BINARY WKB; SRID must be given explicitly (`ST_GeomFromWKB(geometry, 4326)`), per `DATABRICKS.md` |

Both files store coordinates in lon/lat order: OSM x 19.764–20.173, y 49.944–50.162; Overture x 19.726–20.169, y 49.940–50.160. Whole ways are kept, so both layers extend past the bbox (560 OSM ways have `boundary_crossing`). All 561 photos are inside the bbox. Distances must be computed after transforming to EPSG:2180.

## 4. Geometry validity

| Source | Invalid (`ST_IsValid`) | Empty | Types |
|---|---:|---:|---|
| OSM | 0 | 0 | LineString only |
| Overture | 0 | 0 | LineString only |

## 5. Duplicates

None found: `osm_id`, Overture `id`, `photo_id`, photo `sha256` and `filename` are all unique. No two OSM or two Overture rows have byte-identical WKB geometry. `reference_labels.csv` has one row per (`photo_id`, `question`). In `traffic_signs.geojson`, `id` is unique.

## 6. `photo_id` key relations

| Check | Result |
|---|---:|
| `photo_road_links` rows without a photo | 0 |
| Photos without a link row | 0 (1:1, 561 = 561) |
| `reference_labels` photo ids without a photo | 0 |
| Photos with reference labels | 100 (80 Panoramax, 20 Mapillary) |
| Link `osm_way_id` not found in `osm_roads` | 0 of 558 |
| Link `overture_id` not found in `overture_segments` | 0 of 558 |
| Photos with no candidate (`match_method = none`, `no_osm_within_25m`) | 3 |

`match_method`: `osm_source_reference` 557, `spatial_fallback` 1, `none` 3. Candidate distance is median 2.43 m, p95 9.82 m, max 22.17 m.

`candidate_flags` (one photo may carry several): `ambiguous_candidates` 65, `camera_against_way_direction` 35, `panorama_heading_ambiguous` 31, `poor_heading_alignment` 27, `boundary_crossing` 27, `missing_heading` 15, `service_way` 14, `no_osm_within_25m` 3, `overture_spatial_only` 1. 146 links (26 %) have at least one flag.

## 7. `maxspeed` distribution and formats

### OSM `maxspeed` column

| Format | Ways | Notes |
|---|---:|---|
| Missing | 83 478 (83.7 %) | |
| Plain integer (km/h) | 16 290 | 16 distinct values |
| With unit (`km/h`, `mph`) | **0** | |
| Zone code (`PL:urban`, …) | **0** | |
| Keyword (`none`, `signals`, `walk`, `variable`) | **0** | |
| Multiple values (`50;70`) | **0** | |

Values: 50 → 5 737, 30 → 4 802, 20 → 2 053, 70 → 1 294, 40 → 1 245, 140 → 214, 90 → 184, 60 → 181, 100 → 167, 10 → 157, 120 → 134, 80 → 61, 5 → 48, 25 → 7, 15 → 4, 4 → 2. The column always equals `tags.maxspeed` (0 differences).

### Other OSM `maxspeed*` keys (from `tags` JSON)

| Key | Ways | Values / format |
|---|---:|---|
| `source:maxspeed` | 11 671 | `PL:urban`, `PL:rural`, `PL:zone30`, `PL:sign_B-33`, `PL:motorway`, … |
| `maxspeed:type` | 2 578 | `PL:urban`, `PL:rural`, `PL:zone20/30/40`, `PL:living_street`, `sign`; **1 392 of these ways have no `maxspeed`** |
| `maxspeed:bus` / `:trailer` / `:hgv` | 1 031 / 1 028 / 1 007 | Integers (vehicle-class limits) |
| `maxspeed:forward` | 261 | 260 integers, 1 `PL:urban` |
| `maxspeed:backward` | 247 | 243 integers, 4 `PL:urban` |
| `maxspeed:conditional` | 92 | A single value: `110 @ (Mo-Fr 06:00-18:00)` |
| `maxspeed:variable` | 82 | `yes` |
| `zone:maxspeed` | 19 | `PL:30` |
| `maxspeed:lanes` | 10 | Pipe lists, e.g. `120\|120\|120\|80` |
| `maxspeed:source` | 23 | Includes lowercase `pl:zone30` |

On the 558 OSM roads that are photo candidates, 531 have an integer `maxspeed`, 27 have no speed information at all, 4 have directional tags and **79 carry the `110 @ (Mo-Fr 06:00-18:00)` conditional**.

### Overture `speed_limits`

12 847 segments (15.0 %) hold 14 205 rules (max 13 per segment). Every rule has a `max_speed` in `km/h`; none has `min_speed`. Of these rules, 1 925 have a `between` range (partial segment), 880 have a `when` condition and 16 are `is_max_speed_variable`. Top values: 50 → 4 651, 30 → 4 473, 20 → 2 150, 40 → 1 403, 70 → 703.

### Speed reference labels (20 Mapillary photos)

`speed_limit_value` certain: `70` × 5, `30` × 1, `none` × 8; uncertain `unknown` × 6. Against the candidate road's OSM `maxspeed`, the certain numeric labels give 30/30 × 1, 70/70 × 2, 70 vs 50 × 2 and 70 vs missing × 1. These are candidates for review, not verdicts: direction and sign position are unchecked.

`traffic_signs.geojson`: 1 730 Mapillary speed-limit objects, 12 values (5–110).

## 8. Missing values

| Field | Missing |
|---|---:|
| OSM `maxspeed` | 83.7 % |
| OSM `surface` | 60.3 % |
| OSM `lanes` | 88.7 % |
| OSM `lit` | 84.4 % |
| OSM `sidewalk` | 93.5 % |
| OSM `access` | 83.8 % |
| OSM `name` | 80.8 % |
| Overture `speed_limits` | 85.0 % (empty list) |
| Overture `road_surface` | 62.4 % |
| Overture `subclass` | 44.7 % |
| `photos.heading` | 15 (2.7 %, all Panoramax); 23 more photos have a valid `heading = 0.0` |
| `photos.horizontal_accuracy_m` | 56.2 % |
| Links `osm_way_id` / `overture_id` / `distance_m` | 3 (0.5 %) |
| Links `bearing_diff` | 18 (3.2 %) |
| `candidate_flags` | 74.0 % empty, meaning no flag |

Photo dates span 2014-07-14 to 2026-10-18, older than and around the OSM snapshot of 2026-11-01. 31 Panoramax photos are panoramas.

## 9. Local vs Databricks counts

| Table | Local | Databricks (live, 2026-11-12) | Match |
|---|---:|---:|---|
| `osm_roads` | 99 768 | 99 768 | yes |
| `overture_segments` | 85 405 | 85 405 | yes |
| `photos` | 561 | 561 | yes |
| `photo_road_links` | 561 | 561 | yes |
| `traffic_signs` | 1 730 | 1 730 | yes |

All five counts match. The query returned state `SUCCEEDED` with 1 chunk, `truncated: false`, and schema `source STRING`, `rows BIGINT`. With `format = JSON_ARRAY`, the BIGINT counts come back as JSON **strings** (`"99768"`). A REST client must cast values by `manifest.schema`, and mocks must keep them as strings. Only counts were compared; attribute and geometry parity between Databricks and the files was not checked.

Command (run from the repository root, with the variables set in the terminal):

```powershell
uv run --python 3.12 course-materials/exercise-data/map-qa/query_databricks.py --statement "SELECT 'osm_roads' AS source, count(*) AS rows FROM workspace.map_qa.osm_roads UNION ALL SELECT 'overture_segments', count(*) FROM workspace.map_qa.overture_segments UNION ALL SELECT 'photos', count(*) FROM workspace.map_qa.photos UNION ALL SELECT 'photo_road_links', count(*) FROM workspace.map_qa.photo_road_links UNION ALL SELECT 'traffic_signs', count(*) FROM workspace.map_qa.traffic_signs"
```

## 10. Five facts that change the project

1. **The OSM `maxspeed` column is always an integer in km/h.** None of the PRD AC-23 formats (`PL:urban`, `none`, `50;70`, `30 mph`) occur in it. They must be covered by synthetic unit-test inputs, not real fixtures. The real non-numeric cases are elsewhere: 5 `PL:urban` values in `maxspeed:forward/backward`, plus `maxspeed:type`, `zone:maxspeed` and `maxspeed:lanes`.
2. **1 392 ways carry only an implicit limit** (`maxspeed:type`, e.g. `PL:urban`, with no `maxspeed`). Whether these count as `map_missing`, `not_comparable` or a derived national default is a design decision that is still open. None of the 558 photo candidates fall in this group, but the export over all roads would.
3. **The conditional limit matters for photos.** 79 of 558 candidate roads carry `maxspeed:conditional = 110 @ (Mo-Fr 06:00-18:00)`. Comparing them correctly needs the photo's `captured_at` in local time, or an explicit `not_comparable` rule. A first-number parser would silently pick the wrong value.
4. **Speed-value accuracy cannot be measured on `reference_labels.csv`.** It has only 6 certain numeric labels (5 × 70, 1 × 30) plus 8 certain `none`. The evaluation step in the plan needs the `signs/` set or human labels. Any accuracy figure from 6 items must be reported as indicative.
5. **Link quality, not parsing, drives abstention volume.** 3 photos have no candidate, 27 candidate roads have no speed data (→ `map_missing`), and 146 links (26 %) carry flags such as `ambiguous_candidates` (65), panorama (31) or `missing_heading` (15). Overture adds 1 925 partial-segment (`between`) and 880 conditional (`when`) rules, so "segment speed" is not a single number. The comparator must take the rule that applies at the photo position or abstain.

## 11. Questions for a human (answered 2026-11-12)

Decisions:

1. **Implicit limit only:** `not_comparable` with reason `implicit_limit`; no statutory limit is derived. On the sign set this moves 2 Kraków signs (labels 60 and 80, roads with `maxspeed:type=PL:rural` and `source:maxspeed=PL:expressway`) from `map_missing` to `not_comparable`. Expected counts: 13 / 18 / **5** / **9**. Recorded in PRD AC-22, AC-23, §11, §12 and ADR-003 §3.
2. **Conditional limit:** always `not_comparable` in the MVP. PRD AC-23 already said this; it now states explicitly that the condition is not evaluated against the photo time.
3. **Evaluation reference:** `signs/labels.csv` is the main set, as PRD §12 already defines. `reference_labels.csv` is a supplementary set; it is not an MVP input, because its photos are not in `signs/`. Results on small samples are reported as indicative.

Original questions:

1. For ways with only `maxspeed:type`/`source:maxspeed` (e.g. `PL:urban`, `PL:zone30`), should the app derive the Polish default (50/30/…), report `not_comparable`, or report `map_missing`?
2. Should `maxspeed:conditional = 110 @ (Mo-Fr 06:00-18:00)` be evaluated against the photo's `captured_at` (Europe/Warsaw), or always yield `not_comparable` in the MVP?
3. Which set is the official evaluation reference for speed-value reading: the 20 Mapillary rows of `reference_labels.csv`, the `signs/` set (`signs/labels.csv`), or new human labels?
