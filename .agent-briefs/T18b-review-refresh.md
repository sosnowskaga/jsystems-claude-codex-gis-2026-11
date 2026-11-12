# T18b — Refresh machine columns of kept review rows (review finding P2)

Read `.agent-briefs/_common.md` first and follow it. Then read ADR-004 §5 (`docs/ADR/004-mvp-file-and-cli-contract.md`) and `app/src/map_qa/review.py`, `app/tests/unit/test_review.py`.

## Editable files (only these)

- `app/src/map_qa/review.py`
- `app/tests/unit/test_review.py`

Concurrent work: the orchestrator edits `inputs.py`, `pipeline.py`, `evaluate.py`. Do not touch them.

## Defect (from an independent code review)

ADR-004 §5: "For an existing row the machine columns (`osm_way_id`, `tier1_answer`, `tier2_answer`, `outcome`) are refreshed when the sign is in the current run." Today `merge(existing, current)` refreshes only rows whose `row_id` is also in `current`. Example: a sign was in the queue (`photo:queue`, outcome `pending_human`); in the next run a model accepts its value, so `machine_rows()` no longer emits a queue row for it, and the kept queue row still says `pending_human` with old model answers. Likewise a former `photo:candidate` row whose sign is now `match` keeps outcome `mismatch`.

## Change

- Add a function `machine_values(results: list[SignResult]) -> dict[str, dict[str, str]]` returning, per `photo_id` in the run, the four machine columns (same values `machine_rows` uses).
- Change `merge` to `merge(existing, current, refresh: dict[str, dict[str, str]] | None = None)`: when `refresh` is given, every existing row whose `photo_id` is in `refresh` gets its four machine columns from it (human columns `human_value`, `verdict`, `comment` unchanged byte for byte); rows for signs not in the run stay unchanged; rows are never deleted; new rows are added as today; sorting unchanged. Without `refresh`, behaviour is exactly as today (backward compatible).
- Make `machine_rows` reuse `machine_values` so the values cannot diverge.

## Tests (TDD: write them first, see them fail)

- kept queue row of a sign now accepted by a model: outcome refreshed to the new outcome and tier answers refreshed; human columns unchanged; row not deleted;
- kept candidate row of a sign now `match`: outcome `match`, verdict and comment unchanged;
- row of a sign not in the run: unchanged;
- `merge` without `refresh` behaves as before (existing tests stay green).

Verification: `uv run --frozen pytest tests/unit/test_review.py -q`. The orchestrator will pass `refresh=machine_values(results)` from `pipeline.py`.
