# T6 — Review CSV validation and merge

Read `.agent-briefs/_common.md` first and follow it. Also read ADR-004 §5 (`docs/ADR/004-mvp-file-and-cli-contract.md`), PRD `docs/PRD.md` §4.2, §9.3, AC-29…AC-31.

## Editable files (only these)

- `app/src/map_qa/review.py` (new)
- `app/tests/unit/test_review.py` (new)

Concurrent tasks (do not touch their files): T4 `osm_speed.py`, `compare.py`; T5 `sources/`, `inputs.py`; T7 `export.py`; T8 `config.py`.

## API

```python
def read_review(path: Path) -> list[ReviewRow]
def human_values(rows: list[ReviewRow]) -> dict[str, str]
def verdicts(rows: list[ReviewRow]) -> dict[str, str]
def machine_rows(results: list[SignResult]) -> list[ReviewRow]
def merge(existing: list[ReviewRow], current: list[ReviewRow]) -> list[ReviewRow]
def write_review(path: Path, rows: list[ReviewRow]) -> None
```

`ReviewRow`, `REVIEW_COLUMNS`, `ANSWER_SET`, `VERDICTS`, `SignResult`, `TIER_*`, `CANDIDATE_OUTCOMES`, `MapQaError` are in `models.py`.

- `read_review`: missing file → `[]`. UTF-8 (accept a UTF-8 BOM), csv module. Missing column → `MapQaError("review.csv: missing column '<col>'")`. Duplicate `row_id` → `MapQaError("review.csv row <n> (<row_id>): duplicate row_id")`. Validate each row: `human_value.strip()` must be empty or in `ANSWER_SET`; `verdict.strip()` empty or in `VERDICTS`; `row_type` in {`queue`, `candidate`}. First error → `MapQaError(f"review.csv row {n} ({row_id}): invalid {column} '{value}'")` where `n` is the 1-based data row number (header excluded). **Never write the file in this function.** Keep all values as read (do not strip what is stored), but compare stripped.
- `human_values`: `{photo_id: human_value.strip()}` from `queue` rows with a non-empty human value.
- `verdicts`: `{photo_id: verdict.strip()}` from `candidate` rows with a non-empty verdict.
- `machine_rows(results)`: one `queue` row for every result whose `reading.tier` is not `decision_model` or `llm` (no model-accepted value: includes human-tier and abstained signs); one `candidate` row for every result whose `comparison.outcome` is in `CANDIDATE_OUTCOMES`. Fields: `row_id = ReviewRow.make_id(photo_id, row_type)`, `osm_way_id` = str of `link.osm_way_id` or `""`, `tier1_answer` = `reading.tier1.answer` or `""`, `tier2_answer` likewise, `outcome` = comparison outcome; human fields empty.
- `merge(existing, current)` (AC-29): rows are never deleted. For a `row_id` in both: machine columns (`osm_way_id`, `tier1_answer`, `tier2_answer`, `outcome`) from `current`; `human_value`, `verdict`, `comment` copied unchanged from `existing`. Rows only in `existing`: kept unchanged. Rows only in `current`: added. Sort by `photo_id`, then `queue` before `candidate`.
- `write_review`: header `REVIEW_COLUMNS`, `lineterminator="\n"`, UTF-8 without BOM; write to a temporary file in the same folder, then `os.replace` (atomic).

## Tests

- round trip write → read gives equal rows;
- invalid verdict `maybe` in row 3 → MapQaError containing `row 3`, the row_id and `'maybe'`; file bytes unchanged;
- invalid human value `55km` → error; values `50`, ` 50 `, `none`, `unreadable`, empty → accepted;
- missing column; duplicate row_id; missing file → `[]`;
- merge keeps human value, verdict and comment byte for byte (including Polish characters and commas), refreshes outcome, keeps rows absent from current (sign removed from input), adds new rows, sorting;
- `machine_rows` with small `SignResult` objects built in the test: model-accepted sign → no queue row; human-tier sign → queue row; abstained sign → queue row; mismatch → candidate row too.

Verification: `uv run --frozen pytest tests/unit/test_review.py -q`.
