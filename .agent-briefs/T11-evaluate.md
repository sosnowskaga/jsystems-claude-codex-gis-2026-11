# T11 — Evaluation section

Read `.agent-briefs/_common.md` first and follow it, **except**: this module is the only one allowed to read `signs/labels.csv`. Read PRD `docs/PRD.md` §12 (all of it), AC-32…AC-36, AC-45; ADR-001 §7; ADR-003 §6 "Expected counts"; ADR-000 §4 and TAC-000-05; ADR-004 §6 (`run.json`), §7.

## Editable files (only these)

- `app/src/map_qa/evaluate.py` (replace the stub; keep the two public functions and their signatures)
- `app/tests/unit/test_evaluate.py` (new)
- `app/tests/integration/test_expected_counts.py` (new)
- `app/tests/unit/test_architecture.py` (new)

Concurrent tasks: T12 (Haiku) edits `reports/map-qa/select_samples.py`, `app/samples/*.txt`, `app/tests/unit/test_samples.py`. The orchestrator edits `cascade.py`, `ledger.py`, `pipeline.py`, `cli.py`. Do not touch those.

## Existing API you must keep

```python
def evaluation_markdown(out_dir: Path, settings) -> str      # called by `map-qa run` after results.csv and run.json exist
def rebuild_report(out_dir: Path, settings) -> Path          # `map-qa evaluate`: replaces the evaluation section of report.md
```

`settings` is `map_qa.config.Settings` (use `settings.data_dir`). Read `pipeline.py`, `export.py`, `models.py`, `compare.py`, `inputs.py` to understand the data; do not change them.

## Inputs of the evaluation

- `<out_dir>/run.json` (ADR-004 §6): `model_mode`, `threshold`, `tiers`, `tier_stats`, `answers` (StageAnswer dicts: `photo_id`, `tier` 1/2, `model`, `status` `answered`/`model_unavailable`, `from_cache`, `answer`, `probabilities`, `cost_usd`, `latency_ms`, `error_category`), `sample`, `experiment_id`.
- `<out_dir>/results.csv` (ADR-004 §4): `photo_id`, `value`, `tier` (`decision_model`/`llm`/`human`/empty), `outcome`, `escalation_reasons`.
- `<out_dir>/experiments/*.json`: one `run.json` copy per experiment, for the AC-45 table.
- `<data_dir>/signs/labels.csv`: `photo_id, sign_value, status, method, reviewers, note, negative_type`. `sign_value` is a number, `none` or `unreadable`; `status` `certain`/`uncertain`.
- `<data_dir>/signs/signs.csv`: only the `mapillary_value` column (detector baseline). Read CSVs with the `csv` module, all as text.

## Definitions (write them as a short Polish legend above the tables)

- Evaluated signs = the signs in `results.csv`.
- **Measurable** label = `status == certain` and `sign_value != unreadable`. Excluded labels (uncertain, or `unreadable`) are listed separately with their count (AC-34).
- Numeric label = measurable and a number. Negative label = measurable and `none`.
- Per **tier** t (1, 2), from `answers` with `tier == t`: `sent` = number of answers; `failed` = `status == model_unavailable`; `accepted` = signs whose results row has `tier` `decision_model` (t=1) or `llm` (t=2); `escalated` = sent − accepted. Accuracy of accepted answers on numeric labels = correct / accepted-with-numeric-label. False acceptances = accepted answer ≠ label, over accepted-with-measurable-label. Negatives handled = negative-label signs sent to the tier that were answered `none` or escalated, over negative-label signs sent. Requests and cost from `tier_stats`; cost per accepted = cost / accepted (or `—`).
- **Chain**: accepted = rows with tier `decision_model` or `llm`; queue = rows with empty value; same accuracy/false-acceptance/negatives metrics on the chain's accepted value.
- Human tier: only the count of human readings.
- **Detector baseline** (AC-35): `mapillary_value` matching `^regulatory--maximum-speed-limit-(\d+)--` → N; any other class (including `end-of-…`) or empty → `none`. Same metrics, accepted = all evaluated signs, cost 0.
- Every metric is shown as `k/n` with its denominator; `n = 0` → `0/0`.

## Sections of the returned Markdown (Polish headings, level `###`, in this order)

1. `### Zbiór ewaluacyjny`: evaluated, measurable, numeric, negative, excluded (with a sub-list of excluded `photo_id` and reason); the note that labels are working AI labels, not ground truth (PRD §8).
2. `### Metryki poziomów` — table: rows `poziom 1`, `poziom 2`, `łańcuch`, `detektor Mapillary (bazowy)`, `człowiek`; columns as above.
3. `### Cele MVP (PRD §12)` — only when `model_mode != off`; otherwise the line `Nie dotyczy w trybie off.`. Targets, each `spełniony`/`niespełniony` with numbers: false acceptances in the chain ≤ 1; hard cases (signs whose label is negative but whose detector value is a number, plus the `unreadable`-labelled sign is separate): at least 2 of 3 answered `none` or escalated — compute the hard negatives from the data, report the actual count; the sign labelled `unreadable` ends in the queue (empty value) — if it is in the sample; queue ≤ 40 % of evaluated signs; requests in this run (the stage-3 cache re-run must show 0).
4. `### Eksploracja progu (AC-36, eksploracyjne)` — for thresholds 0.50, 0.60, 0.70, 0.80, 0.90, 0.95: from tier-1 answers with probabilities, accepted count (top label not `unreadable` and top p ≥ thr) and false acceptances among them (measurable labels). Clearly labelled exploratory and "not to be reported as accuracy". `0 pozycji` when no tier-1 probabilities exist.
5. `### Porównanie modeli decyzyjnych (AC-45)` — one row per experiment file in `experiments/` (sorted by experiment id) that has at least one tier-1 answer: experiment, tier-1 model, answered (status answered) /sent, accepted at that run's threshold, accuracy of accepted, false acceptances, negatives answered `none` or escalated, `unreadable` answers, failures, median and max `latency_ms` (answers that were not from cache only), cost (sum of tier-1 `cost_usd`), cost per accepted. `0 pozycji` when no experiment has tier-1 answers. Below the table, the line `Różnice w akceptacji żądania: zob. tests/fixtures/captures/` (the orchestrator fills the details later).
6. `### Kontrola logiki porównania (etykiety jako odczyty)` — the TAC-003-02 check: `expected_map_outcomes(data_dir)` counts (below), shown as a small table; this section never feeds the run.

```python
def expected_map_outcomes(data_dir: Path) -> dict[str, int]
```

Substitute each Kraków sign's label (`sign_value`, numeric labels only, status any) as a human reading and run the real comparison: load inputs with `map_qa.inputs.load_inputs(data_dir, LocalRoadsSource(data_dir / "osm_roads.parquet"))`, build `SignReading(photo_id, value=label, tier="human", model="human")`, call `map_qa.compare.compare`, and count outcomes for signs that are not `abstain`. Expected: `match` 13, `mismatch` 18, `map_missing` 5, `not_comparable` 9 (PRD §12, 74 numeric Kraków labels; the rest abstain).

## Tests

- `tests/integration/test_expected_counts.py`: `expected_map_outcomes(real data_dir) == {"match": 13, "mismatch": 18, "map_missing": 5, "not_comparable": 9}` (TAC-003-02). Use the `data_dir` fixture from `tests/conftest.py`.
- `tests/unit/test_architecture.py` (TAC-000-05): scan `app/src/map_qa/**/*.py`; only `evaluate.py` contains the text `labels.csv` or `labels`-file reads; and only `cli.py` imports `map_qa.evaluate` / `from map_qa import evaluate`.
- `tests/unit/test_evaluate.py` with a synthetic `out_dir` (write a small `run.json`, `results.csv`, `experiments/a.json`, `experiments/b.json`) and a synthetic `data_dir` (`signs/labels.csv`, `signs/signs.csv` with a few rows): denominators per tier; uncertain and unreadable labels excluded and listed; detector baseline mapping (`regulatory--maximum-speed-limit-50--g1` → 50, `regulatory--end-of-maximum-speed-limit-30--g1` → none, empty → none); off mode → `Nie dotyczy w trybie off.` and chain `0/0`; threshold exploration rows; AC-45 table with two experiments, median and max latency; `rebuild_report` replaces only the evaluation section of an existing `report.md` (use `map_qa.export.replace_evaluation_section`).

Verification: `uv run --frozen pytest tests/unit/test_evaluate.py tests/unit/test_architecture.py tests/integration/test_expected_counts.py -q`.
