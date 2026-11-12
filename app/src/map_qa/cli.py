"""Command-line interface (ADR-000 §6, ADR-004 §9).

Exit codes: 0 success; 2 configuration, input, source, review or argument error; 1 live-mode confirmation declined.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from map_qa import evaluate
from map_qa.config import resolve_settings
from map_qa.models import DEFAULT_THRESHOLD, DEFAULT_TIER1_MODEL, MODEL_MODES, OUTCOMES, MapQaError

EXIT_OK = 0
EXIT_DECLINED = 1
EXIT_ERROR = 2

_EXPERIMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _threshold(text: str) -> float:
    value = float(text)
    if not 0 < value <= 1:
        raise argparse.ArgumentTypeError("must be in (0, 1]")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _non_negative_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _experiment_id(text: str) -> str:
    if not _EXPERIMENT_RE.match(text):
        raise argparse.ArgumentTypeError("must match ^[a-z0-9][a-z0-9_-]{0,63}$")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="map-qa", description="Map QA: speed-limit sign review for Kraków")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="read the signs, run the cascade, compare with OSM and write the report")
    run.add_argument("--source", choices=("local", "databricks"), help="source of every dataset that supports it")
    run.add_argument("--source-osm-roads", choices=("local", "databricks"), help="source of osm_roads")
    run.add_argument("--config", help="JSON configuration file (default: $MAPQA_CONFIG)")
    run.add_argument("--out", type=Path, help="output folder (default: reports/map-qa/run)")
    run.add_argument("--model-mode", choices=MODEL_MODES, help="off (default), cache or live")
    run.add_argument("--sample", help="comma-separated sample names, id files or photo ids")
    run.add_argument("--threshold", type=_threshold, default=DEFAULT_THRESHOLD, help="tier-1 confidence threshold")
    run.add_argument("--max-requests-tier1", type=_non_negative_int, default=25)
    run.add_argument("--max-requests-tier2", type=_non_negative_int, default=10)
    run.add_argument("--max-cost-tier1", type=_non_negative_float, default=0.05, help="USD")
    run.add_argument("--max-cost-tier2", type=_non_negative_float, default=0.10, help="USD")
    run.add_argument("--tier1-model", default=DEFAULT_TIER1_MODEL)
    run.add_argument("--experiment-id", type=_experiment_id, default="default")
    run.add_argument("--review", type=Path, help="review CSV (default: <out>/review.csv)")
    run.add_argument("--cache-dir", type=Path, help="answer cache and ledger folder (default: app/.cache/map-qa)")
    run.add_argument("--yes", action="store_true", help="skip the live-mode confirmation")
    run.add_argument("--dry-run", action="store_true", help="print sources and maximum calls; read no data")

    ev = sub.add_parser("evaluate", help="rebuild the evaluation section from existing results")
    ev.add_argument("--out", type=Path, help="output folder of a previous run")
    ev.add_argument("--config", help="JSON configuration file")
    return parser


def _default_out() -> Path:
    from map_qa.config import default_repo_root

    return default_repo_root() / "reports" / "map-qa" / "run"


def _print_dry_run(settings, options) -> None:
    from map_qa.pipeline import max_calls, resolve_sample, tier_configs

    print("Tryb próbny (--dry-run): dane nie są czytane, modele nie są wywoływane.")
    print("Źródła danych:")
    for name, src in settings.sources.items():
        print(f"  {name}: {src.kind} {src.location} (wybrane przez: {src.chosen_by})")
    print(f"Tryb modeli: {settings.model_mode} (wybrany przez: {settings.model_mode_chosen_by})")
    tiers = tier_configs(options)
    sample = resolve_sample(options.sample)
    calls = max_calls(settings.model_mode, tiers, len(sample) if sample is not None else None)
    for key, tier in tiers.items():
        print(
            f"  {key} {tier.model}: limit {tier.max_requests} żądań, {tier.max_cost_usd:.4f} USD; "
            f"maks. wywołań w tym przebiegu: {calls[key]}"
        )


def _print_summary(summary) -> None:
    ctx = summary.ctx
    print("Pliki wynikowe:")
    for key in ("report", "results", "review", "run"):
        print(f"  {summary.paths[key]}")
    counts = {o: 0 for o in OUTCOMES}
    for r in ctx.results:
        counts[r.comparison.outcome] += 1
    print("Wyniki:")
    for outcome in OUTCOMES:
        print(f"  {outcome:<15} {counts[outcome]:>4}")
    print("Poziomy:  wysłane zaakceptowane eskalowane błędy cache żądania koszt_USD")
    for key, stats in ctx.tier_stats.items():
        print(
            f"  {key}: {stats.sent:>6} {stats.accepted:>13} {stats.escalated:>10} {stats.failed:>6} "
            f"{stats.cache_hits:>5} {stats.requests:>8} {stats.cost_usd:>9.4f}"
            + (f"  limit: {stats.limit_reached}" if stats.limit_reached else "")
        )
    for warning in ctx.warnings:
        print(f"Ostrzeżenie: {warning}")


def _run(args: argparse.Namespace) -> int:
    from map_qa.pipeline import RunOptions, execute

    env = dict(os.environ)
    settings = resolve_settings(
        config=args.config,
        source=args.source,
        source_osm_roads=args.source_osm_roads,
        model_mode=args.model_mode,
        env=env,
    )
    options = RunOptions(
        out=args.out or _default_out(),
        review=args.review,
        cache_dir=args.cache_dir,
        sample=args.sample,
        threshold=args.threshold,
        tier1_model=args.tier1_model,
        max_requests_tier1=args.max_requests_tier1,
        max_requests_tier2=args.max_requests_tier2,
        max_cost_tier1=args.max_cost_tier1,
        max_cost_tier2=args.max_cost_tier2,
        experiment_id=args.experiment_id,
        yes=args.yes,
        env=env,
    )
    if args.dry_run:
        _print_dry_run(settings, options)
        return EXIT_OK
    summary = execute(settings, options, evaluator=evaluate.evaluation_markdown)
    _print_summary(summary)
    return EXIT_OK


def _evaluate(args: argparse.Namespace) -> int:
    settings = resolve_settings(config=args.config, source=None, source_osm_roads=None, model_mode=None, env=dict(os.environ))
    out = args.out or _default_out()
    report = evaluate.rebuild_report(out, settings)
    print(f"Zaktualizowano sekcję ewaluacji: {report}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        return _evaluate(args)
    except MapQaError as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return EXIT_ERROR
