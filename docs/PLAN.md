# Implementation Plan — Map QA MVP (phase 1)

**Date:** 2026-11-12
**Based on:** [PRD](PRD.md), [ADR-000](ADR/000-main-architecture.md), [ADR-001](ADR/001-decision-cascade.md), [ADR-002](ADR/002-data-sources-and-reports.md), [ADR-003](ADR/003-spatial-and-speed-normalization.md)
**Branch:** `map-app-implementation`
**Execution plan:** [plans/implementation-plan.md](plans/implementation-plan.md)

The plan splits the MVP into increments. Each increment is one TDD cycle (test → Red for the right reason → minimum code → Green → refactor), one commit, and a manual CLI check once the CLI exists. Increments 0–7 need no network or keys and together complete **stage 0 locally** (PRD §12); increment 9 completes stage 0 with OSM roads from Databricks (AC-39). Increments 9–10 wait for the prerequisites under "Blockers".

---

## Blockers and prerequisites

| # | Prerequisite | Blocks | Status (2026-11-12) |
|---|---|---|---|
| B1 | `uv` installed on the VM | everything | **Resolved:** uv 0.13.7, Python 3.12.16 managed by uv |
| B2 | Decision on the uncommitted PRD / `ADR/000-local-sign-mvp.md` on `main` | repository consistency | **Resolved:** plan scope is the full MVP from this branch; the older draft is archived in the local branch `archive/local-sign-mvp` |
| B3 | `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` + one capture of `SELECT … LIMIT 5` | increment 9 | **Access confirmed** (count read `SUCCEEDED`, profile §9). The variables must be set in the shell that starts the agent. **Capture still missing** |
| B4 | Budget and cost limit granted by the trainer, `OPENROUTER_API_KEY` | increment 10 | **Resolved 2026-11-12:** budget granted (in-app models < 1 USD in total); key present in the agent environment |

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
- `inputs`: column and duplicate validation, crop SHA-256, `osm_roads` row count vs `sources.json` → warning (AC-43; the sign files have no snapshot count).
- Input error → one line, exit code 2, no report.
- Integration test on the real files: 119 signs; input hashes before = after.

### 3. Speed normalization and comparison — AC-06, AC-19…AC-24 (ADR-003 §3)
- `osm_speed`: `kind` (`numeric`, `numeric_other_unit`, `implicit`, `special`, `multi`, `conditional`, `unparsed`), `raw` byte-for-byte (TAC-003-01).
- `compare`: `abstain` (flags, road missing from OSM, `area ≠ Kraków`), `pending_human`, `not_comparable`, `map_missing`, `match`/`mismatch`.

### 4. Review CSV — AC-29…AC-31
- `review`: validation of human values and verdicts (error → row and value, file unchanged), deterministic merge by `row_id`, human entries never overwritten.

### 5. Cascade in `off` and `cache` modes + ledger — AC-16, AC-17, AC-30
- `cascade`: human value → mode → per-tier cache (modes `cache`/`live` only; `off` never reads the cache) → queue. Model clients as an interface with no network implementation yet.
- `ledger`: cache in `app/.cache/map-qa/` (key: image SHA-256 + model + `question_version`), persistent counters.
- The test transport raises on every request (TAC-000-02).

### 6. Export — AC-03, AC-25…AC-28
- `export`: `results.csv`, `review.csv`, `report.md` (section order from AC-26, the AC-28 sentence, author/source/license next to links, "0 pozycji", escaping of `|`, `<`, `` ` ``), `run.json`.
- Determinism: two runs → identical report apart from the timestamp line.
- **Milestone — stage 0 locally:** `uv run map-qa run` on 119 signs; trace one sign by hand from crop to report row; one review round with an entered value and verdict.

### 7. Evaluation — AC-32…AC-36
- `evaluate`: the only reader of `labels.csv` (architecture test TAC-000-05); per-tier metrics with denominators; Mapillary detector baseline; threshold recalculation from stored distributions.
- Test TAC-003-02: labels substituted as readings → 13 `match` / 18 `mismatch` / 5 `map_missing` / 9 `not_comparable` (2 implicit-limit roads, decision of 2026-11-12).
- Samples `app/samples/stage1.txt` (5) and `stage2.txt` (20) per PRD §12, selected by a one-off script.
### 8. (buffer) Cleanup and review
- `/code-review` of the whole change; update `app/README.md` with actual usage.

### 9. Databricks — AC-39, AC-41, AC-42 (needs B3)
- First save the real captures listed in ADR-002 §7 (LIMIT 5, parameterized read, `wait_timeout = 0s` polling, deliberate FAILED), then build the mocks from them.
- REST adapter: `:id_n` markers, `SELECT` guard, polling every 2 s up to 180 s, no repeated POST, error categories.
- E2E: `osm_roads` from Databricks = local result (TAC-002-01).

### 10. `live` mode and stages 1–3 of PRD §12 (needs B4)
- `decisions_client` (model-agnostic: Jev 1.14 and Luna Decisions), `llm_client` (GPT-6 Luna); all through the OpenRouter API (httpx, 40 s, no retries), cost reservation, error mapping, confirmation before the first paid call.
- The first request to each model → capture; parser mocks only from captures.
- Stage 1 (5 signs), stage 2 (20), stage 2b (Luna Decisions as tier 1 on the same 25, AC-45), stage 3 (`cache`, zero calls) and the "met / not met" target report.

---

## Open points to clarify in ADR-001

1. ~~Tier-2 cache in `cache` mode~~: **closed 2026-11-12.** The cache is checked per tier; a miss counts as `model_unavailable`; `off` never reads the cache (ADR-001 §5, PRD §4.5).
2. ~~CLI option names~~: **closed 2026-11-12.** ADR-000 §6 now lists `--model-mode`, `--sample`, `--yes` and `--dry-run`. The contract test in increment 1 fixes them.

---

## Execution log

Tracker for [plans/implementation-plan.md](plans/implementation-plan.md) (§10). Task ids T0–T19 are from its dependency matrix.

- T0 tool probe (2026-11-12): Haiku subagent, `codex exec -m gpt-6-luna -s read-only` (exit 0) and `opencode run -m xiaomi-token-plan-ams/mimo-v2.6-flash` (exit 0) all answered; no file changed.
- T0a: `fork/main` merged without conflicts; root `AGENTS.md` unchanged by the merge.
- T1: [ADR-004](ADR/004-mvp-file-and-cli-contract.md) written (Accepted by pre-authorization, D2).
- T2 scaffold, T3 models + acceptance tests (Red: `unrecognized arguments: --out`), T13 Databricks captures (5 real responses: count, LIMIT 5, parameterized, async PENDING→SUCCEEDED, FAILED; scanned for token and host).
- Wave A: T4 (Codex `gpt-6.1-sol`, 69 tests), T5 (Haiku, 24 tests), T6 (OpenCode MiMo flash, 14 tests; its claim that `140` is missing from the answer set was checked and is false), T7 (Codex `gpt-6-luna`, 4 tests; the orchestrator added Polish header labels, LF output and tests for escaping and AC-27 links), T8 (Haiku, 56 tests).
- T9: off pipeline, full CLI, dry-run; stage-0 e2e marker removed. Windows fix: links fall back to absolute paths when the output folder is on another drive.
- **CP-1, stage 0 local (2026-11-12):** `uv run --frozen pytest` 182 passed, 0 skipped (Databricks worker files excluded while in progress). `uv run map-qa run --dry-run` exit 0. `uv run map-qa run` on 119 signs: abstain 58, pending_human 61, 0 requests. Review round in `reports/map-qa/run/review.csv` (values 70, 50, 30): match 1, mismatch 1, map_missing 1, pending 58; verdict `confirmed` kept, summary "Zweryfikowani: 1, Potwierdzeni: 1". Trace: `mapillary_1035052394661534` crop link resolves, value 70 (human) vs `maxspeed=60` → mismatch, candidate row added. Input SHA-256 before = after.
- T14: Databricks REST adapter (Codex `gpt-6.1-sol`, 45 tests from captures) wired into the CLI (`--source-osm-roads databricks`, `MAPQA_SOURCE*`, config).
- **CP-4, stage 0 with Databricks (2026-11-12):** `uv run --frozen pytest --run-live tests/e2e/test_databricks_parity.py` 1 passed: real warehouse, no mocks, `results.csv` byte-identical to the local run (AC-39, TAC-002-01), `sql_log` = 2 SELECT statements, both SUCCEEDED, 88 roads read, snapshot 99 768; token and host absent from all output files. Manual run `map-qa run --config config.example.json --source-osm-roads databricks`: abstain 58, pending_human 61.
- T10: ledger and cache mode (orchestrator; the plan assigned Codex `gpt-6.1-sol`, done in the main session because `cascade.py` is an orchestrator hotspot). **CP-2 (2026-11-12):** `map-qa run --model-mode cache` with an empty cache on 119 signs: tier 1 and tier 2 sent 119, failed 119 (`cache_miss`), 0 requests, no ledger written; abstain 58, pending_human 61.
- T11: evaluation (Codex `gpt-6-luna`; orchestrator fixed per-sign chain counts, false acceptances on measurable labels only, and a division by zero). T12: samples (Haiku), 5 + 20 ids, criteria test green, the 4 hard cases included.
- **CP-3 (2026-11-12):** `map-qa evaluate` on the CP-1 run: 119 evaluated, 114 measurable, 5 excluded (4 uncertain, 1 unreadable); detector baseline 88/89 numeric, 4/114 false acceptances, 22/25 negatives as `none`; comparison check 13/18/5/9 (TAC-003-02). Finding: PRD §12 says the detector "matches all 89 numeric labels", but `mapillary_940500342204329` (label 60) has the class `end-of-maximum-speed-limit-60`, which AC-35 maps to `none`; the code follows AC-35.
- T15: model captures. Jev rejected small crops (`400 Invalid image dimensions`, also at 274×287 px; 512×512 px accepted), so crops are normalized to 512–1568 px PNG (ADR-001 §4a). All three models run as BYOK: `usage.cost` 0, cost in `upstream_inference_cost`.
- T16: OpenRouter backends (Codex `gpt-6.1-sol`, 50 tests from captures); live mode with key check and confirmation (orchestrator). Live E2E `tests/e2e/test_live_models.py` (real endpoints, no mocks, own experiment and cache) passed.
- **CP-5, stages 1–3 of PRD §12 (2026-11-12, real endpoints, `reports/map-qa/run-eval/`):**
  - Stage 1 (`--sample stage1`, live): tier 1 5 requests, 4 accepted; tier 2 1 request, 1 accepted.
  - Stage 2 (`--sample stage1,stage2`, live): tier 1 20 new requests + 5 cache hits, 23/25 accepted; tier 2 1 new request + 1 cache hit. Ledger `default`: tier 1 25 attempts, tier 2 2 attempts.
  - Stage 2b (`--tier1-model openai/gpt-6-luna-decisions --max-requests-tier2 0 --experiment-id luna-decisions`): 25 requests, 6/25 accepted at 0.80; tier 2 disabled (2 tier-2 answers came from the shared cache).
  - Stage 3 (`--model-mode cache`): 0 requests, 25 + 2 cache hits, same readings (AC-03).
  - Targets: chain false acceptances 1/24 (met; the zone sign `mapillary_227382599193004`, tier 1 "30" p 0.54, tier 2 accepted "30"); hard cases 3/3 answered `none` (met); manual queue 0/25 (met); stage-3 requests 0 (met); **the unreadable sign `mapillary_137982605041578` did not reach the queue (not met)**: Jev answered `unreadable` (p 0.44) and escalated, GPT-6 Luna answered "50" and tier 2 accepts any in-set answer (PRD §4.4).
  - AC-45: Jev 23/25 accepted, 0/23 false, median 281 ms, max 422 ms, 0.002095 USD; Luna Decisions 6/25 accepted, 0/6 false, median 891 ms, max 4 093 ms, 0.002471 USD.
- **T17, manual QA (2026-11-12):** real CLI in every mode: `--dry-run` (exit 0, no data read), `off` on 119 signs, review round with values and a verdict, `cache` (0 requests), Databricks `osm_roads`, `evaluate`, and `live` on the stage-1 and stage-2 samples. Traces: `mapillary_1035052394661534` (human 70 vs `maxspeed=60` → mismatch candidate) and `mapillary_227382599193004` (tier 1 "30" p 0.54 → tier 2 "30" → match). Build (`uv sync`), tests and startup recorded separately in each checkpoint.
- **T18, independent review:** Claude `/code-review` (it reviewed `main..mvp-base`, the documents only): accepted the ADR-000 table fix, a CLI contract test and Markdown escaping of `\`, `[`, `]`; rejected the reservation finding (real BYOK costs ~8–10e-05 USD per request are below the reservations); `.gitignore` was already fixed; the human-`unreadable` rule and the `PL:sign…` implicit-tag rule (no affected road in the data) go to the PR for a human decision. Codex `codex exec review --base mvp-base` (`gpt-6.1-sol`): 3 × P2, all accepted and fixed: configured local paths ignored; stale machine columns in kept review rows (fixed by Haiku from a brief); AC-45 label sets of kept experiments.
- **CP-6 (2026-11-12):** `uv run --frozen pytest` 346 passed, 2 skipped (live); `--run-live` E2E 2 passed (Databricks parity, OpenRouter stage 1 + cache re-run); smoke: dry-run, off run with the review round kept, cache re-run of stages 1–2 with 0 requests and an unchanged AC-45 table.
- T19: `app/README.md` usage; agent briefs committed (D9). Push and PR wait for D7.
- **T19 done (2026-11-13):** D7 granted by the user; branch pushed to `fork`, PR https://github.com/sosnowskaga/jsystems-claude-codex-gis-2026-11/pull/2 opened against `main` (not merged).
