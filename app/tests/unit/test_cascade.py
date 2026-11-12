"""Cascade rules on parsed answers (ADR-001 §4 accept-v1, §5 limits; AC-08…AC-12, AC-15, AC-16).

The backends here return ParsedAnswer objects directly: these tests check the decision logic,
not any HTTP request or response shape (those come from captures, T15/T16).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from PIL import Image

from map_qa.cascade import CallOutcome, Cascade, ParsedAnswer
from map_qa.image import model_image
from map_qa.ledger import AnswerCache, Ledger
from map_qa.models import Sign, TierConfig

SHA = "b" * 64


def sign(tmp_path: Path, photo_id: str = "p1", sha: str = SHA) -> Sign:
    crop = tmp_path / f"{photo_id}.jpg"
    shade = sum(photo_id.encode()) % 200  # a different image per sign, so cache keys differ
    Image.new("RGB", (60, 60), (shade, 30, 30)).save(crop, "JPEG")
    return Sign(photo_id, "Mapillary", "Kraków", "s", str(crop), str(crop), sha, "a", "CC", "u", None)


class FakeBackend:
    def __init__(self, model: str, answers: list, errors: list | None = None):
        self.model = model
        self.answers = list(answers)
        self.errors = list(errors or [])
        self.calls: list[tuple[bytes, str]] = []

    def parse(self, entry):
        return entry["parsed"]

    def call(self, image: bytes, mime_type: str) -> CallOutcome:
        self.calls.append((image, mime_type))
        if self.errors:
            return CallOutcome(entry=None, error_category=self.errors.pop(0), latency_ms=5)
        return CallOutcome(entry={"parsed": self.answers.pop(0), "http_status": 200}, error_category=None, latency_ms=7)


class MemoryCache(AnswerCache):
    """Stores entries in memory so ParsedAnswer objects need no JSON form."""

    def __init__(self):
        self.store = {}

    def get(self, model, qv, sha):
        return self.store.get((model, qv, sha))

    def put(self, model, qv, sha, entry):
        self.store[(model, qv, sha)] = entry


def tiers(max1=25, max2=10, cost1=0.05, cost2=0.10):
    return {
        "tier1": TierConfig("typesafe/jev-1.14", max1, cost1, reservation_usd=0.0005),
        "tier2": TierConfig("openai/gpt-6-luna", max2, cost2, reservation_usd=0.004),
    }


def make(tmp_path, t1, t2, t=None, mode="live", cache=None):
    t = t or tiers()
    return Cascade(
        mode=mode, threshold=0.80, tiers=t, cache=cache if cache is not None else MemoryCache(),
        backends={"tier1": t1, "tier2": t2},
        ledger_factory=lambda: Ledger(tmp_path / "ledger.json", "exp", t),
    )


@pytest.mark.parametrize(
    "answer, probs, accepted",
    [
        ("50", {"50": 0.80, "60": 0.20}, True),  # exactly the threshold
        ("50", {"50": 0.79, "60": 0.21}, False),
        ("none", {"none": 0.95, "50": 0.05}, True),
        ("unreadable", {"unreadable": 0.95, "50": 0.05}, False),
        ("50", None, False),  # missing probabilities is not replaced by confidence
    ],
)
def test_tier1_acceptance_rule(tmp_path, answer, probs, accepted):
    t1 = FakeBackend("typesafe/jev-1.14", [ParsedAnswer(answer, probs, 0.0001)])
    t2 = FakeBackend("openai/gpt-6-luna", [ParsedAnswer("50", None, 0.001)])
    reading = make(tmp_path, t1, t2).run([sign(tmp_path)], {}).readings["p1"]
    if accepted:
        assert reading.tier == "decision_model" and reading.value == answer
        assert t2.calls == []  # AC-08: no tier-2 call
    else:
        assert reading.tier == "llm" and len(t2.calls) == 1  # AC-09: exactly one tier-2 call


def test_tier1_failure_escalates_once_and_records_model_unavailable(tmp_path):
    t1 = FakeBackend("typesafe/jev-1.14", [], errors=["rate_limit"])
    t2 = FakeBackend("openai/gpt-6-luna", [ParsedAnswer("none", None, 0.001)])
    result = make(tmp_path, t1, t2).run([sign(tmp_path)], {})
    reading = result.readings["p1"]
    assert reading.tier1.status == "model_unavailable" and reading.tier1.error_category == "rate_limit"
    assert reading.tier1.answer is None  # AC-10: never recorded as a value, none or unreadable
    assert reading.escalation_reasons == ("tier1:model_unavailable:rate_limit",)
    assert reading.value == "none" and reading.tier == "llm"
    assert len(t1.calls) == 1 and len(t2.calls) == 1  # TAC-001-01: no retry
    assert result.tier_stats["tier1"].requests == 1 and result.tier_stats["tier1"].failed == 1


@pytest.mark.parametrize("answer, reason", [("unreadable", "tier2:unreadable"), ("57", "tier2:out_of_set"), (None, "tier2:out_of_set")])
def test_tier2_unreadable_or_out_of_set_goes_to_the_queue(tmp_path, answer, reason):
    t1 = FakeBackend("typesafe/jev-1.14", [ParsedAnswer("50", {"50": 0.5}, 0.0001)])
    t2 = FakeBackend("openai/gpt-6-luna", [ParsedAnswer(answer, None, 0.001)])
    reading = make(tmp_path, t1, t2).run([sign(tmp_path)], {}).readings["p1"]
    assert reading.value is None and reading.tier is None
    assert reading.escalation_reasons == ("tier1:below_threshold", reason)


def test_tier2_receives_only_the_image(tmp_path):
    """AC-12/AC-13: the backend interface carries only image bytes and MIME type."""
    s = sign(tmp_path)
    t1 = FakeBackend("typesafe/jev-1.14", [ParsedAnswer("50", {"50": 0.5}, 0.0001)])
    t2 = FakeBackend("openai/gpt-6-luna", [ParsedAnswer("50", None, 0.001)])
    make(tmp_path, t1, t2).run([s], {})
    image, mime, _ = model_image(s.crop_path)
    assert t2.calls == [(image, mime)] and mime == "image/png"


def test_request_limit_skips_the_tier_for_later_signs(tmp_path):
    t1 = FakeBackend("typesafe/jev-1.14", [ParsedAnswer("50", {"50": 0.95}, 0.0001)])
    t2 = FakeBackend("openai/gpt-6-luna", [ParsedAnswer("60", None, 0.001)])
    signs = [sign(tmp_path, "p1", "1" * 64), sign(tmp_path, "p2", "2" * 64)]
    result = make(tmp_path, t1, t2, t=tiers(max1=1)).run(signs, {})
    assert result.readings["p1"].tier == "decision_model"
    p2 = result.readings["p2"]
    assert p2.tier1.error_category == "limit_reached" and p2.tier == "llm"
    stats = result.tier_stats["tier1"]
    assert stats.limit_reached == "max_requests" and stats.signs_affected_by_limit == 1 and stats.requests == 1


def test_cost_limit_blocks_before_sending(tmp_path):
    t1 = FakeBackend("typesafe/jev-1.14", [])
    t2 = FakeBackend("openai/gpt-6-luna", [])
    result = make(tmp_path, t1, t2, t=tiers(cost1=0.0004, max2=0)).run([sign(tmp_path)], {})
    assert t1.calls == [] and t2.calls == []
    assert result.readings["p1"].tier1.error_category == "cost_limit"
    assert result.readings["p1"].escalation_reasons == ("tier1:model_unavailable:cost_limit", "tier2:model_unavailable:limit_reached")


def test_live_answer_is_cached_and_reused_without_a_call(tmp_path):
    cache = MemoryCache()
    t1 = FakeBackend("typesafe/jev-1.14", [ParsedAnswer("50", {"50": 0.95}, 0.0001)])
    t2 = FakeBackend("openai/gpt-6-luna", [])
    make(tmp_path, t1, t2, cache=cache).run([sign(tmp_path)], {})
    again = make(tmp_path, t1, t2, mode="cache", cache=cache).run([sign(tmp_path)], {})
    assert len(t1.calls) == 1  # AC-16: second run reuses the cached answer
    assert again.readings["p1"].value == "50" and again.readings["p1"].tier1.from_cache
    assert again.tier_stats["tier1"].cache_hits == 1 and again.tier_stats["tier1"].requests == 0


def test_human_value_is_never_sent(tmp_path):
    t1 = FakeBackend("typesafe/jev-1.14", [])
    t2 = FakeBackend("openai/gpt-6-luna", [])
    reading = make(tmp_path, t1, t2).run([sign(tmp_path)], {"p1": "70"}).readings["p1"]
    assert reading.tier == "human" and t1.calls == [] and t2.calls == []  # AC-30


def test_failed_attempts_count_against_the_ledger(tmp_path):
    t1 = FakeBackend("typesafe/jev-1.14", [], errors=["timeout"])
    t2 = FakeBackend("openai/gpt-6-luna", [], errors=["provider"])
    make(tmp_path, t1, t2).run([sign(tmp_path)], {})
    ledger = Ledger(tmp_path / "ledger.json", "exp", tiers())
    assert ledger.attempts("tier1") == 1 and ledger.attempts("tier2") == 1  # AC-15
