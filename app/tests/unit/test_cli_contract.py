"""CLI contract (ADR-000 §6, ADR-004 §9): option names and commands are fixed."""

from __future__ import annotations

from map_qa.cli import build_parser

RUN_OPTIONS = {
    "--source", "--source-osm-roads", "--config", "--out", "--model-mode", "--sample", "--threshold",
    "--max-requests-tier1", "--max-requests-tier2", "--max-cost-tier1", "--max-cost-tier2", "--tier1-model",
    "--experiment-id", "--review", "--cache-dir", "--yes", "--dry-run",
}


def _options(subcommand: str) -> set[str]:
    parser = build_parser()
    sub = next(a for a in parser._subparsers._group_actions if a.dest == "command")  # noqa: SLF001
    return {o for action in sub.choices[subcommand]._actions for o in action.option_strings if o.startswith("--")} - {"--help"}


def test_run_options_are_exactly_the_contract():
    assert _options("run") == RUN_OPTIONS


def test_evaluate_options():
    assert _options("evaluate") == {"--out", "--config"}


def test_stage_2b_command_parses():
    args = build_parser().parse_args(
        ["run", "--model-mode", "live", "--sample", "stage1,stage2", "--tier1-model", "openai/gpt-6-luna-decisions",
         "--max-requests-tier2", "0", "--experiment-id", "luna-decisions"]
    )
    assert args.tier1_model == "openai/gpt-6-luna-decisions" and args.max_requests_tier2 == 0
    assert args.experiment_id == "luna-decisions"
