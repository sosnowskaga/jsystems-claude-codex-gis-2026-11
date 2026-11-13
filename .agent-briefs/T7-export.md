# T7 — Markdown and CSV export

Read `.agent-briefs/_common.md` first and follow it. Also read ADR-004 §4, §6, §7 carefully (`docs/ADR/004-mvp-file-and-cli-contract.md`), PRD `docs/PRD.md` §9.2, AC-03, AC-25…AC-28, and ADR-002 §4.

## Editable files (only these)

- `app/src/map_qa/export.py` (new)
- `app/tests/unit/test_export.py` (new)

Concurrent tasks (do not touch their files): T4 `osm_speed.py`, `compare.py`; T5 `sources/`, `inputs.py`; T6 `review.py`; T8 `config.py`.

## API

```python
RESULTS_COLUMNS: tuple[str, ...]   # exactly the 30 columns of ADR-004 §4, in order

@dataclass
class ReportContext:
    generated_at: str                    # ISO 8601 UTC
    app_version: str
    experiment_id: str
    model_mode: str
    threshold: float
    tiers: dict[str, TierConfig]         # keys "tier1", "tier2"
    tier_stats: dict[str, TierStats]     # keys "tier1", "tier2"
    sources: list[SourceMeta]
    osm_snapshot: str | None
    warnings: list[str]
    results: list[SignResult]
    out_dir: Path                        # folder of report.md, for relative links
    sample: list[str] | None = None
    input_hashes: dict[str, str] = field(default_factory=dict)
    answers: list[StageAnswer] = field(default_factory=list)
    sql_log: list[dict] = field(default_factory=list)
    evaluation_md: str = ""              # body of the evaluation section, filled by evaluate later

def outcome_counts(results: list[SignResult]) -> dict[str, int]        # all six OUTCOMES, zeros included
def summary_counts(results: list[SignResult]) -> dict                 # ADR-004 §6 "counts"
def result_row(result: SignResult, out_dir: Path) -> dict[str, str]
def write_results_csv(path: Path, ctx: ReportContext) -> None
def render_report(ctx: ReportContext) -> str
def replace_evaluation_section(report_text: str, evaluation_md: str) -> str
def run_payload(ctx: ReportContext) -> dict
def write_run_json(out_dir: Path, ctx: ReportContext) -> None          # writes run.json and experiments/<experiment_id>.json
def md_escape(text: str) -> str
```

Details:
- `result_row`: values per ADR-004 §4. `tier1_probability` with 4 decimals (`f"{p:.4f}"`) or `""`. `escalation_reasons` joined with `;`. Raw OSM tags from `result.tags` (empty when `None`). `crop_path` / `photo_path` relative to `out_dir` with `/` separators (`os.path.relpath`, then replace `\`). `verdict` from `result.verdict`. `tier1_model`/`tier2_model` from `reading.tier1.model` / `reading.tier2.model` if present, else from `ctx.tiers` — so give `result_row` an optional `tiers` argument, or compute in `write_results_csv`; your choice, keep the column contract.
- `summary_counts`: `{"outcomes": outcome_counts, "queue": signs with reading.value None, "candidates": mismatch+map_missing, "reviewed": candidates with non-empty verdict, "confirmed": verdict == "confirmed"}`.
- `write_results_csv`: rows sorted by `photo_id`, csv module, `lineterminator="\n"`, UTF-8 without BOM.
- `render_report`: the exact headings of ADR-004 §7 in that order. Line 2 is `Wygenerowano: <generated_at>`; **no other line may depend on time** (do not print `read_at`). `## Nagłówek`: a sources table (dataset | źródło | lokalizacja | wybrane przez), OSM snapshot date, model mode, experiment id, tier-1 and tier-2 model, threshold, request and cost limits and timeout per tier, `question_version` (`models.QUESTION_VERSION`), warnings as a list (or nothing), and the AC-28 sentence as its own paragraph: `Rozbieżność znaku i mapy nie oznacza automatycznie błędu mapy.` `## Podsumowanie`: an outcome table (all six codes), manual queue size, candidates, reviewed and confirmed counts, and a tier table (tier | model | wysłane | zaakceptowane | eskalowane | błędy | z cache | żądania | koszt USD | limit) from `tier_stats`. `## Kandydaci do weryfikacji`: one row per mismatch/map_missing: photo_id, crop link cell, value and tier, OSM way id, raw speed tags (`maxspeed=…; maxspeed:forward=…` only for present tags, plus the implicit tag), distance, review status (verdict or `do przeglądu`). `## Kolejka ręczna`: signs with `reading.value is None`: photo_id, crop link cell, tier-1 top answer and probability, tier-2 answer, escalation reasons, outcome. `## Nie da się porównać`: abstain and not_comparable signs: photo_id, outcome, reason. `## Ewaluacja`: `ctx.evaluation_md` or `0 pozycji` when empty. Every empty section body is exactly the line `0 pozycji`.
- Crop link cell (AC-27): `[wycinek](<crop_rel>) · [zdjęcie](<photo_rel>) — <creator>, <source>, <license>` with `source` = `sign.source` plus a link to `source_url`: use `[<sign.source>](<source_url>)`.
- `md_escape`: `|` → `\|`, `<` → `&lt;`, `` ` `` → `` \` ``, `\r`/`\n` → space. Apply to every text from data inside tables.
- Sort table rows by `photo_id`. Use the same `results` for every counter (one data structure).
- `replace_evaluation_section`: replace everything from the line `## Ewaluacja` to the end with `## Ewaluacja\n\n<evaluation_md or 0 pozycji>\n`.
- `run_payload`: exactly the keys of ADR-004 §6; `schema_version` 1; `question_version` and `accept_rule` from models.py; `tiers` from `TierConfig` fields; `tier_stats` from `TierStats` fields; `sources` from `SourceMeta` fields; `answers` from `StageAnswer` fields **without `raw_response`**; `counts` = `summary_counts`. `write_run_json`: `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False)`, UTF-8, and the same file copied to `<out_dir>/experiments/<experiment_id>.json` (create the folder).

## Tests (unit, synthetic SignResult objects built in the test)

- `RESULTS_COLUMNS` equals the ADR-004 list (copy it into the test literally);
- heading order; AC-28 sentence present; the only differing line between two renders with different `generated_at` is the `Wygenerowano:` line (AC-03);
- `0 pozycji` for empty candidates / queue / cannot-compare / evaluation;
- escaping: a creator `a|b<script>` and a raw tag with a backtick stay text and keep the table column count;
- relative links: crop under `<tmp>/data/signs/crops/x.jpg` and out dir `<tmp>/reports/run` → `../../data/signs/crops/x.jpg`; author, source link and license present;
- results.csv: header, one row per sign, sorted, probability formatting, empty for None;
- run.json: key set equals ADR-004 §6, no `raw_response`, no `data:image`, experiments copy exists and is identical;
- `replace_evaluation_section` replaces only the tail.

Verification: `uv run --frozen pytest tests/unit/test_export.py -q`.
