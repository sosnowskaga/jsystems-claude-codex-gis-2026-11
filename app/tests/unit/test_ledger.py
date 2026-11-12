"""Answer cache and limits ledger (ADR-001 §5, ADR-004 §8, AC-15, AC-16, TAC-001-04)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from map_qa.ledger import AnswerCache, Ledger
from map_qa.models import MapQaError, TierConfig

SHA = "a" * 64
TIERS = {
    "tier1": TierConfig("typesafe/jev-1.14", max_requests=2, max_cost_usd=0.0012, reservation_usd=0.0005),
    "tier2": TierConfig("openai/gpt-6-luna", max_requests=1, max_cost_usd=0.10, reservation_usd=0.004),
}


def test_cache_path_uses_image_hash_model_and_question_version(tmp_path: Path):
    cache = AnswerCache(tmp_path)
    path = cache.path("typesafe/jev-1.14", "b33-v1", SHA)
    assert path == tmp_path / "answers" / "typesafe__jev-1.14" / "b33-v1" / f"{SHA}.json"
    assert cache.path("openai/gpt-6-luna-decisions", "b33-v1", SHA) != path  # model is part of the key
    assert cache.path("typesafe/jev-1.14", "b33-v2", SHA) != path


def test_cache_get_put_round_trip_and_miss(tmp_path: Path):
    cache = AnswerCache(tmp_path)
    assert cache.get("m/x", "b33-v1", SHA) is None
    entry = {"http_status": 200, "response": {"k": "v"}, "latency_ms": 12}
    cache.put("m/x", "b33-v1", SHA, entry)
    stored = cache.get("m/x", "b33-v1", SHA)
    assert stored["response"] == {"k": "v"}
    assert stored["key"] == {"image_sha256": SHA, "model": "m/x", "question_version": "b33-v1"}


def test_cache_rejects_invalid_hash(tmp_path: Path):
    with pytest.raises(ValueError):
        AnswerCache(tmp_path).path("m/x", "b33-v1", "../../etc")


def test_attempts_are_counted_before_the_request_and_limit_stops_the_tier(tmp_path: Path):
    ledger = Ledger(tmp_path / "ledger.json", "exp", TIERS)
    assert ledger.blocked("tier1") is None
    ledger.reserve("tier1")
    ledger.settle("tier1", 0.0005, None)  # failed attempt: reservation stays (AC-15)
    ledger.reserve("tier1")
    assert ledger.attempts("tier1") == 2
    assert ledger.blocked("tier1") == "max_requests"


def test_cost_reservation_blocks_a_call_that_would_exceed_the_cost_limit(tmp_path: Path):
    ledger = Ledger(tmp_path / "ledger.json", "exp", {**TIERS, "tier1": TierConfig("t", 10, 0.0009, reservation_usd=0.0005)})
    ledger.reserve("tier1")
    ledger.settle("tier1", 0.0005, 0.0004)
    assert ledger.cost("tier1") == pytest.approx(0.0004)
    assert ledger.blocked("tier1") is None  # 0.0004 + 0.0005 = 0.0009 is not above the limit
    ledger.reserve("tier1")
    ledger.settle("tier1", 0.0005, 0.0001)
    assert ledger.blocked("tier1") == "max_cost_usd"


def test_actual_cost_above_reservation_disables_the_tier(tmp_path: Path):
    ledger = Ledger(tmp_path / "ledger.json", "exp", TIERS)
    ledger.reserve("tier2")
    exceeded = ledger.settle("tier2", 0.004, 0.009)
    assert exceeded is True
    assert ledger.cost("tier2") == pytest.approx(0.009)


def test_ledger_persists_per_experiment_and_sums_across_runs(tmp_path: Path):
    path = tmp_path / "ledger.json"
    first = Ledger(path, "exp", TIERS)
    first.reserve("tier1")
    first.settle("tier1", 0.0005, 0.0002)
    second = Ledger(path, "exp", TIERS)
    second.reserve("tier1")
    assert second.attempts("tier1") == 2  # TAC-001-04: not reset on resume
    other = Ledger(path, "other", TIERS)
    assert other.attempts("tier1") == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["experiments"]["exp"]["tier1"]["model"] == "typesafe/jev-1.14"
    assert data["experiments"]["exp"]["tier1"]["attempts"] == 2


def test_changing_the_model_of_an_experiment_is_rejected(tmp_path: Path):
    path = tmp_path / "ledger.json"
    Ledger(path, "exp", TIERS).reserve("tier1")
    other_model = {**TIERS, "tier1": TierConfig("openai/gpt-6-luna-decisions", 25, 0.05)}
    with pytest.raises(MapQaError, match="experiment 'exp'"):
        Ledger(path, "exp", other_model)
