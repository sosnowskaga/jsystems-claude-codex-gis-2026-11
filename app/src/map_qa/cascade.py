"""Reading cascade: human value → (cache) → tier 1 → tier 2 → manual queue (ADR-001, PRD §4.4).

T9 implements the human and ``off`` paths. ``cache`` and ``live`` are added in T10 and T16.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from map_qa.models import (
    TIER_HUMAN,
    SignReading,
    Sign,
    StageAnswer,
    TierConfig,
    TierStats,
)


@dataclass
class CascadeResult:
    readings: dict[str, SignReading]
    answers: list[StageAnswer] = field(default_factory=list)
    tier_stats: dict[str, TierStats] = field(default_factory=lambda: {"tier1": TierStats(), "tier2": TierStats()})


@dataclass
class Cascade:
    mode: str
    threshold: float
    tiers: dict[str, TierConfig]

    def run(self, signs: list[Sign], human_values: dict[str, str]) -> CascadeResult:
        result = CascadeResult(readings={})
        for sign in signs:
            human = human_values.get(sign.photo_id)
            if human:
                # AC-30: a sign with a human value is never sent to a model.
                result.readings[sign.photo_id] = SignReading(
                    photo_id=sign.photo_id, value=human, tier=TIER_HUMAN, model="human"
                )
                continue
            if self.mode == "off":
                # AC-17: no model call, cache not read.
                result.readings[sign.photo_id] = SignReading(
                    photo_id=sign.photo_id,
                    value=None,
                    tier=None,
                    model=None,
                    escalation_reasons=("tier1:mode_off",),
                )
                continue
            raise NotImplementedError(f"model mode '{self.mode}' is not implemented in this build")
        return result
