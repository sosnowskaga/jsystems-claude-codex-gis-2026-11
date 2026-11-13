# T12 — Evaluation samples (stage 1 and stage 2)

Read `.agent-briefs/_common.md` first and follow it, **except**: your selection script and your test may read `signs/labels.csv` (the samples are selected from labels once, outside the application: ADR-000 §8 "Samples as fixed identifier lists"). Read PRD `docs/PRD.md` §12 "Stages" and "What we expect" carefully.

## Editable files (only these)

- `reports/map-qa/select_samples.py` (new, one-off script)
- `app/samples/stage1.txt` (new)
- `app/samples/stage2.txt` (new)
- `app/tests/unit/test_samples.py` (new)

Concurrent tasks: T11 edits `app/src/map_qa/evaluate.py` and its tests; the orchestrator edits `cascade.py`, `ledger.py`, `pipeline.py`, `cli.py`. Do not touch those.

## Data

`course-materials/exercise-data/map-qa/signs/labels.csv` (`photo_id, sign_value, status, method, reviewers, note, negative_type`) and `signs/signs.csv` (`photo_id`, `sequence`, `area`, `mapillary_value`, …). `negative_type` values include `cancelled_sign` (2), `false_positive_object` (2), `zone_end`, `no_entry`, `weight`, `no_speed_sign`, `zone`, `limit_end`, `no_stopping`, `no_overtaking`, `unreadable`. Read with DuckDB or `csv`, all as text.

## Selection rules (PRD §12)

- **Stage 1 — 5 signs:** 3 numeric labels with 3 different values, 1 zone sign (`negative_type == zone`, status certain), 1 cancelled speed sign (`cancelled_sign`).
- **Stage 2 — 20 more signs:** 13 numeric covering every numeric value not already covered by stage 1 at least once (there are 11 distinct numeric values 20–120; fill the remaining numeric slots with other values); 6 negatives: the other `cancelled_sign`, the `false_positive_object` **whose `mapillary_value` is a speed class** (`regulatory--maximum-speed-limit-N--…`), a `zone_end`, a `no_entry`, a `weight` (certain), a `no_speed_sign`; and 1 sign labelled `unreadable`.
- Across both stages: at most one sign per `sequence`; `status == uncertain` excluded; no sign in both lists. The 4 hard cases must be in the union: both `cancelled_sign`, the speed-class `false_positive_object`, and the `unreadable` sign (verify that their `mapillary_value` is a speed class; report if not).
- Deterministic: sort candidates by `photo_id` and take the first that fits; prefer Kraków signs where there is a choice, but do not require it.
- Output files: one `photo_id` per line, LF, a first comment line `# stage1: selected by reports/map-qa/select_samples.py from signs/labels.csv (PRD §12)` (the application ignores `#` comments). The script prints the selection with label, negative type, value and sequence, and writes the two files (overwrite only these two).

Run it from `app/`: `uv run --frozen python ../reports/map-qa/select_samples.py`.

## Test (`app/tests/unit/test_samples.py`)

Read the two files and the real labels/signs (use `DATA_DIR` from `tests/conftest.py`), and assert the criteria: sizes 5 and 20; disjoint; every id exists in `signs.csv`; no uncertain label; one sign per sequence across the union; stage-1 composition; stage-2 composition (13 numeric, 6 negatives with the listed types, 1 unreadable); the 4 hard cases present; the union covers every distinct numeric label value at least once.

Verification: `uv run --frozen pytest tests/unit/test_samples.py -q`.

In the Report, list the 25 selected ids with label, negative type, detector value and sequence.
