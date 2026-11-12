# ADR-004: MVP file and CLI contract

**Date:** 2026-11-12
**Status:** Accepted by pre-authorization (implementation plan §2, D2); reviewed by the human in the MVP pull request
**Relates to:** [PRD](../PRD.md) §4, §9; [ADR-000](000-main-architecture.md) §5–§7; [ADR-001](001-decision-cascade.md) §4–§5; [ADR-002](002-data-sources-and-reports.md) §3–§4; [ADR-003](003-spatial-and-speed-normalization.md) §3

---

## 1. Context

The PRD and ADR-000…003 name the files, fields and options, but leave their exact layout open. Parallel workers need one fixed contract. This ADR fixes it. It does not repeat what the other ADRs already decided (output directory, option names, per-tier cache, `abstain` precedence, exit 2 for a missing key, `run` calling `evaluate`).

Code identifiers and CSV column names are English. Report headings and labels are Polish (PRD §8).

---

## 2. Paths and defaults

| Item | Default | Override |
|---|---|---|
| Repository root | three levels above `app/src/map_qa/` | — |
| Input folder | `<root>/course-materials/exercise-data/map-qa` | `data_dir` in the configuration file, `MAPQA_DATA_DIR` |
| Output folder | `<root>/reports/map-qa/run` | `--out` |
| Review CSV | `<out>/review.csv` | `--review` |
| Cache and ledger | `<root>/app/.cache/map-qa` | `--cache-dir`, `MAPQA_CACHE_DIR` |
| Samples | `<root>/app/samples/<name>.txt` | a path in `--sample` |

Datasets and their local paths, relative to the input folder: `signs` → `signs/signs.csv`, `nearest_osm` → `signs/nearest_osm.csv`, `crops` → `signs/crops/<photo_id>.jpg`, `photos` → `signs/photos/<photo_id>.jpg`, `osm_roads` → `osm_roads.parquet`, `labels` → `signs/labels.csv`, `source_meta` → `sources.json`. Only `osm_roads` has a Databricks location (`workspace.map_qa.osm_roads`).

---

## 3. Domain codes

| Code set | Values |
|---|---|
| `outcome` | `match`, `mismatch`, `map_missing`, `not_comparable`, `abstain`, `pending_human` |
| `tier` | `decision_model`, `llm`, `human`, empty (no accepted value) |
| answer | `5` … `140` from the ADR-001 set, `none`, `unreadable` |
| `abstain_reason` (`;`-joined, this order) | `outside_krakow`, `road_not_in_osm`, `flag:<match_flag>` for each flag in `match_flags` |
| `not_comparable_reason` | `sign_none`, `sign_unreadable`, `directional_or_conditional_tag`, `maxspeed_not_numeric`, `implicit_limit` |
| escalation reasons (`;`-joined) | `tier1:mode_off`, `tier1:cache_miss`, `tier1:below_threshold`, `tier1:unreadable`, `tier1:no_probabilities`, `tier1:model_unavailable:<category>`, and the same with `tier2:`, plus `tier2:out_of_set`; `tier2:<…>` only when tier 2 was reached |
| error category | ADR-001 §5 (`bad_request`, `auth`, `payment`, `not_found`, `timeout`, `rate_limit`, `provider`), plus `limit_reached`, `cost_limit`, `cache_miss`, `parse_error` |
| `verdict` | `confirmed`, `no_problem`, `cannot_assess`, empty |

A human value of `unreadable` is a final human reading, not an accepted sign value. The sign leaves the manual queue and gets `not_comparable` with reason `sign_unreadable` (or `abstain` if the link is untrusted). This fills a gap in the PRD: without it, an analyst could never close a sign whose value cannot be read.

---

## 4. `results.csv`

UTF-8, comma-separated, header row, `\n` line endings, one row per sign (AC-05), sorted by `photo_id`. Empty means missing. Columns in this order:

| # | Column | Type | Content |
|---|---|---|---|
| 1 | `photo_id` | text | |
| 2 | `area` | text | from `signs.csv` |
| 3 | `sequence` | text | |
| 4 | `outcome` | code | §3 |
| 5 | `abstain_reason` | text | §3 |
| 6 | `not_comparable_reason` | text | §3 |
| 7 | `value` | text | accepted value: number, `none`, `unreadable` (human only), empty |
| 8 | `tier` | code | §3 |
| 9 | `model` | text | model of the accepted answer, `human`, or empty |
| 10 | `tier1_model` | text | model used as tier 1 in this run |
| 11 | `tier1_answer` | text | top label, empty if no answer |
| 12 | `tier1_probability` | decimal | top probability, 4 decimals |
| 13 | `tier2_model` | text | |
| 14 | `tier2_answer` | text | |
| 15 | `escalation_reasons` | text | §3 |
| 16 | `osm_way_id` | integer | from `nearest_osm.csv` |
| 17 | `distance_m` | decimal | from `nearest_osm.csv`, unchanged text |
| 18 | `match_flags` | text | unchanged |
| 19 | `osm_maxspeed` | text | raw, from `osm_roads` |
| 20 | `osm_maxspeed_forward` | text | raw, from `osm_roads.tags` |
| 21 | `osm_maxspeed_backward` | text | raw |
| 22 | `osm_maxspeed_conditional` | text | raw |
| 23 | `osm_implicit_tag` | text | key of the first implicit-limit tag with a `PL:` value, in the order `maxspeed:type`, `source:maxspeed`, `zone:maxspeed` |
| 24 | `osm_implicit_value` | text | its raw value |
| 25 | `verdict` | code | from the candidate row of `review.csv` |
| 26 | `crop_path` | text | relative to the output folder, `/` separators |
| 27 | `photo_path` | text | as above |
| 28 | `creator` | text | photo author |
| 29 | `license` | text | |
| 30 | `source_url` | text | |

No source column is included, so a Databricks run and a local run produce identical files (TAC-002-01).

---

## 5. `review.csv`

Same CSV dialect. Columns (PRD §9.3): `row_id`, `row_type`, `photo_id`, `osm_way_id`, `tier1_answer`, `tier2_answer`, `human_value`, `outcome`, `verdict`, `comment`.

- `row_id = <photo_id>:<row_type>`; `row_type` is `queue` or `candidate`.
- A `queue` row exists for every sign that had no model-accepted value in a run, including abstained signs. A `candidate` row exists for every `mismatch` or `map_missing`.
- **Merge** (AC-29): rows are never deleted. For an existing row the machine columns (`osm_way_id`, `tier1_answer`, `tier2_answer`, `outcome`) are refreshed when the sign is in the current run; `human_value`, `verdict` and `comment` are copied byte for byte. New rows are added with those three empty. Output order: `photo_id`, then `queue` before `candidate`.
- **Validation** (AC-31) runs before any model call: `human_value` ∈ answer set ∪ {empty}, after trimming spaces; `verdict` ∈ §3. The first error stops the run with `review.csv row <n> (<row_id>): invalid <column> '<value>'`, exit 2, and the file is left unchanged. A missing column or a duplicate `row_id` is also exit 2.
- A missing review file is not an error: the first run creates it.

---

## 6. `run.json`

UTF-8 JSON, 2-space indent, sorted keys. Top-level keys:

| Key | Content |
|---|---|
| `schema_version` | `1` |
| `generated_at` | ISO 8601 UTC |
| `app_version` | package version |
| `experiment_id` | `default` unless `--experiment-id` |
| `model_mode` | `off` / `cache` / `live` |
| `question_version`, `accept_rule` | `b33-v1`, `accept-v1` |
| `threshold` | float |
| `tiers` | `{"tier1": {...}, "tier2": {...}}`, each with `model`, `max_requests`, `max_cost_usd`, `timeout_s`, `reservation_usd` |
| `sample` | list of `photo_id`, or `null` for all signs |
| `sources` | list of ADR-002 metadata: `dataset`, `source_kind`, `location`, `chosen_by`, `rows`, `snapshot_rows`, `read_at`, `statement_id` |
| `warnings` | list of text |
| `input_hashes` | `{relative path: sha256}` of every local input file read |
| `counts` | `outcomes` (all six codes, zero included), `queue`, `candidates`, `reviewed`, `confirmed` |
| `tier_stats` | per tier: `sent`, `accepted`, `escalated`, `failed`, `cache_hits`, `requests`, `cost_usd`, `limit_reached` (null or `max_requests` / `max_cost_usd`), `signs_affected_by_limit` |
| `answers` | list of StageAnswer (ADR-000 §5) without the raw response: `photo_id`, `tier`, `model`, `question_version`, `image_sha256`, `status` (`answered` / `model_unavailable`), `from_cache`, `answer`, `probabilities`, `cost_usd`, `latency_ms`, `error_category`, `requested_at` |
| `sql_log` | list of `{"dataset", "statement", "statement_id", "state"}` (AC-41) |

Each run also copies `run.json` to `<out>/experiments/<experiment_id>.json`. `evaluate` reads all files there for the AC-45 table.

---

## 7. `report.md`

Headings, in AC-26 order:

```text
# Raport Map QA: znaki ograniczenia prędkości
Wygenerowano: <ISO 8601 UTC>
## Nagłówek
## Podsumowanie
## Kandydaci do weryfikacji
## Kolejka ręczna
## Nie da się porównać
## Ewaluacja
```

- The `Wygenerowano:` line is the only line that depends on time (AC-03). Read times of the sources are in `run.json`, not in the report.
- `## Nagłówek` contains a table of sources (dataset, source, location, chosen by), the OSM snapshot date, model mode, experiment, both tier models, threshold, request and cost limits, timeout, `question_version`, any warnings, and the AC-28 sentence as its own paragraph.
- Every empty section contains the line `0 pozycji`.
- Table cells escape `|` as `\|`, `<` as `&lt;`, `` ` `` as `` \` ``, and line breaks as spaces.
- Crop links: `[wycinek](<crop_path>) · [zdjęcie](<photo_path>) — <creator>, <source>, <license>` (AC-27).
- `map-qa evaluate` replaces everything from `## Ewaluacja` to the end of the file.

---

## 8. Cache and ledger

- Cache entry: `<cache-dir>/answers/<model with / replaced by __>/<question_version>/<image_sha256>.json`, containing `key` (`image_sha256`, `model`, `question_version`), `endpoint`, `http_status`, `response` (unchanged body), `usage`, `latency_ms`, `requested_at`. Only HTTP 200 responses are stored. No image bytes, no headers.
- `<cache-dir>/ledger.json`: `{"schema_version": 1, "experiments": {"<experiment_id>": {"tier1": {...}, "tier2": {...}}}}`, each tier with `model`, `attempts`, `cost_usd`, `limit_reached`. Counters are cumulative per experiment and never reset (TAC-001-04). The file is written after every attempt.

---

## 9. CLI

Commands: `map-qa run` and `map-qa evaluate`. Exit codes: **0** success; **2** configuration, input, source, review or argument error (one line on stderr naming the dataset, file, column, variable or row); **1** the user declined the live-mode confirmation.

| Option | Type and check |
|---|---|
| `--source`, `--source-osm-roads` | `local` or `databricks`. Datasets without a Databricks location stay local |
| `--config` | path to a JSON file (ADR-002 §3) |
| `--out` | folder, created if missing |
| `--model-mode` | `off`, `cache`, `live` |
| `--sample` | comma-separated tokens; each is a sample name (`stage1`), a file with one `photo_id` per line, or a `photo_id`. Unknown ids → exit 2 |
| `--threshold` | float in (0, 1], default 0.80 |
| `--max-requests-tier1`, `--max-requests-tier2` | integer ≥ 0, default 25 and 10 |
| `--max-cost-tier1`, `--max-cost-tier2` | USD float ≥ 0, default 0.05 and 0.10 |
| `--tier1-model` | model id, default `typesafe/jev-1.14` |
| `--experiment-id` | `^[a-z0-9][a-z0-9_-]{0,63}$`, default `default` |
| `--review` | path |
| `--cache-dir` | folder |
| `--yes` | skip the live-mode confirmation |
| `--dry-run` | print sources and maximum calls, exit 0, read no data |

`map-qa evaluate` takes `--out` and `--config`.

Until the live clients exist, `--model-mode live` checks `OPENROUTER_API_KEY` (exit 2 if missing) and then exits 2 with `model mode 'live' is not implemented in this build`.

On success `run` prints the output paths, an outcome table and a tier table (sent, accepted, escalated, failed, cache hits, requests, cost).

---

## 10. Consequences

- (+) Workers can implement modules in parallel against fixed names.
- (+) Results from local and Databricks runs are directly comparable.
- (−) A new column needs an ADR amendment and a test change.

**Review trigger:** a second analyst, a new output format, or phase 2.
