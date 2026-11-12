"""Run orchestration: settings → inputs → review → cascade → compare → export → evaluate."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from map_qa import __version__
from map_qa.cascade import Cascade
from map_qa.compare import compare
from map_qa.config import Settings, default_repo_root
from map_qa.export import (
    ReportContext,
    render_report,
    write_results_csv,
    write_run_json,
)
from map_qa.inputs import load_inputs
from map_qa.ledger import AnswerCache, Ledger
from map_qa.models import (
    DEFAULT_TIER2_MODEL,
    LUNA_DECISIONS_MODEL,
    MapQaError,
    SignResult,
    TierConfig,
)
from map_qa.review import human_values, machine_rows, merge, read_review, verdicts, write_review
from map_qa.sources.databricks import DatabricksRoadsSource
from map_qa.sources.local import LocalRoadsSource

# Fixed per-call cost reservations in USD (ADR-001 §5).
RESERVATIONS = {
    "typesafe/jev-1.14": 0.0005,
    LUNA_DECISIONS_MODEL: 0.002,
    DEFAULT_TIER2_MODEL: 0.004,
}
TIMEOUT_S = 40.0


@dataclass
class RunOptions:
    out: Path
    review: Path | None = None
    cache_dir: Path | None = None
    sample: str | None = None
    threshold: float = 0.80
    tier1_model: str = "typesafe/jev-1.14"
    max_requests_tier1: int = 25
    max_requests_tier2: int = 10
    max_cost_tier1: float = 0.05
    max_cost_tier2: float = 0.10
    experiment_id: str = "default"
    yes: bool = False
    confirm: Callable[[], bool] | None = None  # asks the user (AC-18)
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    @property
    def review_path(self) -> Path:
        return self.review or self.out / "review.csv"

    @property
    def cache_path(self) -> Path:
        if self.cache_dir:
            return self.cache_dir
        if self.env.get("MAPQA_CACHE_DIR"):
            return Path(self.env["MAPQA_CACHE_DIR"])
        return default_repo_root() / "app" / ".cache" / "map-qa"


@dataclass
class RunSummary:
    ctx: ReportContext
    paths: dict[str, Path]


def tier_configs(options: RunOptions) -> dict[str, TierConfig]:
    return {
        "tier1": TierConfig(
            model=options.tier1_model,
            max_requests=options.max_requests_tier1,
            max_cost_usd=options.max_cost_tier1,
            timeout_s=TIMEOUT_S,
            reservation_usd=RESERVATIONS.get(options.tier1_model, 0.002),
        ),
        "tier2": TierConfig(
            model=DEFAULT_TIER2_MODEL,
            max_requests=options.max_requests_tier2,
            max_cost_usd=options.max_cost_tier2,
            timeout_s=TIMEOUT_S,
            reservation_usd=RESERVATIONS[DEFAULT_TIER2_MODEL],
        ),
    }


def samples_dir() -> Path:
    return default_repo_root() / "app" / "samples"


def resolve_sample(spec: str | None, known_ids: set[str] | None = None) -> list[str] | None:
    """Comma-separated tokens: a sample name, a file with one id per line, or a photo_id (ADR-004 §9)."""
    if not spec:
        return None
    ids: list[str] = []
    for token in (t.strip() for t in spec.split(",")):
        if not token:
            continue
        named = samples_dir() / f"{token}.txt"
        path = Path(token)
        if named.is_file():
            source = named
        elif path.suffix == ".txt" or path.is_file():
            if not path.is_file():
                raise MapQaError(f"sample: file not found: {token}")
            source = path
        else:
            ids.append(token)
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    unique = list(dict.fromkeys(ids))
    if known_ids is not None:
        unknown = [i for i in unique if i not in known_ids]
        if unknown:
            raise MapQaError(f"sample: unknown photo_id '{unknown[0]}'")
    return unique


def max_calls(mode: str, tiers: dict[str, TierConfig], n_signs: int | None) -> dict[str, int]:
    if mode == "off":
        return {"tier1": 0, "tier2": 0}
    limits = {k: t.max_requests for k, t in tiers.items()}
    if n_signs is not None:
        limits = {k: min(v, n_signs) for k, v in limits.items()}
    if mode == "cache":
        return {"tier1": 0, "tier2": 0}
    return limits


def check_live_prerequisites(settings: Settings, options: RunOptions) -> None:
    if settings.model_mode != "live":
        return
    # PRD §4.7 item 5: checked before the confirmation prompt; only presence, never the value.
    if not options.env.get("OPENROUTER_API_KEY"):
        raise MapQaError("model mode 'live': environment variable OPENROUTER_API_KEY is not set")


class RunDeclined(Exception):
    """The user did not confirm the live-mode calls (exit code 1)."""


def default_backends(mode: str, tiers: dict[str, TierConfig], env: dict[str, str]) -> dict:
    """Tier backends parse cached answers (cache, live) and send requests (live only)."""
    if mode not in ("cache", "live"):
        return {}
    from map_qa.decisions_client import DecisionsBackend
    from map_qa.llm_client import ChatBackend

    return {
        "tier1": DecisionsBackend(tiers["tier1"].model, env=env, timeout_s=tiers["tier1"].timeout_s),
        "tier2": ChatBackend(tiers["tier2"].model, env=env, timeout_s=tiers["tier2"].timeout_s),
    }


def confirmation_text(n_crops: int, tiers: dict[str, TierConfig]) -> str:
    lines = [f"Tryb live: do wysłania co najwyżej {n_crops} wycinków (odpowiedzi z cache nie są wysyłane)."]
    for name, tier in tiers.items():
        lines.append(
            f"  {name} {tier.model}: limit {tier.max_requests} żądań, {tier.max_cost_usd:.4f} USD, "
            f"rezerwacja {tier.reservation_usd:.4f} USD/żądanie, limit czasu {tier.timeout_s:g} s"
        )
    return "\n".join(lines)


def roads_source_for(settings: Settings, env: dict[str, str]):
    src = settings.sources["osm_roads"]
    if src.kind == "local":
        path = Path(src.location)
        if not path.is_absolute():
            path = settings.data_dir / path
        return LocalRoadsSource(path, chosen_by=src.chosen_by, location=src.location)
    db = settings.databricks
    return DatabricksRoadsSource(
        table=src.location,
        env=env,
        host_env=db.host_env,
        token_env=db.token_env,
        warehouse_env=db.warehouse_env,
        warehouse_id=db.warehouse_id,
        chosen_by=src.chosen_by,
    )


def execute(
    settings: Settings,
    options: RunOptions,
    evaluator: Callable[[Path, Settings], str] | None = None,
    backends: dict | None = None,
) -> RunSummary:
    check_live_prerequisites(settings, options)
    chosen_by = {name: s.chosen_by for name, s in settings.sources.items()}
    bundle = load_inputs(settings.data_dir, roads_source_for(settings, options.env), chosen_by)

    sample = resolve_sample(options.sample, {s.photo_id for s in bundle.signs})
    signs = bundle.signs if sample is None else [s for s in bundle.signs if s.photo_id in set(sample)]

    existing_review = read_review(options.review_path)  # validates before any model call (AC-31)
    humans = human_values(existing_review)
    review_verdicts = verdicts(existing_review)

    tiers = tier_configs(options)
    if settings.model_mode == "live":
        n_crops = sum(1 for s in signs if s.photo_id not in humans)
        print(confirmation_text(n_crops, tiers))
        if not options.yes and not (options.confirm or (lambda: False))():
            raise RunDeclined("live-mode calls not confirmed")
    if backends is None:
        backends = default_backends(settings.model_mode, tiers, options.env)
    cache = AnswerCache(options.cache_path) if settings.model_mode in ("cache", "live") else None
    cascade = Cascade(
        mode=settings.model_mode,
        threshold=options.threshold,
        tiers=tiers,
        cache=cache,
        backends=backends or {},
        ledger_factory=lambda: Ledger(options.cache_path / "ledger.json", options.experiment_id, tiers),
    )
    cascade_result = cascade.run(signs, humans)

    results: list[SignResult] = []
    for sign in signs:
        link = bundle.links[sign.photo_id]
        tags = bundle.roads.get(link.osm_way_id) if link.osm_way_id is not None else None
        reading = cascade_result.readings[sign.photo_id]
        comparison = compare(sign, link, tags, reading)
        results.append(
            SignResult(
                sign=sign,
                link=link,
                tags=tags,
                reading=reading,
                comparison=comparison,
                verdict=review_verdicts.get(sign.photo_id) if comparison.outcome in ("mismatch", "map_missing") else None,
            )
        )
    results.sort(key=lambda r: r.sign.photo_id)

    out = options.out
    out.mkdir(parents=True, exist_ok=True)
    ctx = ReportContext(
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        app_version=__version__,
        experiment_id=options.experiment_id,
        model_mode=settings.model_mode,
        threshold=options.threshold,
        tiers=tiers,
        tier_stats=cascade_result.tier_stats,
        sources=bundle.sources,
        osm_snapshot=bundle.osm_snapshot,
        warnings=bundle.warnings,
        results=results,
        out_dir=out,
        sample=sample,
        input_hashes=bundle.input_hashes,
        answers=cascade_result.answers,
        sql_log=bundle.sql_log,
    )
    paths = {
        "results": out / "results.csv",
        "review": options.review_path,
        "run": out / "run.json",
        "report": out / "report.md",
    }
    write_results_csv(paths["results"], ctx)
    write_review(paths["review"], merge(existing_review, machine_rows(results)))
    write_run_json(out, ctx)
    # `run` calls evaluate after the results exist (ADR-000 §4); only cli imports evaluate.
    if evaluator is not None:
        ctx.evaluation_md = evaluator(out, settings)
    paths["report"].write_text(render_report(ctx), encoding="utf-8", newline="\n")
    return RunSummary(ctx=ctx, paths=paths)
