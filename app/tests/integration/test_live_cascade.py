"""Live-mode wiring with captured HTTP, real image normalization, cache and ledger."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from map_qa.cascade import Cascade
from map_qa.decisions_client import DecisionsBackend
from map_qa.ledger import AnswerCache, Ledger
from map_qa.llm_client import ChatBackend
from map_qa.models import Sign, TierConfig

ROOT = Path(__file__).resolve().parents[3]
CAPTURES = ROOT / "app/tests/fixtures/captures"
CROP = ROOT / "course-materials/exercise-data/map-qa/signs/crops/mapillary_1022418992130552.jpg"
KEY = "t16-integration-secret"


def capture(name: str) -> dict:
    return json.loads((CAPTURES / f"openrouter_{name}.json").read_text(encoding="utf-8"))


def setup_cascade(tmp_path: Path, first: str, status: int = 200):
    t1, t2 = capture(first), capture("chat_luna")
    calls = {"tier1": [], "tier2": []}

    def transport(tier, recorded, code):
        def respond(request):
            calls[tier].append(request)
            if code != 200:
                return httpx.Response(code, content=b"")
            return httpx.Response(recorded["status"], json=recorded["body"])
        return httpx.MockTransport(respond)

    backends = {
        "tier1": DecisionsBackend(t1["model"], env={"OPENROUTER_API_KEY": KEY}, transport=transport("tier1", t1, status)),
        "tier2": ChatBackend(t2["model"], env={"OPENROUTER_API_KEY": KEY}, transport=transport("tier2", t2, 200)),
    }
    tiers = {"tier1": TierConfig(t1["model"], 25, 0.05, reservation_usd=0.0005),
             "tier2": TierConfig(t2["model"], 10, 0.10, reservation_usd=0.004)}
    cache = AnswerCache(tmp_path)

    def make(mode="live", cache_root=None):
        return Cascade(mode=mode, threshold=0.8, tiers=tiers,
                       cache=cache if cache_root is None else AnswerCache(cache_root), backends=backends,
                       ledger_factory=lambda: Ledger(tmp_path / "ledger.json", "t16", tiers))

    sign = Sign("1022418992130552", "Mapillary", "Kraków", "s", str(CROP), str(CROP),
                hashlib.sha256(CROP.read_bytes()).hexdigest(), "a", "CC", "u", None)
    return make, calls, sign, tiers


@pytest.mark.parametrize("first,tier,counts,reason", [
    ("decisions_jev", "decision_model", (1, 0), ()),
    ("decisions_luna", "llm", (1, 1), ("tier1:below_threshold",)),
])
def test_live_then_cache(tmp_path, first, tier, counts, reason):
    make, calls, sign, tiers = setup_cascade(tmp_path, first)
    live = make().run([sign], {})
    reading = live.readings[sign.photo_id]
    assert reading.value == "30" and reading.tier == tier and reading.escalation_reasons == reason
    assert tuple(map(len, calls.values())) == counts
    for requests in calls.values():
        requests.clear()
    ledger_before = (tmp_path / "ledger.json").read_bytes()
    cached = make("cache").run([sign], {})
    cached_reading = cached.readings[sign.photo_id]
    assert replace(cached_reading,
                   tier1=replace(cached_reading.tier1, from_cache=False),
                   tier2=replace(cached_reading.tier2, from_cache=False) if cached_reading.tier2 else None) == reading
    assert tuple(map(len, calls.values())) == (0, 0)
    assert all(stats.requests == 0 for stats in cached.tier_stats.values())
    assert (tmp_path / "ledger.json").read_bytes() == ledger_before
    for path in (tmp_path / "answers").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "base64," not in text and KEY not in text
    # A new cache represents a resumed live batch; keep the same cumulative ledger.
    make(cache_root=tmp_path / "next-batch").run([sign], {})
    ledger = Ledger(tmp_path / "ledger.json", "t16", tiers)
    assert (ledger.attempts("tier1"), ledger.attempts("tier2")) == tuple(2 * count for count in counts)
    assert ledger.cost("tier1") == pytest.approx(2 * reading.tier1.cost_usd)
    assert ledger.cost("tier2") == pytest.approx(2 * reading.tier2.cost_usd if reading.tier2 else 0)


def test_rate_limit_escalates_once(tmp_path):
    make, calls, sign, tiers = setup_cascade(tmp_path, "decisions_jev", 429)
    reading = make().run([sign], {}).readings[sign.photo_id]
    assert reading.value == "30" and reading.tier == "llm"
    assert reading.tier1.status == "model_unavailable" and reading.tier1.error_category == "rate_limit"
    assert reading.escalation_reasons == ("tier1:model_unavailable:rate_limit",)
    assert tuple(map(len, calls.values())) == (1, 1)
    make().run([sign], {})
    assert tuple(map(len, calls.values())) == (2, 1)
    ledger = Ledger(tmp_path / "ledger.json", "t16", tiers)
    assert (ledger.attempts("tier1"), ledger.attempts("tier2")) == (2, 1)
    cached = make("cache").run([sign], {}).readings[sign.photo_id]
    assert cached.value == reading.value and cached.tier == reading.tier
    assert tuple(map(len, calls.values())) == (2, 1)
    assert len(list((tmp_path / "answers").rglob("*.json"))) == 1
