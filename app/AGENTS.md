# app/ — Map QA CLI

Python 3.12 package `map_qa` (src layout), managed with uv. Contracts: [ADR-004](../docs/ADR/004-mvp-file-and-cli-contract.md).

## Commands (run from `app/`)

- `uv sync` — install the locked environment.
- `uv run --frozen pytest` — full suite; `live` tests are skipped unless `--run-live` is given.
- `uv run --frozen pytest tests/unit/test_x.py` — one file.
- `uv run map-qa --help`, `uv run map-qa run --dry-run`, `uv run map-qa run`, `uv run map-qa evaluate`.
- On Windows set `PYTHONUTF8=1` when piping output.

Do not change `pyproject.toml` or `uv.lock` unless your task says so.

## Test-double rule (non-negotiable)

- **Manual and E2E tests never use mocks.** They call the real endpoints: Jev 1.14 and Luna Decisions (OpenRouter Decisions API), GPT-6 Luna (OpenRouter Chat Completions) and Databricks.
- **Only unit and integration tests may use mocks**, and only after a real call has been made and captured in `tests/fixtures/captures/`. The capture shows the accepted request and the real response; the mock is derived from it. Without a capture the test is skipped with the reason "no capture". Never invent a request or response shape from documentation or memory.
- A worker that cannot get a capture reports the missing prerequisite instead of writing a mock.

## Rules

- Input files in `course-materials/exercise-data/map-qa/` are read-only; tests check their hashes.
- Only `map_qa/evaluate.py` may refer to `labels.csv` (architecture test).
- Never print, log or store credential values; read them from environment variables at call time.
