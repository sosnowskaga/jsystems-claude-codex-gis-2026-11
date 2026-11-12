"""Evaluation section (stub until T11): writes "0 pozycji"."""

from __future__ import annotations

from pathlib import Path


def evaluation_markdown(out_dir: Path, settings) -> str:
    return ""


def rebuild_report(out_dir: Path, settings) -> Path:
    from map_qa.export import replace_evaluation_section
    from map_qa.models import MapQaError

    report = out_dir / "report.md"
    if not report.is_file():
        raise MapQaError(f"evaluate: report not found: {report}")
    text = report.read_text(encoding="utf-8")
    report.write_text(replace_evaluation_section(text, evaluation_markdown(out_dir, settings)), encoding="utf-8", newline="\n")
    return report
