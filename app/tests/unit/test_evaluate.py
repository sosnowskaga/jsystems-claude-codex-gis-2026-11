from __future__ import annotations

import csv
import json
from types import SimpleNamespace

from map_qa.evaluate import evaluation_markdown, rebuild_report


def _csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path, mode="live"):
    out = tmp_path / "out"
    data = tmp_path / "data"
    (out / "experiments").mkdir(parents=True)
    (data / "signs").mkdir(parents=True)
    answers = [
        {"photo_id": "a", "tier": 1, "model": "jev", "status": "answered", "answer": "50", "probabilities": {"50": .9}, "from_cache": False, "cost_usd": .01, "latency_ms": 10},
        {"photo_id": "b", "tier": 1, "model": "jev", "status": "answered", "answer": "none", "probabilities": {"none": .7}, "from_cache": True, "cost_usd": 0, "latency_ms": 20},
        {"photo_id": "c", "tier": 2, "model": "llm", "status": "answered", "answer": "30", "probabilities": None, "from_cache": False, "cost_usd": .02, "latency_ms": 40},
        {"photo_id": "d", "tier": 1, "model": "jev", "status": "model_unavailable", "answer": None, "probabilities": None, "from_cache": False, "cost_usd": 0, "latency_ms": 60},
    ]
    run = {"model_mode": mode, "threshold": .8, "tiers": {"tier1": {"model": "jev"}}, "tier_stats": {"tier1": {"sent": 3, "accepted": 1, "escalated": 2, "failed": 1, "requests": 2, "cost_usd": .01}, "tier2": {"sent": 1, "accepted": 1, "escalated": 0, "failed": 0, "requests": 1, "cost_usd": .02}}, "answers": answers, "sample": ["a", "b", "c", "d"], "experiment_id": "default"}
    (out / "run.json").write_text(json.dumps(run), encoding="utf-8")
    _csv(out / "results.csv", ["photo_id", "value", "tier", "outcome", "escalation_reasons"], [
        {"photo_id": "a", "value": "50", "tier": "decision_model", "outcome": "match", "escalation_reasons": ""},
        {"photo_id": "b", "value": "", "tier": "", "outcome": "pending_human", "escalation_reasons": "tier1:below_threshold"},
        {"photo_id": "c", "value": "30", "tier": "llm", "outcome": "match", "escalation_reasons": ""},
        {"photo_id": "d", "value": "", "tier": "", "outcome": "pending_human", "escalation_reasons": "tier1:model_unavailable:timeout"},
    ])
    for name, model, latency in (("a", "jev", 10), ("b", "luna", 30)):
        exp = {**run, "experiment_id": name, "tiers": {"tier1": {"model": model}}, "answers": [dict(answers[0], model=model, latency_ms=latency)]}
        (out / "experiments" / f"{name}.json").write_text(json.dumps(exp), encoding="utf-8")
    _csv(data / "signs" / "labels.csv", ["photo_id", "sign_value", "status", "method", "reviewers", "note", "negative_type"], [
        {"photo_id": "a", "sign_value": "50", "status": "certain"},
        {"photo_id": "b", "sign_value": "none", "status": "certain"},
        {"photo_id": "c", "sign_value": "30", "status": "uncertain"},
        {"photo_id": "d", "sign_value": "unreadable", "status": "certain"},
    ])
    _csv(data / "signs" / "signs.csv", ["photo_id", "mapillary_value"], [
        {"photo_id": "a", "mapillary_value": "regulatory--maximum-speed-limit-50--g1"},
        {"photo_id": "b", "mapillary_value": "regulatory--end-of-maximum-speed-limit-30--g1"},
        {"photo_id": "c", "mapillary_value": ""},
        {"photo_id": "d", "mapillary_value": ""},
    ])
    return out, SimpleNamespace(data_dir=data)


def test_evaluation_reports_denominators_exclusions_baseline_and_exploration(tmp_path):
    out, settings = _fixture(tmp_path)
    md = evaluation_markdown(out, settings)
    assert "### Zbiór ewaluacyjny" in md
    assert "Wykluczone etykiety:" in md
    assert "c" in md and "d" in md
    assert "detektor Mapillary (bazowy)" in md
    assert "regulatory--end-of" not in md
    assert "0.50" in md and "0.95" in md
    assert "nie należy raportować jako trafności" in md
    assert "1/1" in md and "0/0" in md
    assert "| detektor Mapillary (bazowy) | 4/4 | 0/4 | 4/4 | 0/4 | 1/1 | 0/2 | 1/1 |" in md


def test_off_mode_and_rebuild_report_replaces_only_evaluation(tmp_path):
    out, settings = _fixture(tmp_path, "off")
    md = evaluation_markdown(out, settings)
    assert "Nie dotyczy w trybie off." in md
    assert "| łańcuch | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |" in md
    report = out / "report.md"
    report.write_text("# report\n## Podsumowanie\nkeep\n## Ewaluacja\nold\n", encoding="utf-8")
    rebuild_report(out, settings)
    actual = report.read_text(encoding="utf-8")
    assert actual.startswith("# report\n## Podsumowanie\nkeep\n## Ewaluacja\n")
    assert "old" not in actual


def test_ac45_has_two_experiments_and_latency_summary(tmp_path):
    out, settings = _fixture(tmp_path)
    md = evaluation_markdown(out, settings)
    section = md.split("### Porównanie modeli decyzyjnych", 1)[1]
    assert "| a | jev |" in section
    assert "| b | luna |" in section
    assert "10" in section and "30" in section


def _row(md: str, title: str) -> list[str]:
    line = next(l for l in md.splitlines() if l.startswith(f"| {title} |"))
    return [c.strip() for c in line.strip("|").split("|")]


def test_experiment_without_accepted_answers_does_not_crash(tmp_path):
    out, settings = _fixture(tmp_path)
    low = {"experiment_id": "low", "threshold": 0.8, "tiers": {"tier1": {"model": "luna"}}, "answers": [
        {"photo_id": "a", "tier": 1, "model": "luna", "status": "answered", "answer": "50", "probabilities": {"50": 0.3}, "latency_ms": 5}]}
    (out / "experiments" / "low.json").write_text(json.dumps(low), encoding="utf-8")
    row = _row(evaluation_markdown(out, settings), "low")
    assert row[3] == "0/1" and row[-1] == "—"


def test_uncertain_label_is_not_a_false_acceptance(tmp_path):
    """c is accepted by tier 2 with value 30 but its label is uncertain: excluded from numerator and denominator."""
    out, settings = _fixture(tmp_path)
    row = _row(evaluation_markdown(out, settings), "poziom 2")
    assert row[6] == "0/0"


def test_chain_counts_signs_not_answers(tmp_path):
    out, settings = _fixture(tmp_path)
    answers = json.loads((out / "run.json").read_text(encoding="utf-8"))
    # b escalated from tier 1 to tier 2, which answered none: one sign, two answers
    answers["answers"].append({"photo_id": "b", "tier": 2, "model": "llm", "status": "answered", "answer": "unreadable"})
    (out / "run.json").write_text(json.dumps(answers), encoding="utf-8")
    row = _row(evaluation_markdown(out, settings), "łańcuch")
    assert row[1] == "4/4"  # signs sent to the chain
    assert row[7] == "1/1"  # negative b handled (escalated to the queue), counted once


def test_ac45_latency_includes_answers_replayed_from_cache(tmp_path):
    """A cached answer keeps the latency measured when it was requested (ADR-004 §8), so stage 3 keeps AC-45 latency."""
    out, settings = _fixture(tmp_path)
    cached = {"experiment_id": "c", "threshold": 0.8, "tiers": {"tier1": {"model": "jev"}}, "answers": [
        {"photo_id": "a", "tier": 1, "model": "jev", "status": "answered", "answer": "50", "probabilities": {"50": 0.9},
         "from_cache": True, "latency_ms": 250}]}
    (out / "experiments" / "c.json").write_text(json.dumps(cached), encoding="utf-8")
    row = _row(evaluation_markdown(out, settings), "c")
    assert row[9] == "250" and row[10] == "250"


def test_ac45_uses_labels_of_signs_outside_the_last_run(tmp_path):
    """Review finding: a kept experiment is scored on its own signs, not on the last results.csv."""
    out, settings = _fixture(tmp_path)
    with (settings.data_dir / "signs" / "labels.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write("e,70,certain,,,,\n")
    old = {"experiment_id": "old", "threshold": 0.8, "tiers": {"tier1": {"model": "jev"}}, "answers": [
        {"photo_id": "e", "tier": 1, "model": "jev", "status": "answered", "answer": "70", "probabilities": {"70": 0.9}, "latency_ms": 5}]}
    (out / "experiments" / "old.json").write_text(json.dumps(old), encoding="utf-8")
    row = _row(evaluation_markdown(out, settings), "old")
    assert row[4] == "1/1" and row[5] == "0/1"
