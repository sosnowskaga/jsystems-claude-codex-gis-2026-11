"""Stage-0 acceptance test: real CLI, real input files, model mode ``off``, no network.

Expected outcomes are precomputed from ``signs/nearest_osm.csv`` and ``osm_roads.parquet``
(see tests/fixtures/review_round.csv, column ``osm_basis``), never from labels (TAC-000-05):
61 signs have a trusted link (Kraków, no match flag, road found), 58 are abstained.

Covers AC-01, AC-03 (mode off), AC-05, AC-06, AC-17, AC-24, AC-25, AC-26, AC-28, AC-29, AC-30, AC-31.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from conftest import DATA_DIR, FIXTURES, run_cli

pytestmark = pytest.mark.xfail(strict=True, reason="pipeline not implemented")

TRUSTED = 61
ABSTAINED = 58
HEADINGS = [
    "# Raport Map QA: znaki ograniczenia prędkości",
    "## Nagłówek",
    "## Podsumowanie",
    "## Kandydaci do weryfikacji",
    "## Kolejka ręczna",
    "## Nie da się porównać",
    "## Ewaluacja",
]
DISCLAIMER = "Rozbieżność znaku i mapy nie oznacza automatycznie błędu mapy."


def _hash_inputs() -> dict[str, str]:
    return {
        str(p.relative_to(DATA_DIR)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DATA_DIR.rglob("*"))
        if p.is_file()
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _without_timestamp(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("Wygenerowano:"))


def _run(out: Path, env: dict[str, str], *extra: str):
    return run_cli(["run", "--out", str(out), "--cache-dir", str(out / "cache"), *extra], env=env)


def test_stage0_off_run_and_review_round(tmp_path: Path, no_network_env: dict[str, str]):
    before = _hash_inputs()
    out = tmp_path / "run"

    # --- Run 1: off mode, no review file yet ---------------------------------------------------
    first = _run(out, no_network_env)
    assert first.returncode == 0, first.stderr
    for name in ("report.md", "results.csv", "review.csv", "run.json"):
        assert (out / name).is_file(), name

    results = _read_csv(out / "results.csv")
    assert len(results) == 119
    assert len({r["photo_id"] for r in results}) == 119
    outcomes = [r["outcome"] for r in results]
    assert outcomes.count("abstain") == ABSTAINED
    assert outcomes.count("pending_human") == TRUSTED
    assert all(r["tier"] == "" and r["value"] == "" for r in results)
    assert all("tier1:mode_off" in r["escalation_reasons"] for r in results)

    review = _read_csv(out / "review.csv")
    assert list(review[0].keys()) == [
        "row_id", "row_type", "photo_id", "osm_way_id", "tier1_answer", "tier2_answer",
        "human_value", "outcome", "verdict", "comment",
    ]
    assert len(review) == 119 and {r["row_type"] for r in review} == {"queue"}

    run_json = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert run_json["model_mode"] == "off"
    assert run_json["counts"]["outcomes"]["pending_human"] == TRUSTED
    assert run_json["tier_stats"]["tier1"]["requests"] == 0
    assert run_json["tier_stats"]["tier2"]["requests"] == 0
    assert not (out / "cache" / "answers").exists()

    report = (out / "report.md").read_text(encoding="utf-8")
    positions = [report.index(h) for h in HEADINGS]
    assert positions == sorted(positions)
    assert DISCLAIMER in report
    assert "data:image" not in report

    # --- Round 2: analyst enters values ------------------------------------------------------
    expected = _read_csv(FIXTURES / "review_round.csv")
    by_id = {r["photo_id"]: r for r in expected}
    for row in review:
        if row["photo_id"] in by_id:
            row["human_value"] = by_id[row["photo_id"]]["human_value"]
            row["comment"] = "odczyt ręczny"
    _write_csv(out / "review.csv", review)

    second = _run(out, no_network_env)
    assert second.returncode == 0, second.stderr
    results = {r["photo_id"]: r for r in _read_csv(out / "results.csv")}
    for photo_id, exp in by_id.items():
        row = results[photo_id]
        assert row["outcome"] == exp["expected_outcome"], photo_id
        reason = row["abstain_reason"] if exp["expected_outcome"] == "abstain" else row["not_comparable_reason"]
        assert reason == exp["expected_reason"], photo_id
        assert row["tier"] == "human" and row["value"] == exp["human_value"]
    counts = [r["outcome"] for r in results.values()]
    assert counts.count("match") == 1
    assert counts.count("mismatch") == 1
    assert counts.count("map_missing") == 1
    assert counts.count("not_comparable") == 6
    assert counts.count("abstain") == ABSTAINED
    assert counts.count("pending_human") == TRUSTED - 9

    review2 = _read_csv(out / "review.csv")
    queue = {r["photo_id"]: r for r in review2 if r["row_type"] == "queue"}
    assert len(queue) == 119  # queue rows are never deleted (AC-29)
    for photo_id, exp in by_id.items():
        assert queue[photo_id]["human_value"] == exp["human_value"]
        assert queue[photo_id]["comment"] == "odczyt ręczny"
    candidates = {r["photo_id"]: r for r in review2 if r["row_type"] == "candidate"}
    assert set(candidates) == {"mapillary_1035052394661534", "mapillary_224324516912032"}

    # --- Round 3: verdict on a candidate ----------------------------------------------------
    for row in review2:
        if row["row_id"] == "mapillary_1035052394661534:candidate":
            row["verdict"] = "confirmed"
            row["comment"] = "znak dotyczy innej jezdni?"
    _write_csv(out / "review.csv", review2)
    third = _run(out, no_network_env)
    assert third.returncode == 0, third.stderr
    review3 = {r["row_id"]: r for r in _read_csv(out / "review.csv")}
    assert review3["mapillary_1035052394661534:candidate"]["verdict"] == "confirmed"
    assert review3["mapillary_1035052394661534:candidate"]["comment"] == "znak dotyczy innej jezdni?"
    run_json = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert run_json["counts"]["confirmed"] == 1
    report3 = (out / "report.md").read_text(encoding="utf-8")

    # --- AC-03 in mode off: an identical re-run gives an identical report ---------------------
    fourth = _run(out, no_network_env)
    assert fourth.returncode == 0, fourth.stderr
    report4 = (out / "report.md").read_text(encoding="utf-8")
    assert _without_timestamp(report3) == _without_timestamp(report4)

    # --- AC-31: invalid verdict stops the run and leaves the file unchanged ---------------------
    review_bytes = (out / "review.csv").read_bytes()
    bad = _read_csv(out / "review.csv")
    bad[0]["verdict"] = "maybe"
    _write_csv(out / "review.csv", bad)
    bad_bytes = (out / "review.csv").read_bytes()
    invalid = _run(out, no_network_env)
    assert invalid.returncode == 2
    assert "maybe" in invalid.stderr and bad[0]["row_id"] in invalid.stderr
    assert (out / "review.csv").read_bytes() == bad_bytes
    assert review_bytes != bad_bytes

    # --- AC-01: inputs unchanged ---------------------------------------------------------------
    assert _hash_inputs() == before


def test_missing_input_column_exits_2_without_report(tmp_path: Path, no_network_env: dict[str, str]):
    """AC-02: a missing column stops the run, names dataset and column, and writes no report."""
    data = tmp_path / "data"
    (data / "signs").mkdir(parents=True)
    for name in ("signs.csv", "nearest_osm.csv"):
        (data / "signs" / name).write_bytes((DATA_DIR / "signs" / name).read_bytes())
    (data / "sources.json").write_bytes((DATA_DIR / "sources.json").read_bytes())
    (data / "osm_roads.parquet").write_bytes((DATA_DIR / "osm_roads.parquet").read_bytes())
    rows = _read_csv(data / "signs" / "nearest_osm.csv")
    for row in rows:
        del row["match_flags"]
    _write_csv(data / "signs" / "nearest_osm.csv", rows)
    env = dict(no_network_env, MAPQA_DATA_DIR=str(data))
    out = tmp_path / "run"
    result = _run(out, env)
    assert result.returncode == 2
    assert "nearest_osm" in result.stderr and "match_flags" in result.stderr
    assert not (out / "report.md").exists()


def test_dry_run_reads_no_data(tmp_path: Path, no_network_env: dict[str, str]):
    """AC-44: dry-run prints sources and max calls, exits 0, reads no data."""
    env = dict(no_network_env, MAPQA_DATA_DIR=str(tmp_path / "does-not-exist"))
    result = run_cli(["run", "--dry-run", "--out", str(tmp_path / "run")], env=env)
    assert result.returncode == 0, result.stderr
    assert "osm_roads" in result.stdout and "local" in result.stdout
    assert "25" in result.stdout and "10" in result.stdout
    assert not (tmp_path / "run" / "report.md").exists()
