from __future__ import annotations

import csv
import json
from pathlib import Path

from map_qa.export import (
    RESULTS_COLUMNS,
    ReportContext,
    md_escape,
    outcome_counts,
    render_report,
    replace_evaluation_section,
    result_row,
    run_payload,
    summary_counts,
    write_results_csv,
    write_run_json,
)
from map_qa.models import (
    ACCEPT_RULE, QUESTION_VERSION, Comparison, OsmSpeedTags, RoadLink, Sign,
    SignReading, SignResult, SourceMeta, StageAnswer, TierConfig, TierStats,
)

EXPECTED_COLUMNS = ("photo_id", "area", "sequence", "outcome", "abstain_reason", "not_comparable_reason", "value", "tier", "model", "tier1_model", "tier1_answer", "tier1_probability", "tier2_model", "tier2_answer", "escalation_reasons", "osm_way_id", "distance_m", "match_flags", "osm_maxspeed", "osm_maxspeed_forward", "osm_maxspeed_backward", "osm_maxspeed_conditional", "osm_implicit_tag", "osm_implicit_value", "verdict", "crop_path", "photo_path", "creator", "license", "source_url")


def make_context(tmp_path: Path, generated_at: str = "2026-01-01T00:00:00Z") -> ReportContext:
    sign = Sign("p1", "Mapillary", "Kraków", "s1", str(tmp_path / "data/signs/crops/x.jpg"), str(tmp_path / "data/photo.jpg"), "hash", "a|b<script>", "CC-BY", "https://example.test/photo", None)
    reading = SignReading("p1", "50", "decision_model", "m1", StageAnswer("p1", 1, "m1", QUESTION_VERSION, "hash", "answered", answer="50", probabilities={"50": .87555}))
    result = SignResult(sign, RoadLink("p1", 12, "2.3", (), 1), OsmSpeedTags(12, maxspeed="30`x"), reading, Comparison("p1", "mismatch"), None)
    tiers = {"tier1": TierConfig("m1", 5, 1.2), "tier2": TierConfig("m2", 3, 2.0)}
    return ReportContext(generated_at, "1.0", "exp", "cache", .8, tiers, {"tier1": TierStats(), "tier2": TierStats()}, [SourceMeta("signs", "local", "data", "default")], "2026-01-01", [], [result], tmp_path / "reports/run")


def test_column_contract_and_result_csv(tmp_path: Path) -> None:
    assert RESULTS_COLUMNS == EXPECTED_COLUMNS
    ctx = make_context(tmp_path)
    row = result_row(ctx.results[0], ctx.out_dir, ctx.tiers)
    assert row["crop_path"] == "../../data/signs/crops/x.jpg"
    assert row["tier1_probability"] == "0.8756"
    ctx.results.append(ctx.results[0])
    ctx.results[1] = SignResult(**{**ctx.results[0].__dict__, "sign": Sign(**{**ctx.results[0].sign.__dict__, "photo_id": "p0"})})
    path = tmp_path / "results.csv"
    write_results_csv(path, ctx)
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == list(EXPECTED_COLUMNS)
    assert [r["photo_id"] for r in rows] == ["p0", "p1"]


def test_report_sections_empty_and_timestamp_only(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    first = render_report(ctx)
    assert "Rozbieżność znaku i mapy nie oznacza automatycznie błędu mapy." in first
    assert [line for line in first.splitlines() if line.startswith("## ")] == ["## Nagłówek", "## Podsumowanie", "## Kandydaci do weryfikacji", "## Kolejka ręczna", "## Nie da się porównać", "## Ewaluacja"]
    ctx.generated_at = "2027-01-01T00:00:00Z"
    second = render_report(ctx)
    assert [(a, b) for a, b in zip(first.splitlines(), second.splitlines()) if a != b] == [("Wygenerowano: 2026-01-01T00:00:00Z", "Wygenerowano: 2027-01-01T00:00:00Z")]
    assert first.count("0 pozycji") >= 3
    assert "a\\|b&lt;script>" in first
    assert "[Mapillary](https://example.test/photo)" in first
    assert "30\\`x" in first


def test_counts_and_evaluation_tail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert outcome_counts(ctx.results) == {"match": 0, "mismatch": 1, "map_missing": 0, "not_comparable": 0, "abstain": 0, "pending_human": 0}
    assert summary_counts(ctx.results) == {"outcomes": outcome_counts(ctx.results), "queue": 0, "candidates": 1, "reviewed": 0, "confirmed": 0}
    text = "# x\n## Ewaluacja\n\nold\n"
    assert replace_evaluation_section(text, "new") == "# x\n## Ewaluacja\n\nnew\n"
    assert md_escape("a|b<script>`\r\nx") == "a\\|b&lt;script>\\`  x"


def test_run_json_contract_and_copy(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    ctx.answers = [StageAnswer("p1", 1, "m1", QUESTION_VERSION, "hash", "answered", answer="50", raw_response="data:image/secret")]
    payload = run_payload(ctx)
    assert set(payload) == {"schema_version", "generated_at", "app_version", "experiment_id", "model_mode", "question_version", "accept_rule", "threshold", "tiers", "sample", "sources", "warnings", "input_hashes", "counts", "tier_stats", "answers", "sql_log"}
    assert payload["schema_version"] == 1
    assert payload["question_version"] == QUESTION_VERSION and payload["accept_rule"] == ACCEPT_RULE
    assert "raw_response" not in json.dumps(payload) and "data:image" not in json.dumps(payload)
    write_run_json(ctx.out_dir, ctx)
    assert (ctx.out_dir / "run.json").read_bytes() == (ctx.out_dir / "experiments/exp.json").read_bytes()


def _candidate_row(report: str) -> str:
    section = report[report.index("## Kandydaci do weryfikacji"):report.index("## Kolejka ręczna")]
    return next(line for line in section.splitlines() if line.startswith("| p1 "))


def test_candidate_row_escapes_data_and_keeps_column_count(tmp_path: Path) -> None:
    report = render_report(make_context(tmp_path))
    row = _candidate_row(report)
    assert "a\|b&lt;script>" in row
    assert "<script>" not in report
    assert "maxspeed=30\`x" in row
    header = report[report.index("## Kandydaci do weryfikacji"):].splitlines()[2]
    unescaped_pipes = lambda line: line.replace("\|", "").count("|")
    assert unescaped_pipes(row) == unescaped_pipes(header)


def test_crop_links_are_relative_with_attribution(tmp_path: Path) -> None:
    """AC-27: crop and full photo by relative path, with author, source and licence."""
    row = _candidate_row(render_report(make_context(tmp_path)))
    assert "[wycinek](<../../data/signs/crops/x.jpg>)" in row
    assert "[zdjęcie](<../../data/photo.jpg>)" in row
    assert "[Mapillary](https://example.test/photo)" in row and "CC-BY" in row
    csv_row = result_row(make_context(tmp_path).results[0], tmp_path / "reports/run")
    assert csv_row["crop_path"] == "../../data/signs/crops/x.jpg"


def test_header_is_polish_with_disclaimer_paragraph(tmp_path: Path) -> None:
    report = render_report(make_context(tmp_path))
    header = report[report.index("## Nagłówek"):report.index("## Podsumowanie")]
    for label in ("Migawka OSM", "Tryb modeli", "Eksperyment", "Próg pewności", "Limit żądań", "Wersja pytania"):
        assert label in header
    assert "\n\nRozbieżność znaku i mapy nie oznacza automatycznie błędu mapy.\n\n" in header


def test_written_files_use_lf(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_run_json(ctx.out_dir, ctx)
    assert b"\r\n" not in (ctx.out_dir / "run.json").read_bytes()
