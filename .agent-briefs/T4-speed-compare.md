# T4 — OSM speed normalization and comparison

Read `.agent-briefs/_common.md` first and follow it. Also read `docs/ADR/003-spatial-and-speed-normalization.md` §3 and §6, and PRD `docs/PRD.md` AC-19…AC-24 and §11 "Outcomes".

## Editable files (only these)

- `app/src/map_qa/osm_speed.py` (new)
- `app/src/map_qa/compare.py` (new)
- `app/tests/unit/test_osm_speed.py` (new)
- `app/tests/unit/test_compare.py` (new)

Concurrent tasks (do not touch their files): T5 `sources/`, `inputs.py`; T6 `review.py`; T7 `export.py`; T8 `config.py`.

## `osm_speed.py` API

```python
def parse_speed(source_tag: str, raw: str | None) -> list[SpeedRule]
def normalize(tags: OsmSpeedTags) -> list[SpeedRule]
```

- `parse_speed` returns `[]` for `None`, `""` or whitespace-only (empty tag = no rule, never 0).
- `raw` of every rule is the tag value **byte for byte** (TAC-003-01), including for `multi` parts? No: for `multi`, each part rule has `raw` = the full original value and `multi_value=True`; the part's own value is in `value_kmh` / `kind`.
- kinds (constants in models.py):
  - `numeric`: `^\s*\d+\s*$` or `^\s*\d+\s*km/h\s*$` → `value_kmh` int.
  - `numeric_other_unit`: number + `mph` / `knots` (any other unit text after a number except km/h) → `value_kmh=None`, `unit_raw` = unit text.
  - `implicit`: country-code value `^[A-Za-z]{2}:` (e.g. `PL:urban`, `PL:rural`, `PL:motorway`) → no value.
  - `special`: `none`, `signals`, `walk`, `variable` (case-insensitive) → no value.
  - `multi`: contains `;` → one rule per part, each parsed recursively, each with `multi_value=True` and `raw` = full value.
  - `conditional`: contains `@` → `value_kmh` = the number before `@` if numeric, `condition_raw` = text after `@` stripped (e.g. `(22:00-06:00)`).
  - anything else → `unparsed`, `raw` kept.
- `normalize(tags)` returns rules for, in order: `maxspeed`, `maxspeed:forward`, `maxspeed:backward`, `maxspeed:conditional`, then the implicit tag (`tags.implicit_tag` with `tags.implicit_value`, kind `implicit`).

## `compare.py` API

```python
def abstain_reason(sign: Sign, link: RoadLink, tags: OsmSpeedTags | None) -> str | None
def compare(sign: Sign, link: RoadLink, tags: OsmSpeedTags | None, reading: SignReading) -> Comparison
```

`abstain_reason`: `;`-joined codes in this order (ADR-004 §3), `None` if none apply:
1. `outside_krakow` when `sign.area != models.KRAKOW`;
2. `road_not_in_osm` when `tags is None` (or `link.osm_way_id is None`);
3. `flag:<f>` for each flag in `link.match_flags`, in input order.

`compare` rules, in this order:
1. abstain reason present → `Comparison(outcome=ABSTAIN, abstain_reason=...)`. **Abstain takes precedence even when a value exists** (AC-24).
2. `reading.value is None` → `PENDING_HUMAN`.
3. value `none` → `NOT_COMPARABLE`, `NC_SIGN_NONE`; value `unreadable` → `NOT_COMPARABLE`, `NC_SIGN_UNREADABLE`.
4. any of `maxspeed_forward`, `maxspeed_backward`, `maxspeed_conditional` present → `NOT_COMPARABLE`, `NC_DIRECTIONAL_OR_CONDITIONAL` (the condition is never evaluated).
5. `maxspeed` present: if `parse_speed("maxspeed", ...)` gives exactly one rule of kind `numeric` → `MATCH` if `value_kmh == int(value)` else `MISMATCH`; otherwise `NOT_COMPARABLE`, `NC_MAXSPEED_NOT_NUMERIC`.
6. `maxspeed` absent: `implicit_tag` present → `NOT_COMPARABLE`, `NC_IMPLICIT_LIMIT`; else `MAP_MISSING`.

Note: `OsmSpeedTags.implicit_tag` is already filtered by the input loader (only `PL:`-style values), so `source:maxspeed=sign` arrives as `implicit_tag=None`. Test that case as `map_missing` by building `OsmSpeedTags(osm_way_id=1)` with no implicit tag.

## Tests (synthetic inputs, ADR-003 §6 — the real `maxspeed` has only integers)

- normalization table: `50`, `50 km/h`, `30 mph`, `20 knots`, `PL:urban`, `none`, `walk`, `50;70`, `60 @ (22:00-06:00)`, `110 @ (Mo-Fr 06:00-18:00)`, `""`, `None`, `"  "`, `abc`; assert kind, value_kmh, unit_raw, condition_raw and raw byte-for-byte.
- comparison: reading 50 + `maxspeed=50` → match; + `40` → mismatch; + no tags → map_missing; + `forward=50` → not_comparable directional; + only implicit `maxspeed:type=PL:rural` → not_comparable implicit_limit; + only `source:maxspeed=sign` (implicit_tag None) → map_missing; reading `none` → not_comparable sign_none; reading `unreadable` → sign_unreadable; `30 mph` + reading 50 → not_comparable maxspeed_not_numeric (not mismatch); `50;70` → maxspeed_not_numeric; reading None → pending_human; area `Warszawa` with value → abstain `outside_krakow;road_not_in_osm` when tags None; Kraków + flags `("ambiguous_candidates","service_way")` → `flag:ambiguous_candidates;flag:service_way` even with value 50.
- Build `Sign`, `RoadLink`, `SignReading` with small helper factories in the test file.

Verification: `uv run --frozen pytest tests/unit/test_osm_speed.py tests/unit/test_compare.py -q`.
