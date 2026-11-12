# Implementation plan: Map QA MVP, whole application in one continuous flow

> **Status: DRAFT, awaiting human review.** This plan does not authorize execution, new agents, paid calls, publishing or merging. Nothing below has been started.

| | |
|---|---|
| Date | 2026-11-12 |
| Branch | `map-app-implementation` (tracks `fork/map-app-implementation`, remote `sosnowskaga/jsystems-claude-codex-gis-2026-11`) |
| Requirements | [PRD](../PRD.md), [ADR-000](../ADR/000-main-architecture.md), [ADR-001](../ADR/001-decision-cascade.md), [ADR-002](../ADR/002-data-sources-and-reports.md), [ADR-003](../ADR/003-spatial-and-speed-normalization.md), [data profile](../../reports/map-qa/profile.md) |
| Increment list | [docs/PLAN.md](../PLAN.md), increments 0–10. This plan orders them into phases and assigns agents |
| Design guidance | None applies. The MVP has no UI ([PRD §7](../PRD.md#7-out-of-scope-mvp), [§9](../PRD.md#9-ui-description)), and `docs/design-guidelines.md` does not exist. Report language follows [PRD §8, Functional](../PRD.md#functional): Polish headings and labels, raw identifiers unchanged |

---

## 1. Goal and execution mode

**Goal:** the whole PRD MVP (phase 1), built test-first in one autonomous flow after a single upfront authorization. The orchestrator runs phases P0–P9 in order without waiting for a human between phases. It stops only on the **stop conditions** in §7.

Every requirement that needs a human is moved to the **pre-flight decision sheet (§2)**, so it can be answered before the flow starts. Inside the flow, the human gates become **automatic checkpoints**. At each one the orchestrator reproduces the checks and records evidence in the tracker. The human reviews everything once, at the end, through the pull request.

**Phases (logical order):**

| Phase | Theme | PLAN increments | Needs network or keys |
|---|---|---|---|
| P0 | Pre-flight: tools, sync with `fork/main` | — | git only |
| P1 | Contracts and scaffold: ADR-004, `pyproject`, models, failing acceptance tests | 0 | no |
| P2 | Core domain, in parallel: speed rules, comparison, local inputs, review, export, config | 1–4, 6 | no |
| P3 | Integration: off pipeline, full CLI, dry-run, review round → **stage 0 complete locally** | 1, 5 (off), 6 | no |
| P4 | Cache, ledger, `cache` mode | 5 | no |
| P5 | Evaluation and samples | 7 | no |
| P6 | Databricks source → **stage 0 with Databricks** | 9 | Databricks variables (verified working) |
| P7 | Live model clients, stages 1–3 | 10 | `OPENROUTER_API_KEY` + budget (**B4**) |
| P8 | Hardening: manual QA, independent review, fixes | 8 | no |
| P9 | Tracker, docs, push, PR | — | GitHub (pre-authorized only) |

---

## 2. Pre-flight decision sheet (answer once, before the flow starts)

| # | Decision | Proposal | If unanswered |
|---|---|---|---|
| D1 | `fork/main` has 15 commits this branch lacks; `git merge-tree` predicts no conflicts. Merge first? PR base? | Merge `fork/main` in P0, so the trainer's latest `AGENTS.md` applies. PR base: `sosnowskaga:main` | P0 skips the merge; PR base is still asked at the end |
| D2 | Accept ADR-004 (§3) without a separate review stop? | Yes. The orchestrator writes it as Accepted-by-preauthorization, and the human reviews it in the PR | Flow stops after T1 |
| D3 | ~~Per-tier cache in `cache` mode~~ | **Decided 2026-11-12:** per-tier cache; `off` never reads it ([ADR-001 §5](../ADR/001-decision-cascade.md#5-client-limits-cache-capture)) | — |
| D4 | ~~P7 live calls: budget~~ | **Granted 2026-11-12.** Jev 1.14, Luna Decisions and GPT-6 Luna may be used for captures, E2E and manual tests without a practical cap (total < 1 USD). `OPENROUTER_API_KEY` was present in the agent's environment on 2026-11-12 (checked for presence only). The PRD stage limits (≤ 25 tier-1, ≤ 10 tier-2) remain the app's per-experiment defaults for stages 1–2. Test runs use a separate `experiment_id`, so they do not consume the stage ledger | Only if the key is missing when P7 starts: P7 is skipped and listed as blocked in the PR |
| D5 | Stage-1 and stage-2 samples (`app/samples/*.txt`) go into live use without a human approving the selection ([ADR-000 §8](../ADR/000-main-architecture.md#samples-as-fixed-identifier-lists) requires approval before the first paid call) | Approve now: the selection must meet the PRD §12 criteria, checked by a test | P7 stops before the first paid call |
| D6 | Databricks capture and the Databricks E2E run use the shared warehouse | Yes; the 4 captures listed in [ADR-002 §7](../ADR/002-data-sources-and-reports.md#7-test-strategy), then one parity run | P6 is skipped and listed as blocked in the PR |
| D7 | Allow `git push fork map-app-implementation` and `gh pr create` at the end without asking | Yes, push to `fork` only, no merge | Flow stops before P9 push |
| D8 | Project `opencode.json` with deny rules (`rm`, `git push`, `.env` reads), so OpenCode workers can run with `--auto`? | Add it in P0 only if the T0 probe shows `opencode run` cannot finish without `--auto` | OpenCode tasks go to Haiku |
| D9 | Commit `.agent-briefs/*.md`; git-ignore `.agent-briefs/logs/` and `reports/map-qa/run/` | Yes (outputs ignored: human decision of 2026-11-12) | — |

---

## 3. Architecture to fix first: ADR-004 (task T1)

The PRD and ADRs name the fields but leave the following details open. Parallel workers need them fixed, so they go into `docs/ADR/004-mvp-file-and-cli-contract.md`. They are not assumptions in this plan.

Already decided elsewhere, so not repeated in ADR-004:
- the output directory `reports/map-qa/run/`, git-ignored ([ADR-000 §3](../ADR/000-main-architecture.md#repository-structure));
- the option names ([ADR-000 §6](../ADR/000-main-architecture.md#6-cli-interface));
- `off` never reading the cache, and the per-tier cache ([ADR-001 §5](../ADR/001-decision-cascade.md#5-client-limits-cache-capture));
- `abstain` taking precedence over `pending_human` ([AC-24](../PRD.md#comparison-with-the-map));
- a missing model key failing with exit 2 ([PRD §4.7](../PRD.md#47-errors) item 5);
- `run` calling `evaluate` ([ADR-000 §4](../ADR/000-main-architecture.md#4-modules-and-dependencies)).

ADR-004 fixes:
1. **File contract:**
   - exact column names, order and types of `results.csv` and `review.csv` ([PRD §9.3](../PRD.md#93-review-csv), [ADR-000 §5](../ADR/000-main-architecture.md#5-data-models));
   - `run.json` keys;
   - Polish section headings of `report.md` in the [AC-26](../PRD.md#outputs-and-review) order;
   - the cache entry layout and `ledger.json` keys ([ADR-001 §5](../ADR/001-decision-cascade.md#5-client-limits-cache-capture)).
2. **CLI behaviour per phase:** exit codes 0 and 2, and the argument types of each option. Before P7, `--model-mode live` exits 2 with "not implemented in this build".

---

## 4. Baseline (inspected 2026-11-12)

| Item | Finding |
|---|---|
| Working tree | Clean on `map-app-implementation` at `7e85e8b`; in sync with `fork/map-app-implementation` |
| Application code | **None.** `app/` holds only `README.md`, a stale Next.js/TypeScript checklist from the base repository. No `pyproject.toml`, package, entry point or tests |
| Test command | None yet. `uv run --no-project --python 3.12 pytest` in `app/` fails with `Failed to spawn: pytest`. This is the baseline, not a regression |
| Tooling verified | uv 0.13.7; Python 3.12.16 (via uv); DuckDB 1.5.6 and pyarrow 23.0.1 (via `uv run --with`); `profile_data.py` exit 0; Databricks REST count read `SUCCEEDED`; gh 2.103.3; codex-cli 0.167.2; opencode v2.2.5 |
| Input data | 119 signs, 119 crops, 119 `nearest_osm` rows, 99 768 OSM roads. Expected comparison counts: [PRD §12](../PRD.md#expected-map-outcomes-for-kraków-from-the-labels-before-any-model-run) |
| Tracker | GitHub issues are disabled on the fork. The tracker is [docs/PLAN.md](../PLAN.md) (§10) |
| Divergence | `fork/main` is 15 ahead and this branch 19 ahead; no conflicts predicted (D1) |
| Agent config | No `.claude/agents/` roles and no project `opencode.json`. `.codex/config.toml` uses `default_permissions = "course-root"`. `.claude/settings.json` denies `rm`, `env`, force pushes and pushes to `main` |

---

## 5. Agents and delegation

### Roles

| Role | Who | Does | Does not |
|---|---|---|---|
| Orchestrator | Claude Code main session, Opus 5.5 | ADR-004 and shared contracts; compiles briefs; dispatches; reads every diff; reproduces checks; integrates; commits; triages reviews | Accept work without reproducing checks |
| Worker, default | Claude Code ad hoc subagent, `model: haiku` (Haiku 5.5) | Bounded implementation and tests from a brief | Change scope, contracts or files outside the brief; commit |
| Worker, complex logic | Codex `gpt-6.1-sol` via `codex exec` | Rule-heavy and protocol modules | Same as above |
| Worker, simple or research | Codex `gpt-6-luna`; OpenCode `xiaomi-token-plan-ams/mimo-v2.6-flash` | Small pure modules, documentation lookups | Same as above |
| Independent reviewer | Codex `gpt-6.1-sol` (`codex exec review`), Claude `/code-review` | Review of the finished diff | Fix during review |
| Final acceptance | Human | Decision sheet (§2), PR review and merge | — |

The model IDs were verified locally: `codex debug models` lists `gpt-6.1-sol` and `gpt-6-luna`; `opencode models` lists `xiaomi-token-plan-ams/mimo-v2.6-flash`.

**Ad hoc subagents by default.** Every task is one-off, so an exact brief matters more than a role file. No `.claude/agents/` definition is created. Add one only if a recurring role with reusable tool restrictions appears.

### Brief files

- Path: `.agent-briefs/<task-id>-<slug>.md`, compiled **just before dispatch**.
- Each brief contains: goal; links to the PRD/ADR sections; the relevant excerpts **pasted in**; the current contracts (`models.py`, ADR-004); predecessor outputs; exact editable and read-only files; ordered TDD steps; test inputs with expected results; the commands to run; which tasks run concurrently and on which files; and a **Report** section.
- The Report section asks for: files changed, commands with output summary, Red→Green evidence, and contradictions. The worker cannot change scope or accept its own work.

### Headless commands (PowerShell, repository root)

The local `--help` of each CLI is the source of truth for these flags. Context7 (`/openai/codex`, `/websites/opencode_ai`) confirmed the behaviour that `--help` does not describe.

**Claude Code subagent** (from the orchestrator session):

```text
Agent(description="T5 inputs", model="haiku",
      prompt="Read .agent-briefs/T5-inputs.md and execute it exactly. Edit only the files it lists. Finish with its Report section.")
```

**Codex worker:**

```powershell
Get-Content -Raw .agent-briefs/T4-speed-compare.md |
  codex exec -m gpt-6.1-sol -s workspace-write -C . --json `
             -o .agent-briefs/logs/T4.last.md - > .agent-briefs/logs/T4.jsonl
```

- With `-`, the prompt is read from stdin. `-o` saves the final message; `--json` streams JSONL events, including token usage.
- **In `exec` mode Codex rejects every approval request** instead of asking, so a command that needs escalation fails and the worker reports it. Never use `--dangerously-bypass-approvals-and-sandbox`.
- `AGENTS.md` is loaded automatically from the git root down to the working directory.
- Use `-m gpt-6-luna` for simple tasks.

**Codex review** (`mvp-base` is a local branch at the commit before T2, created in T2):

```powershell
codex exec review --base mvp-base -m gpt-6.1-sol -o .agent-briefs/logs/review-codex.md
```

**OpenCode worker:**

```powershell
opencode run -m xiaomi-token-plan-ams/mimo-v2.6-flash --title "T6 review" --format json `
  -f .agent-briefs/T6-review.md "Execute the attached brief exactly. Edit only the files it lists. Finish with its Report section." `
  > .agent-briefs/logs/T6.jsonl
```

- Do not pass `--auto` unless D8 added deny rules. `--auto` approves everything that is not explicitly denied.

**After every worker:** the orchestrator runs `git status` and `git diff --stat`. It rejects edits outside the file list, reads the diff, reruns the task's verification, and only then commits.

---

## 6. Dependency matrix (authoritative)

- Paths are relative to the repository root.
- Commands under "Verification" run from `app/` and are reproduced by the orchestrator.
- One row = one commit, in the format `Area: summary`.
- **CP** = automatic checkpoint. The orchestrator runs the full suite with `uv run --frozen pytest` and reports discovery and skip counts. It also runs the **real CLI on the real files** for the phase's feature and saves the console output as evidence. It records both in the tracker.

| Task | Observable outcome | PRD/ADR links | Prerequisites | Owner/model | Editable files | Verification and handoff |
|---|---|---|---|---|---|---|
| **P0 · T0** Tool probe | Each mechanism answers a read-only prompt; `git status` stays clean | — | — | Orchestrator | none | Agent(haiku) replies; `codex exec -m gpt-6-luna -s read-only` exits 0; `opencode run -m xiaomi-token-plan-ams/mimo-v2.6-flash` finishes without hanging. A failed mechanism → its tasks go to Haiku. No commit |
| **P0 · T0a** Sync | `fork/main` merged | — | D1 | Orchestrator | merge only | `git merge fork/main` with no conflicts; reread root `AGENTS.md` for changed rules. Merge commit |
| **P1 · T1** ADR-004 + tracker fix | Contracts of §3 written; `docs/PLAN.md` header branch corrected | §3 links | T0a, D2 | Orchestrator | `docs/ADR/004-mvp-file-and-cli-contract.md`, `docs/PLAN.md`, this plan (links) | Consistency read against PRD and ADRs; link check. Commit `Docs: ADR-004 file and CLI contract` |
| **P1 · T2** Scaffold | `uv sync` works; `uv run map-qa --help`; smoke test passes | ADR-000 §3, §10, TAC-000-01; PLAN inc. 0 | T1 | Haiku | `app/pyproject.toml`, `app/uv.lock`, `app/src/map_qa/__init__.py`, `app/src/map_qa/cli.py` (stub), `app/tests/conftest.py` (`live` marker skipped with a reason; socket guard fixture), `app/tests/unit/test_smoke.py`, `app/AGENTS.md` (commands plus the test-double rule from §8), `app/README.md`, `.gitignore` | `uv sync`; `uv run pytest` (1 passed); `uv run map-qa --help`. **All runtime dependencies are pinned now**: DuckDB 1.5.6, pyarrow 23.0.1, httpx 0.28.1, pytest 9.2.0, so later phases never touch the lockfile in parallel. Then `git branch mvp-base <parent of T2>`. Commit `Backend: app scaffold` |
| **P1 · T3** Models + failing acceptance tests | `models.py` exists. Stage-0 e2e tests exist and are **Red for the right reason** | ADR-000 §5, §9.3, §10; ADR-004; AC-05, AC-17, AC-25, AC-29, AC-37 | T2 | Orchestrator | `app/src/map_qa/models.py`, `app/tests/e2e/test_stage0_local.py`, `app/tests/fixtures/review_round.csv` | The e2e test runs the real CLI twice through `subprocess` with a temporary output directory and a hand-made review fixture. Its expected outcomes are precomputed by the orchestrator from `nearest_osm.csv` and `osm_roads`, **not** from `labels.csv` (TAC-000-05). Marked `xfail(strict=True, reason="pipeline not implemented")`; the marker comes off in T9. Commit `Backend: domain models and stage-0 acceptance tests` |
| **P2 · T4** Speed normalization + comparison | All `kind` values; all six outcomes, including `implicit_limit` | AC-19…AC-24; ADR-003 §3, §6, TAC-003-01 | T3 | Codex `gpt-6.1-sol` | `app/src/map_qa/osm_speed.py`, `app/src/map_qa/compare.py`, `app/tests/unit/test_osm_speed.py`, `app/tests/unit/test_compare.py` | Red→Green: `uv run --frozen pytest tests/unit/test_osm_speed.py tests/unit/test_compare.py`. Synthetic inputs per ADR-003 §6, including `source:maxspeed=sign` → `map_missing`. Commit `Backend: OSM speed normalization and comparison` |
| **P2 · T5** Local source + inputs | Real files load with explicit types; 119 signs; missing file or column → error naming it; `osm_roads` count vs `sources.json` → warning | AC-01, AC-02, AC-05, AC-06, AC-43; ADR-000 §8; ADR-002 §3 | T3 | Haiku | `app/src/map_qa/sources/__init__.py`, `app/src/map_qa/sources/local.py`, `app/src/map_qa/inputs.py`, `app/tests/integration/test_inputs.py` | `uv run --frozen pytest tests/integration/test_inputs.py`; input hashes before = after inside the test. Implicit-limit tags come from `osm_roads.tags`. Commit `Backend: local source and input validation` |
| **P2 · T6** Review CSV | Validate and merge; invalid value → row and value, file unchanged; human entries never overwritten | AC-29, AC-31; PRD §9.3; ADR-002 §4 | T3 | OpenCode MiMo flash (fallback Haiku) | `app/src/map_qa/review.py`, `app/tests/unit/test_review.py` | `uv run --frozen pytest tests/unit/test_review.py`; key `row_id = photo_id + row_type`. Commit `Backend: review CSV validation and merge` |
| **P2 · T7** Export | `report.md`, `results.csv`, `review.csv` and `run.json` from records; deterministic apart from the timestamp line | AC-03, AC-25…AC-28; PRD §9.2; ADR-002 §4 | T3 | Codex `gpt-6-luna` | `app/src/map_qa/export.py`, `app/tests/unit/test_export.py` | `uv run --frozen pytest tests/unit/test_export.py`: section order, AC-28 sentence, "0 pozycji", escaping `|` `<` `` ` ``, relative links with author, source and licence. Commit `Backend: Markdown and CSV export` |
| **P2 · T8** Config + source selection | Precedence option > env > file > local with `chosen_by`; credential value in file → error naming the field, not the value | AC-37, AC-38, AC-40; ADR-002 §3 "Source selection" | T3 | Haiku | `app/src/map_qa/config.py`, `app/config.example.json`, `app/tests/unit/test_config.py` | `uv run --frozen pytest tests/unit/test_config.py`, including the secret patterns and the accepted warehouse ID. Commit `Backend: configuration and source selection` |
| **P3 · T9** Off pipeline + full CLI + dry-run | `map-qa run` writes 4 files for 119 signs with **zero HTTP** and without reading the cache. Untrusted links → `abstain` (precedence over `pending_human`), but still get a `queue` row. `--dry-run` exits 0 without reading data. An input error → one line, exit 2. `run` calls an `evaluate` stub that writes "0 pozycji". The stage-0 e2e test passes (marker removed) | AC-01…AC-06, AC-17, AC-24, AC-29, AC-30, AC-44; ADR-000 §6, §9.2–9.3, TAC-000-02, 03; ADR-004 | T4–T8 | Orchestrator integrates; Haiku helper for bounded fixes | `app/src/map_qa/cascade.py` (human → queue path), `app/src/map_qa/pipeline.py`, `app/src/map_qa/cli.py`, `app/tests/integration/test_run_off.py`, `app/tests/e2e/test_stage0_local.py` (remove marker) | Full suite green. **CP-1, stage 0 local:** real run on 119 signs + review round + hash check, with console output saved as evidence. Commit `Backend: off-mode pipeline and CLI` |
| **P4 · T10** Ledger + cache mode | Cache keyed by image SHA-256 + model + `question_version`; persistent counters; `cache` mode makes no network call; misses → `model_unavailable` → queue | AC-03, AC-15, AC-16, AC-17; ADR-001 §5 (per-tier cache, `off` never reads it) | T9 | Codex `gpt-6.1-sol` | `app/src/map_qa/ledger.py`, `app/src/map_qa/cascade.py`, `app/tests/unit/test_ledger.py`, `app/tests/integration/test_cache_mode.py` | No model response shape exists before T15, so **no cache entry with a model answer is created here**. Tests cover only what needs no response: cache key computation, counters and limits, persistence of `ledger.json`, the empty-cache path (miss → `model_unavailable` → queue), and that `cache` mode opens no socket. The **cache-hit path and AC-03 with a complete cache move to T16**, built from T15 captures. **CP-2.** Commit `Backend: ledger and cache mode` |
| **P5 · T11** Evaluation | Replaces the T9 stub. `run` and `map-qa evaluate` write the evaluation section with denominators; detector baseline; threshold recompute labelled exploratory; only `evaluate` reads `labels.csv` | AC-32…AC-36; ADR-000 TAC-000-05; ADR-003 §6 expected counts | T9 | Codex `gpt-6-luna` | `app/src/map_qa/evaluate.py`, `app/tests/unit/test_evaluate.py`, `app/tests/integration/test_expected_counts.py`, `app/tests/unit/test_architecture.py` | Labels as readings → 13 / 18 / 5 / 9 (TAC-003-02); architecture test passes. Commit `Backend: evaluation` |
| **P5 · T12** Samples | `app/samples/stage1.txt` (5) and `stage2.txt` (20), meeting the PRD §12 criteria | PRD §12; ADR-000 §8 "Samples" | T11 | Haiku | `reports/map-qa/select_samples.py`, `app/samples/stage1.txt`, `app/samples/stage2.txt`, `app/tests/unit/test_samples.py` | The test checks the criteria: one sign per sequence, uncertain labels excluded, the 4 hard cases present. Covered by D5. **CP-3.** Commit `Backend: evaluation samples` |
| **P6 · T13** Databricks captures | The 4 real responses listed in ADR-002 §7 (LIMIT 5, parameterized read, `wait_timeout = 0s` with GET polls, deliberate FAILED), saved verbatim: status, headers and body, no token or host | ADR-002 §2, §7; AGENTS.md mock rule | T9, D6 | Orchestrator | `app/tests/fixtures/captures/databricks_*.json`, a short capture script | Variables checked for **presence only**. Scan the capture for the token and host (TAC-002-04). If the variables are missing in the agent's shell → **skip P6** and record it as blocked. Commit `Backend: Databricks capture` |
| **P6 · T14** Databricks adapter | REST adapter: `:id_n` markers, `SELECT` guard, polling, no repeated POST, typed by `manifest.schema` (strings → `LONG`), error categories | AC-39, AC-41, AC-42; ADR-002 §3 contract, §5, §7, TAC-002-01…04 | T13 | Codex `gpt-6.1-sol` | `app/src/map_qa/sources/databricks.py`, `app/tests/unit/test_databricks_sql.py`, `app/tests/integration/test_databricks_adapter.py` | Mocks only from the T13 capture (`httpx.MockTransport`). Then one real E2E: `osm_roads` from Databricks = local result (TAC-002-01). **CP-4, stage 0 with Databricks.** Commit `Backend: Databricks source` |
| **P7 · T15** Model captures | **Before any client, parser or mock is written**, one real request per model (Jev 1.14 on the Decisions endpoint, GPT-6 Luna on Chat Completions) is sent with a minimal capture script built from the current OpenRouter docs (Context7). The capture saves verbatim the **accepted request** (endpoint, headers without `Authorization`, body with the image replaced by `<image sha256=…>`) and the **response** (status, headers without account IDs, unchanged body). The client's request format and the parser's data structure are then taken from this capture, not from documentation alone. A rejected request (4xx) is fixed and sent again; every attempt counts against the limit | ADR-001 §5, TAC-001-05 | T10, D4, D5 | Orchestrator | `app/tests/fixtures/captures/*.json`, the capture path in the clients | **Only if D4 is granted**; otherwise P7 is skipped and recorded as blocked. Each capture is the **first stage-1 request** of its model, not an extra one (ADR-001 §9), so tier 1 stays at 5 + 20 = 25 requests. Commit `Backend: model captures` |
| **P7 · T16** Model clients + live cascade | `decisions_client` and `llm_client` (httpx, 40 s, no retries); error mapping; cost reservation; confirmation before the first paid call | AC-08…AC-15, AC-18; ADR-001 §3–§6 | T15 | Codex `gpt-6.1-sol` | `app/src/map_qa/decisions_client.py`, `app/src/map_qa/llm_client.py`, `app/src/map_qa/cascade.py`, `app/tests/unit/test_clients.py`, `app/tests/integration/test_live_cascade.py` | Unit and integration mocks (`httpx.MockTransport`, cache-hit entries) are built **only from T15 captures**, including the cache-hit path and AC-03 moved from T10. **E2E and manual tests call the real endpoints with no mocks**: stage 1 (5 signs) and stage 2 (20) `live`, then stage 3 in `cache` with zero calls (AC-03). Targets from PRD §12 are reported as met or not met. **CP-5.** Commit `Backend: live model cascade` |
| **P8 · T17** Manual runtime QA | A human-style run of every mode that is available | AGENTS.md "Manual QA"; ADR-000 §10 principle 3 | T9…T16 (whichever ran) | Orchestrator | none | Run `uv run map-qa run --dry-run`, `run` (off), the review round, `--model-mode cache`, Databricks `osm_roads` if P6 ran, and `evaluate`. If D4 was granted, also one `--model-mode live` run on stage-1 signs against the **real endpoints**, within the remaining limits. Manual QA never uses mocks. Trace one sign from crop to report row. Record startup, build and tests separately. Evidence goes into the PR |
| **P8 · T18** Independent review + fixes | Two review reports; triage; accepted findings fixed | AGENTS.md "Verification" | T17 | Codex `gpt-6.1-sol` (`codex exec review --base mvp-base`) + Claude `/code-review`; fixes by Haiku with briefs | fix tasks T18a…, each with its own brief and file list | Each fix: full suite + the T17 smoke commands. **CP-6.** One commit per fix |
| **P9 · T19** Tracker, docs, PR | `docs/PLAN.md` statuses; `app/README.md` usage; PR open | — | T18, D7 | Orchestrator | `docs/PLAN.md`, `app/README.md` | `git push fork map-app-implementation`; `gh pr create` against the D1 base. The body contains the AC checklist, test counts, CP evidence, review triage, and the skipped or blocked phases. **No merge** |

### Parallelism

| Wave | Tasks in parallel | Shared inputs (read-only) | Why it is safe |
|---|---|---|---|
| A (P2) | T4, T5, T6, T7, T8 | `models.py`, ADR-004 | Disjoint editable files; dependencies locked in T2; workers run `uv run --frozen pytest <own tests>` only |
| B (after T9) | T10 ‖ T11 | pipeline, models | T10 owns `ledger.py` and `cascade.py`; T11 owns `evaluate.py`. Neither edits the other's files |
| C | T13 → T14 can run alongside T12 | — | Disjoint files (`sources/databricks.py` vs `app/samples/`) |

- Everything else is serialized: T0–T3 (contracts, lockfile), T9 (integration, `cli.py`), T15–T19.
- Every brief names its concurrent tasks and their files.
- One checkout, no worktree. A worktree is requested only if two tasks would need the same file, following the [repository rule](../../AGENTS.md#git-worktrees) and the `../<repo>-worktrees/<branch>` convention; it is removed afterwards.

### Critical path

`T0 → T0a → T1 → T2 → T3 → T4 (longest in wave A) → T9 → T10 → T15 → T16 → T17 → T18 → T19`

Without D4, the path continues from T10 directly to T17.

### Shared-file hotspots (orchestrator only, unless a brief grants them)

- `app/pyproject.toml`, `app/uv.lock`
- `app/src/map_qa/models.py`, `app/src/map_qa/cli.py`, `app/src/map_qa/pipeline.py`
- `app/src/map_qa/cascade.py`: T9, then T10, then T16, strictly in sequence
- `app/tests/conftest.py`, `.gitignore`
- `docs/PLAN.md`, `docs/ADR/*`

A worker that needs one of these stops and reports.

---

## 7. Continuous-flow rules

**The flow continues automatically when:** a checkpoint is green, or a phase is skipped because a decision from §2 says so (the skip is recorded in the tracker and the PR).

**The flow stops and waits for the human only when:**
1. a test that the specification supports cannot be made green after one re-brief and one orchestrator attempt;
2. the specification contradicts itself, or a contract change would alter accepted PRD behaviour;
3. a merge conflict or an unexpected change appears in files outside this plan;
4. a paid or shared-resource action is needed that §2 did not authorize;
5. a secret is found in any output, log or capture.

**Rollback:**
- A failed or out-of-scope worker result is not committed. Tracked files are restored with `git restore --staged --worktree <files>`. New untracked files are listed for the human to delete, because `rm` is denied.
- A bad commit is undone with `git revert <sha>`. History is never rewritten, and nothing is force-pushed.
- A wrong contract stops the current wave. The orchestrator amends the contract in its own commit and re-briefs the affected workers.

**Tool fallback:** an unavailable Codex or OpenCode → Haiku subagent. No subagents → the orchestrator runs the same matrix sequentially.

### Smaller fallback scope

If time or model access runs out, the minimum deliverable is **stage 0 locally: P0–P3 plus T17 and T18** (all offline and free). In priority order, P4, P5, P6 and P7 are cut from the end. The PR states what was cut.

---

## 8. Verification ladder

**Test-double rule (non-negotiable; copied into every brief and into `app/AGENTS.md`):**
- **Manual and E2E tests never use mocks.** They call the real endpoints: Jev 1.14 (Decisions API), GPT-6 Luna (Chat Completions) and Databricks. Luna Decisions is outside the MVP ([ADR-001 §6](../ADR/001-decision-cascade.md#6-technical-decisions)); if it is ever enabled, the same rule applies.
- **Only unit and integration tests may use mocks**, and only after a real call has been made and captured. The capture shows the accepted request (format and data) and the real response structure, and the mock is derived from it. Without a capture, the test is skipped with the reason "no capture"; a request or response shape is never invented from documentation or memory.
- A worker that cannot get a capture reports the missing prerequisite instead of writing a mock.

1. **Per task:** Red for the right reason → Green on its own tests (worker evidence, reproduced by the orchestrator).
2. **Per wave:** full suite with `uv run --frozen pytest`, with discovery and skip counts.
3. **Checkpoints CP-1…CP-6:** the real CLI on real files. A green build alone never counts as startup or a working flow.
4. **Independent review (T18):** two reviewers, triaged by the orchestrator.
5. **Human:** the decision sheet before the flow, the PR after it.

---

## 9. Cost notes

- Workers: Codex about 6 runs (Sol 4, Luna 2), OpenCode 1, Haiku about 6 plus fixes. Codex and OpenCode usage counts against the user's own plans; `--json` logs record tokens.
- **Where the money goes:** agent time on strong models (Opus orchestrator, Codex Sol) is the main cost. The app's own model calls are cheap: Jev $0.042/1M input tokens, Luna Decisions $0.10/1M, GPT-6 Luna as in ADR-001 §5. All captures, E2E and manual tests together stay under 1 USD (D4).
  - Do not ration real calls; a real capture or E2E run is always preferred to a guessed mock (§8).
  - Save on orchestration instead: bounded work goes to Haiku, Codex Luna or OpenCode MiMo. Opus and Sol are kept for contracts, integration, rule-heavy modules and review.
- Model calls happen only from P7 on. The stage-1/2 evaluation keeps the PRD limits (AC-15: ≤ 25 tier-1, ≤ 10 tier-2 per experiment), because they define the measured sample, not the budget.
- Databricks: one capture and one parity run on the shared warehouse (D6).

---

## 10. Tracker

[docs/PLAN.md](../PLAN.md) stays the single execution tracker, because issues are disabled on the fork.

- When each task is accepted, the orchestrator adds a line `Status: <task> done in <sha> (YYYY-MM-DD)` under the matching increment, in the same commit.
- Checkpoint evidence and phase skips are added there too.
- This plan is not edited for status.

---

## 11. First bounded task and review gate

- **Review gate now:** the human reviews this draft and fills in the decision sheet (§2, D1–D9).
- **After an explicit "execute":** the first task is **T0, the tool probe**. It is read-only, makes no commit, and confirms each headless mechanism before any brief depends on it. From there the flow runs to T19 under §7.
- Approving this plan does not by itself authorize execution, agents, paid calls, push or merge. Each of those needs the matching decision in §2.
