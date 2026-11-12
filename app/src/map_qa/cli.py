"""Command-line entry point (stub until T9)."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="map-qa", description="Map QA: speed-limit sign review for Kraków")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="read the signs, run the cascade, compare with OSM and write the report")
    sub.add_parser("evaluate", help="rebuild the evaluation section from existing results")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
