"""Domain models and codes shared by every module (ADR-000 §5, ADR-004 §3).

Plain frozen dataclasses with no behaviour beyond small helpers. Modules exchange these objects;
CSV/JSON layout is defined by ADR-004 and implemented in ``export``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Answer set (ADR-001 §4) -------------------------------------------------------------------

SPEED_VALUES: tuple[int, ...] = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 90, 100, 110, 120, 130, 140)
NONE = "none"
UNREADABLE = "unreadable"
ANSWER_SET: tuple[str, ...] = tuple(str(v) for v in SPEED_VALUES) + (NONE, UNREADABLE)

QUESTION_VERSION = "b33-v1"
ACCEPT_RULE = "accept-v1"
DEFAULT_THRESHOLD = 0.80
DEFAULT_TIER1_MODEL = "typesafe/jev-1.14"
DEFAULT_TIER2_MODEL = "openai/gpt-6-luna"
LUNA_DECISIONS_MODEL = "openai/gpt-6-luna-decisions"
KRAKOW = "Kraków"


def is_speed_value(answer: str | None) -> bool:
    """True for a numeric answer from the closed set (``"50"``), False for ``none``/``unreadable``/other."""
    return answer is not None and answer in ANSWER_SET[: len(SPEED_VALUES)]


# --- Codes (ADR-004 §3) ------------------------------------------------------------------------

MATCH = "match"
MISMATCH = "mismatch"
MAP_MISSING = "map_missing"
NOT_COMPARABLE = "not_comparable"
ABSTAIN = "abstain"
PENDING_HUMAN = "pending_human"
OUTCOMES: tuple[str, ...] = (MATCH, MISMATCH, MAP_MISSING, NOT_COMPARABLE, ABSTAIN, PENDING_HUMAN)
CANDIDATE_OUTCOMES: frozenset[str] = frozenset({MISMATCH, MAP_MISSING})

TIER_DECISION_MODEL = "decision_model"
TIER_LLM = "llm"
TIER_HUMAN = "human"

# abstain reasons, joined with ";" in this order
ABSTAIN_OUTSIDE_KRAKOW = "outside_krakow"
ABSTAIN_ROAD_NOT_IN_OSM = "road_not_in_osm"
ABSTAIN_FLAG_PREFIX = "flag:"

# not_comparable reasons
NC_SIGN_NONE = "sign_none"
NC_SIGN_UNREADABLE = "sign_unreadable"
NC_DIRECTIONAL_OR_CONDITIONAL = "directional_or_conditional_tag"
NC_MAXSPEED_NOT_NUMERIC = "maxspeed_not_numeric"
NC_IMPLICIT_LIMIT = "implicit_limit"

VERDICTS: frozenset[str] = frozenset({"confirmed", "no_problem", "cannot_assess"})

MODEL_MODES: tuple[str, ...] = ("off", "cache", "live")

# OSM speed rule kinds (ADR-003 §3)
KIND_NUMERIC = "numeric"
KIND_NUMERIC_OTHER_UNIT = "numeric_other_unit"
KIND_IMPLICIT = "implicit"
KIND_SPECIAL = "special"
KIND_MULTI = "multi"
KIND_CONDITIONAL = "conditional"
KIND_UNPARSED = "unparsed"

# Implicit-limit tags, in the order used for osm_implicit_tag (ADR-004 §4)
IMPLICIT_TAGS: tuple[str, ...] = ("maxspeed:type", "source:maxspeed", "zone:maxspeed")


# --- Inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sign:
    photo_id: str
    source: str  # "Mapillary" / "Panoramax"
    area: str
    sequence: str
    crop_path: str  # absolute path to the crop file
    photo_path: str  # absolute path to the full photo
    crop_sha256: str  # computed from the file bytes
    creator: str
    license: str
    source_url: str
    mapillary_value: str | None  # detector class; evaluation baseline only, never sent to a model


@dataclass(frozen=True)
class RoadLink:
    photo_id: str
    osm_way_id: int | None
    distance_m: str | None  # unchanged text from nearest_osm.csv
    match_flags: tuple[str, ...]  # split on ";", empty tuple when no flag
    candidate_count: int | None


@dataclass(frozen=True)
class OsmSpeedTags:
    """Raw speed tags of one OSM way. ``None`` means the tag is absent (never ``""`` or 0)."""

    osm_way_id: int
    maxspeed: str | None = None
    maxspeed_forward: str | None = None
    maxspeed_backward: str | None = None
    maxspeed_conditional: str | None = None
    implicit_tag: str | None = None  # first of IMPLICIT_TAGS with a "PL:" value
    implicit_value: str | None = None


@dataclass(frozen=True)
class SpeedRule:
    source_tag: str
    raw: str
    kind: str
    value_kmh: int | None = None
    unit_raw: str | None = None
    condition_raw: str | None = None
    multi_value: bool = False


@dataclass(frozen=True)
class SourceMeta:
    dataset: str
    source_kind: str  # "local" / "databricks"
    location: str
    chosen_by: str  # "option" / "env" / "config" / "default"
    rows: int | None = None
    snapshot_rows: int | None = None
    read_at: str | None = None
    statement_id: str | None = None


@dataclass
class InputBundle:
    signs: list[Sign]
    links: dict[str, RoadLink]  # by photo_id
    roads: dict[int, OsmSpeedTags]  # by osm_way_id; only roads referenced by links
    sources: list[SourceMeta]
    warnings: list[str] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    osm_snapshot: str | None = None  # from sources.json osm.extract_date
    sql_log: list[dict[str, Any]] = field(default_factory=list)


# --- Cascade -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class StageAnswer:
    photo_id: str
    tier: int  # 1 or 2
    model: str
    question_version: str
    image_sha256: str
    status: str  # "answered" / "model_unavailable"
    from_cache: bool = False
    answer: str | None = None  # top label (tier 1) or value (tier 2); None when unavailable
    probabilities: dict[str, float] | None = None  # tier 1 only
    cost_usd: float | None = None
    latency_ms: int | None = None
    error_category: str | None = None
    requested_at: str | None = None
    raw_response: Any = None  # kept in the cache only, never in run.json

    @property
    def top_probability(self) -> float | None:
        if not self.probabilities or self.answer is None:
            return None
        return self.probabilities.get(self.answer)


@dataclass(frozen=True)
class SignReading:
    photo_id: str
    value: str | None  # accepted value (number text, "none", or human "unreadable"); None if not accepted
    tier: str | None  # TIER_* or None
    model: str | None  # model of the accepted answer, "human", or None
    tier1: StageAnswer | None = None
    tier2: StageAnswer | None = None
    escalation_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Comparison:
    photo_id: str
    outcome: str
    abstain_reason: str | None = None
    not_comparable_reason: str | None = None


@dataclass(frozen=True)
class SignResult:
    """Everything export needs for one sign (one row of results.csv)."""

    sign: Sign
    link: RoadLink
    tags: OsmSpeedTags | None  # None when the road is not in osm_roads
    reading: SignReading
    comparison: Comparison
    verdict: str | None = None  # from the candidate row of review.csv


# --- Review ------------------------------------------------------------------------------------

REVIEW_COLUMNS: tuple[str, ...] = (
    "row_id",
    "row_type",
    "photo_id",
    "osm_way_id",
    "tier1_answer",
    "tier2_answer",
    "human_value",
    "outcome",
    "verdict",
    "comment",
)


@dataclass(frozen=True)
class ReviewRow:
    row_id: str
    row_type: str  # "queue" / "candidate"
    photo_id: str
    osm_way_id: str = ""
    tier1_answer: str = ""
    tier2_answer: str = ""
    human_value: str = ""
    outcome: str = ""
    verdict: str = ""
    comment: str = ""

    @staticmethod
    def make_id(photo_id: str, row_type: str) -> str:
        return f"{photo_id}:{row_type}"


# --- Tiers and run -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TierConfig:
    model: str
    max_requests: int
    max_cost_usd: float
    timeout_s: float = 40.0
    reservation_usd: float = 0.0


@dataclass
class TierStats:
    sent: int = 0
    accepted: int = 0
    escalated: int = 0
    failed: int = 0
    cache_hits: int = 0
    requests: int = 0
    cost_usd: float = 0.0
    limit_reached: str | None = None  # None / "max_requests" / "max_cost_usd"
    signs_affected_by_limit: int = 0


class MapQaError(Exception):
    """A configuration, input, source or review error: one line, exit code 2 (ADR-004 §9)."""
