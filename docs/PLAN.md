# Implementation Plan — Map QA MVP (phase 1)

**Date:** 2026-11-12
**Based on:** [PRD](PRD.md), [ADR-000](ADR/000-main-architecture.md), [ADR-001](ADR/001-decision-cascade.md), [ADR-002](ADR/002-data-sources-and-reports.md), [ADR-003](ADR/003-spatial-and-speed-normalization.md)
**Branch:** `worktree-prototype`

The plan splits the MVP into increments. Each increment is one TDD cycle (test → Red for the right reason → minimum code → Green → refactor), one commit, and a manual CLI check once the CLI exists. Increments 0–7 need no network or keys and together complete **stage 0** of PRD §12. Increments 9–10 wait for the prerequisites under "Blockers".

---

## Blockers and prerequisites

| # | Prerequisite | Blocks | Status (2026-11-12) |
|---|---|---|---|
| B1 | `uv` installed on the VM | everything | **Missing** (`uv: command not found`; system Python is 3.14.9, the ADR requires 3.12 through uv) |
| B2 | Decision on the uncommitted PRD / `ADR/000-local-sign-mvp.md` on `main` | repository consistency | Plan scope: the full MVP from this branch (user decision, 2026-11-12). The files on `main` are to be removed or archived |
| B3 | `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` + one capture of `SELECT … LIMIT 5` | increment 9 | unknown |
| B4 | Budget and cost limit granted by the trainer, `OPENROUTER_API_KEY` | increment 10 | none |

---

## Increments

### 0. Project scaffold
- `app/pyproject.toml` (Python 3.12, DuckDB 1.5.6, pyarrow 23.0.1, httpx 0.28.1, pytest 9.2.0), `uv.lock`, package `app/src/map_qa/`, entry point `map-qa`.
- `app/tests/{unit,integration,e2e}/`; the `live` marker is skipped by default with a stated reason.
- Rewrite `app/README.md` (currently a Next.js template) and add a short `app/AGENTS.md` with the commands `uv sync`, `uv run pytest`, `uv run map-qa`.
- **Verification:** `uv sync`; `uv run pytest` (discovery: 0 tests or a smoke test); `uv run map-qa --help`.

### 1. Configuration and CLI contract — AC-37, AC-38, AC-40, AC-44
- A CLI contract test fixes the option names: `--source`, `--source-<dataset>`, `--config`, `--out`, `--model-mode`, `--sample`, `--threshold`, `--max-requests-tier1/2`, `--max-cost-tier1/2`, `--review`, `--yes`, `--dry-run`.
- `config`: precedence option > env var > file > `local`, with `chosen_by`; secret rejection (field name, never the value); `app/config.example.json`.
- `--dry-run`: sources, locations, max 25 + 10 calls, exit 0, no data read and no HTTP (TAC-002-03).

### 2. Local input reading — AC-01, AC-02, AC-05, AC-43
- `local` adapter (DuckDB, explicit types, identifiers as text, empty → NULL) and the shared table contract with source metadata.
- `inputs`: column and duplicate validation, crop SHA-256, row counts vs `sources.json` → warning.
- Input error → one line, exit code 2, no report.
- Integration test on the real files: 119 signs; input hashes before = after.

### 3. Speed normalization and comparison — AC-06, AC-19…AC-24 (ADR-003 §3)
- `osm_speed`: `kind` (`numeric`, `numeric_other_unit`, `implicit`, `special`, `multi`, `conditional`, `unparsed`), `raw` byte-for-byte (TAC-003-01).
- `compare`: `abstain` (flags, road missing from OSM, `area ≠ Kraków`), `pending_human`, `not_comparable`, `map_missing`, `match`/`mismatch`.

### 4. Review CSV — AC-29…AC-31
- `review`: validation of human values and verdicts (error → row and value, file unchanged), deterministic merge by `row_id`, human entries never overwritten.

### 5. Cascade in `off` and `cache` modes + ledger — AC-16, AC-17, AC-30
- `cascade`: human value → cache → (mode) → queue. Model clients as an interface with no network implementation yet.
- `ledger`: cache in `app/.cache/map-qa/` (key: image SHA-256 + model + `question_version`), persistent counters.
- The test transport raises on every request (TAC-000-02).

### 6. Export — AC-03, AC-25…AC-28
- `export`: `results.csv`, `review.csv`, `report.md` (section order from AC-26, the AC-28 sentence, author/source/license next to links, "0 pozycji", escaping of `|`, `<`, `` ` ``), `run.json`.
- Determinism: two runs → identical report apart from the timestamp line.
- **Milestone — stage 0 locally:** `uv run map-qa run` on 119 signs; trace one sign by hand from crop to report row; one review round with an entered value and verdict.

### 7. Evaluation — AC-32…AC-36
- `evaluate`: the only reader of `labels.csv` (architecture test TAC-000-05); per-tier metrics with denominators; Mapillary detector baseline; threshold recalculation from stored distributions.
- Test TAC-003-02: labels substituted as readings → 13 `match` / 18 `mismatch` / 7 `map_missing` / 7 `not_comparable`.
- Samples `app/samples/stage1.txt` (5) and `stage2.txt` (20) per PRD §12, selected by a one-off script. Check whether `reports/map-qa/pilot-manifest.csv` from `main` (25 rows) meets the PRD §12 criteria; if not, select again.

### 8. (buffer) Cleanup and review
- `/code-review` of the whole change; update `app/README.md` with actual usage.

### 9. Databricks — AC-39, AC-41, AC-42 (needs B3)
- Capture a real `SELECT … LIMIT 5` first, then build the mock from the capture.
- REST adapter: `:id_n` markers, `SELECT` guard, polling every 2 s up to 180 s, no repeated POST, error categories.
- E2E: `osm_roads` from Databricks = local result (TAC-002-01).

### 10. `live` mode and stages 1–3 of PRD §12 (needs B4)
- `decisions_client`, `llm_client` (httpx, 40 s, no retries), cost reservation, error mapping, confirmation before the first paid call.
- The first request to each model → capture; parser mocks only from captures.
- Stage 1 (5 signs), stage 2 (20), stage 3 (`cache`, zero calls) and the "met / not met" target report.

---

## Open points to clarify in ADR-001

1. **Tier-2 cache in `cache` mode:** when tier 1 is cached with p < 0.80, does the cascade use a cached tier-2 answer? Proposal: yes, the cache is checked per tier; no entry → `model_unavailable`.
2. **CLI option names:** ADR-000 §6 does not list `--model-mode`, `--sample`, `--yes`, `--dry-run` among the options, although its table uses them. The contract test in increment 1 will fix them.
