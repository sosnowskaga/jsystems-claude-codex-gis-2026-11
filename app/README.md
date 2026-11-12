# Map QA CLI

Reads speed-limit sign crops through a decision-model → LLM → human cascade, compares the reading with the OSM speed tags of the nearest road, and writes a Markdown report, a results CSV and a review CSV. Requirements: [PRD](../docs/PRD.md). Architecture: [ADR-000…004](../docs/ADR/); the file and CLI contract is [ADR-004](../docs/ADR/004-mvp-file-and-cli-contract.md).

## Setup

```powershell
cd app
uv sync
uv run --frozen pytest            # live tests are skipped
uv run map-qa --help
```

On Windows set `$env:PYTHONUTF8 = "1"` when you pipe the output.

## Run

| Goal | Command (from `app/`) |
|---|---|
| Show sources and the maximum number of calls, read nothing | `uv run map-qa run --dry-run` |
| Whole workflow without models (default mode `off`) | `uv run map-qa run` |
| Re-use cached model answers only, no network call | `uv run map-qa run --model-mode cache --sample stage1,stage2` |
| Call the models (asks for confirmation; `--yes` skips it) | `uv run map-qa run --model-mode live --sample stage1` |
| Decision-model comparison (stage 2b) | `uv run map-qa run --model-mode live --sample stage1,stage2 --tier1-model openai/gpt-6-luna-decisions --max-requests-tier2 0 --experiment-id luna-decisions` |
| OSM roads from Databricks | `uv run map-qa run --config config.example.json --source-osm-roads databricks` |
| Rebuild the evaluation section | `uv run map-qa evaluate` |

Outputs go to `reports/map-qa/run/` (ignored by Git): `report.md`, `results.csv`, `review.csv`, `run.json` and `experiments/<experiment_id>.json`. The answer cache and the limits ledger are in `app/.cache/map-qa/` (ignored). Exit codes: 0 success, 2 configuration/input/source/review error (one line naming the dataset, file, column, variable or row), 1 live confirmation declined.

## Review round

1. Open `reports/map-qa/run/report.md` and `review.csv`. Each row links to the crop and the full photo with author, source and licence.
2. In `queue` rows enter `human_value`: a number from the answer set (5–140 km/h), `none` or `unreadable`.
3. In `candidate` rows enter `verdict`: `confirmed`, `no_problem` or `cannot_assess`, and an optional `comment`.
4. Run `uv run map-qa run` again. Your values are kept byte for byte; a sign with a human value is never sent to a model. An invalid value stops the run with the row number and leaves the file unchanged. Back up `review.csv` yourself: it is not versioned.

## Configuration and environment

Precedence: option > environment variable > configuration file > default (local files). See `config.example.json`; the file holds only **names** of credential variables, and a credential value in it is rejected.

| Variable | Use |
|---|---|
| `OPENROUTER_API_KEY` | `live` mode only; checked for presence before the confirmation |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | when `osm_roads` comes from Databricks |
| `DATABRICKS_SQL_WAREHOUSE_ID` | overrides `databricks.warehouse_id` from the configuration |
| `MAPQA_CONFIG`, `MAPQA_SOURCE`, `MAPQA_SOURCE_OSM_ROADS`, `MAPQA_MODEL_MODE`, `MAPQA_DATA_DIR`, `MAPQA_CACHE_DIR` | defaults for the matching options |

## Tests

- `uv run --frozen pytest` — unit, integration and offline E2E tests.
- `uv run --frozen pytest --run-live tests/e2e` — real OpenRouter and Databricks calls, no mocks (needs the variables above; a few cents at most).
- Mocks in unit and integration tests are built only from the real captures in `tests/fixtures/captures/` (`reports/map-qa/capture_*.py` recreates them).
