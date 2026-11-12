"""Reading cascade: human value → per-tier cache → tier 1 → tier 2 → manual queue (ADR-001, PRD §4.4).

* ``off``: no model call, the cache is never read (AC-17).
* ``cache``: only cached answers, checked per tier; a miss is ``model_unavailable`` (cache_miss).
* ``live``: per tier, the cache first, then a call within the ledger limits (T16 backends).

A tier backend parses a stored HTTP 200 body (``parse``) and, in live mode, sends one request
(``call``). Parsing is derived from the real captures in ``tests/fixtures/captures`` (T15/T16).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from map_qa.image import model_image
from map_qa.ledger import AnswerCache, Ledger
from map_qa.models import (
    ANSWER_SET,
    NONE,
    QUESTION_VERSION,
    TIER_DECISION_MODEL,
    TIER_HUMAN,
    TIER_LLM,
    UNREADABLE,
    Sign,
    SignReading,
    StageAnswer,
    TierConfig,
    TierStats,
)

ACCEPTABLE = frozenset(a for a in ANSWER_SET if a != UNREADABLE)


@dataclass(frozen=True)
class ParsedAnswer:
    """What a tier extracted from one HTTP 200 body."""

    answer: str | None  # top label (tier 1) or value (tier 2); None when the body has no usable answer
    probabilities: dict[str, float] | None = None
    cost_usd: float | None = None
    error_category: str | None = None  # e.g. "parse_error" when the body does not fit the schema


@dataclass(frozen=True)
class CallOutcome:
    """Result of one live request: an entry to cache (HTTP 200) or an error category."""

    entry: dict[str, Any] | None
    error_category: str | None
    latency_ms: int | None


class TierBackend(Protocol):
    model: str

    def parse(self, entry: dict[str, Any]) -> ParsedAnswer: ...

    def call(self, image: bytes, mime_type: str) -> CallOutcome: ...


@dataclass
class CascadeResult:
    readings: dict[str, SignReading]
    answers: list[StageAnswer] = field(default_factory=list)
    tier_stats: dict[str, TierStats] = field(default_factory=lambda: {"tier1": TierStats(), "tier2": TierStats()})


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def mime_type_for(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "image/jpeg"


@dataclass
class Cascade:
    mode: str
    threshold: float
    tiers: dict[str, TierConfig]
    cache: AnswerCache | None = None
    backends: dict[str, TierBackend] = field(default_factory=dict)
    ledger_factory: Any = None  # () -> Ledger; created lazily so cache mode never writes a ledger

    def __post_init__(self) -> None:
        self._ledger: Ledger | None = None

    # --- public -------------------------------------------------------------------------------

    def run(self, signs: list[Sign], human_values: dict[str, str]) -> CascadeResult:
        result = CascadeResult(readings={})
        for sign in signs:
            human = human_values.get(sign.photo_id)
            if human:
                # AC-30: a sign with a human value is never sent to a model.
                result.readings[sign.photo_id] = SignReading(sign.photo_id, value=human, tier=TIER_HUMAN, model="human")
                continue
            if self.mode == "off":
                # AC-17: no model call, cache not read.
                result.readings[sign.photo_id] = SignReading(
                    sign.photo_id, value=None, tier=None, model=None, escalation_reasons=("tier1:mode_off",)
                )
                continue
            result.readings[sign.photo_id] = self._read(sign, result)
        return result

    # --- tiers --------------------------------------------------------------------------------

    def _read(self, sign: Sign, result: CascadeResult) -> SignReading:
        reasons: list[str] = []
        image = model_image(sign.crop_path)  # same normalized bytes for every tier (AC-12, ADR-001 §4a)
        t1 = self._ask("tier1", 1, sign, image, result)
        result.answers.append(t1)
        stats1 = result.tier_stats["tier1"]
        stats1.sent += 1
        reason = self._tier1_reason(t1)
        if reason is None:
            stats1.accepted += 1
            return SignReading(sign.photo_id, value=t1.answer, tier=TIER_DECISION_MODEL, model=t1.model, tier1=t1)
        stats1.escalated += 1
        reasons.append(f"tier1:{reason}")

        t2 = self._ask("tier2", 2, sign, image, result)
        result.answers.append(t2)
        stats2 = result.tier_stats["tier2"]
        stats2.sent += 1
        reason = self._tier2_reason(t2)
        if reason is None:
            stats2.accepted += 1
            return SignReading(
                sign.photo_id, value=t2.answer, tier=TIER_LLM, model=t2.model, tier1=t1, tier2=t2,
                escalation_reasons=tuple(reasons),
            )
        stats2.escalated += 1
        reasons.append(f"tier2:{reason}")
        return SignReading(sign.photo_id, value=None, tier=None, model=None, tier1=t1, tier2=t2,
                           escalation_reasons=tuple(reasons))

    def _tier1_reason(self, answer: StageAnswer) -> str | None:
        """None when tier 1 accepts (accept-v1), else the escalation reason."""
        if answer.status != "answered":
            return "cache_miss" if answer.error_category == "cache_miss" else f"model_unavailable:{answer.error_category}"
        if not answer.probabilities or answer.answer is None:
            return "no_probabilities"
        if answer.answer == UNREADABLE:
            return "unreadable"
        if answer.answer not in ACCEPTABLE:
            return "out_of_set"
        p = answer.top_probability
        if p is None or p < self.threshold:
            return "below_threshold"
        return None

    @staticmethod
    def _tier2_reason(answer: StageAnswer) -> str | None:
        if answer.status != "answered":
            return "cache_miss" if answer.error_category == "cache_miss" else f"model_unavailable:{answer.error_category}"
        if answer.answer == UNREADABLE:
            return "unreadable"
        if answer.answer not in ACCEPTABLE:
            return "out_of_set"
        return None

    def _ask(self, tier: str, number: int, sign: Sign, image: tuple[bytes, str, str], result: CascadeResult) -> StageAnswer:
        config = self.tiers[tier]
        stats = result.tier_stats[tier]
        image_bytes, mime_type, image_sha = image
        base = dict(photo_id=sign.photo_id, tier=number, model=config.model, question_version=QUESTION_VERSION,
                    image_sha256=image_sha)
        backend = self.backends.get(tier)

        entry = self.cache.get(config.model, QUESTION_VERSION, image_sha) if self.cache else None
        if entry is not None:
            stats.cache_hits += 1
            if backend is None:
                return self._unavailable(base, stats, "parse_error")
            parsed = backend.parse(entry)
            return self._answered(base, stats, parsed, entry, from_cache=True)

        if self.mode != "live":
            return self._unavailable(base, stats, "cache_miss")

        ledger = self._get_ledger()
        blocked = stats.limit_reached or ledger.blocked(tier)
        if blocked:
            if stats.limit_reached is None:
                stats.limit_reached = blocked
                ledger.mark_limit(tier, blocked)
            stats.signs_affected_by_limit += 1
            return self._unavailable(base, stats, "limit_reached" if blocked == "max_requests" else "cost_limit")

        reserved = ledger.reserve(tier)  # counted before the request (AC-15)
        stats.requests += 1
        requested_at = _now()
        outcome = backend.call(image_bytes, mime_type)
        if outcome.entry is None:
            ledger.settle(tier, reserved, None)
            stats.cost_usd = round(stats.cost_usd + reserved, 10)
            answer = self._unavailable(base, stats, outcome.error_category or "provider")
            return StageAnswer(**{**answer.__dict__, "latency_ms": outcome.latency_ms, "requested_at": requested_at})

        entry = dict(outcome.entry)
        entry.setdefault("latency_ms", outcome.latency_ms)
        entry.setdefault("requested_at", requested_at)
        if self.cache:
            self.cache.put(config.model, QUESTION_VERSION, image_sha, entry)
        parsed = backend.parse(entry)
        actual = parsed.cost_usd
        if ledger.settle(tier, reserved, actual):
            stats.limit_reached = stats.limit_reached or "max_cost_usd"
            ledger.mark_limit(tier, "max_cost_usd")
        stats.cost_usd = round(stats.cost_usd + (actual if actual is not None else reserved), 10)
        return self._answered(base, stats, parsed, entry, from_cache=False)

    def _get_ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = self.ledger_factory()
        return self._ledger

    @staticmethod
    def _unavailable(base: dict, stats: TierStats, category: str) -> StageAnswer:
        stats.failed += 1
        return StageAnswer(**base, status="model_unavailable", error_category=category)

    @staticmethod
    def _answered(base: dict, stats: TierStats, parsed: ParsedAnswer, entry: dict, from_cache: bool) -> StageAnswer:
        if parsed.error_category is not None:
            stats.failed += 1
            return StageAnswer(**base, status="model_unavailable", from_cache=from_cache,
                               error_category=parsed.error_category, latency_ms=entry.get("latency_ms"),
                               requested_at=entry.get("requested_at"), cost_usd=parsed.cost_usd,
                               raw_response=entry.get("response"))
        return StageAnswer(
            **base, status="answered", from_cache=from_cache, answer=parsed.answer,
            probabilities=parsed.probabilities, cost_usd=parsed.cost_usd,
            latency_ms=entry.get("latency_ms"), requested_at=entry.get("requested_at"),
            raw_response=entry.get("response"),
        )


__all__ = ["Cascade", "CascadeResult", "CallOutcome", "ParsedAnswer", "TierBackend", "NONE", "mime_type_for"]
