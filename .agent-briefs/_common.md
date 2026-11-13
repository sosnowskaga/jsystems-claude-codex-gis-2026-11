# Common rules for every Map QA worker brief

- Repository root: `D:\jsystems-claude-codex-gis-2026-11`. Application: `app/` (Python 3.12, uv, src layout, package `map_qa`).
- Read first: `app/AGENTS.md`, `app/src/map_qa/models.py` (shared dataclasses and codes — **read-only**), `docs/ADR/004-mvp-file-and-cli-contract.md`.
- **Edit only the files listed in your brief.** Do not touch `pyproject.toml`, `uv.lock`, `models.py`, `cli.py`, `conftest.py`, `.gitignore`, docs. If you need a change there, stop and report it.
- Do not commit, do not push, do not delete files.
- TDD: write the tests first, run them, confirm they fail for the expected reason (ImportError / missing function counts), then implement the minimum code, then run them green. Refactor only while green.
- Test command, from `app/`: `uv run --frozen pytest <your test files> -q`. Never run `uv add`, `uv lock` or `uv sync` with changes.
- Input data in `course-materials/exercise-data/map-qa/` is read-only. Never write there.
- Do **not** read `signs/labels.csv` (only the evaluation module may).
- **Test-double rule:** manual and E2E tests never use mocks. Unit and integration tests may use mocks only when derived from a real capture in `app/tests/fixtures/captures/`. Never invent an HTTP response shape. Your task needs no HTTP at all.
- Style: type hints, `from __future__ import annotations`, standard library + the pinned deps (duckdb 1.5.6, pyarrow 23.0.1, httpx 0.28.1). No new dependency.
- Errors that must stop the run are raised as `map_qa.models.MapQaError` with a one-line English message naming the dataset/file/column/row/field.

## Report (end your work with this section)

1. Files changed or created.
2. Commands run, with a short summary of their output (test counts).
3. Red → Green evidence: the failing output before implementation and the passing output after.
4. Any contradiction or gap you found in the brief, models.py or ADR-004 (do not resolve it by changing scope).
