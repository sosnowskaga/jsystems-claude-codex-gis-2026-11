from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import models
from .models import (
    CANDIDATE_OUTCOMES, OUTCOMES, SignResult, SourceMeta, StageAnswer,
    TierConfig, TierStats,
)

RESULTS_COLUMNS: tuple[str, ...] = (
    "photo_id", "area", "sequence", "outcome", "abstain_reason", "not_comparable_reason",
    "value", "tier", "model", "tier1_model", "tier1_answer", "tier1_probability",
    "tier2_model", "tier2_answer", "escalation_reasons", "osm_way_id", "distance_m",
    "match_flags", "osm_maxspeed", "osm_maxspeed_forward", "osm_maxspeed_backward",
    "osm_maxspeed_conditional", "osm_implicit_tag", "osm_implicit_value", "verdict",
    "crop_path", "photo_path", "creator", "license", "source_url",
)


@dataclass
class ReportContext:
    generated_at: str
    app_version: str
    experiment_id: str
    model_mode: str
    threshold: float
    tiers: dict[str, TierConfig]
    tier_stats: dict[str, TierStats]
    sources: list[SourceMeta]
    osm_snapshot: str | None
    warnings: list[str]
    results: list[SignResult]
    out_dir: Path
    sample: list[str] | None = None
    input_hashes: dict[str, str] = field(default_factory=dict)
    answers: list[StageAnswer] = field(default_factory=list)
    sql_log: list[dict] = field(default_factory=list)
    evaluation_md: str = ""


def outcome_counts(results: list[SignResult]) -> dict[str, int]:
    counts = dict.fromkeys(OUTCOMES, 0)
    for result in results:
        counts[result.comparison.outcome] += 1
    return counts


def summary_counts(results: list[SignResult]) -> dict:
    candidates = [r for r in results if r.comparison.outcome in CANDIDATE_OUTCOMES]
    return {
        "outcomes": outcome_counts(results),
        "queue": sum(r.reading.value is None for r in results),
        "candidates": len(candidates),
        "reviewed": sum(bool(r.verdict) for r in candidates),
        "confirmed": sum(r.verdict == "confirmed" for r in candidates),
    }


def link_path(path: str | Path, out_dir: Path) -> str:
    """Path relative to the report folder with ``/`` separators (AC-27).

    On Windows a path on another drive has no relative form; it is then given absolute.
    """
    try:
        return os.path.relpath(path, out_dir).replace("\\", "/")
    except ValueError:
        return Path(path).as_posix()


def result_row(result: SignResult, out_dir: Path, tiers: dict[str, TierConfig] | None = None) -> dict[str, str]:
    sign, link, tags, reading = result.sign, result.link, result.tags, result.reading
    t1, t2 = reading.tier1, reading.tier2
    rel = lambda path: link_path(path, out_dir)
    return dict(zip(RESULTS_COLUMNS, (
        sign.photo_id, sign.area, sign.sequence, result.comparison.outcome,
        result.comparison.abstain_reason or "", result.comparison.not_comparable_reason or "",
        reading.value or "", reading.tier or "", reading.model or "",
        t1.model if t1 else (tiers["tier1"].model if tiers else ""), t1.answer or "" if t1 else "",
        f"{t1.top_probability:.4f}" if t1 and t1.top_probability is not None else "",
        t2.model if t2 else (tiers["tier2"].model if tiers else ""), t2.answer or "" if t2 else "",
        ";".join(reading.escalation_reasons), str(link.osm_way_id) if link.osm_way_id is not None else "",
        link.distance_m or "", ";".join(link.match_flags), tags.maxspeed or "" if tags else "",
        tags.maxspeed_forward or "" if tags else "", tags.maxspeed_backward or "" if tags else "",
        tags.maxspeed_conditional or "" if tags else "", tags.implicit_tag or "" if tags else "",
        tags.implicit_value or "" if tags else "", result.verdict or "", rel(sign.crop_path), rel(sign.photo_path),
        sign.creator, sign.license, sign.source_url,
    )))


def write_results_csv(path: Path, ctx: ReportContext) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(result_row(r, ctx.out_dir, ctx.tiers) for r in sorted(ctx.results, key=lambda x: x.sign.photo_id))


def md_escape(text: str) -> str:
    text = str(text).replace("\r", " ").replace("\n", " ").replace("\\", "\\\\")  # backslash first
    for char in ("|", "`", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text.replace("<", "&lt;")


def _cell(value: object) -> str:
    return md_escape("" if value is None else str(value))


def _table(headers: list[str], rows: list[list[object]]) -> str:
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join("| " + " | ".join(_cell(v) for v in row) + " |\n" for row in rows)


class _MarkdownCell(str):
    pass


def _crop_cell(r: SignResult, ctx: ReportContext) -> str:
    sign = r.sign
    crop = link_path(sign.crop_path, ctx.out_dir)
    photo = link_path(sign.photo_path, ctx.out_dir)
    return _MarkdownCell(f"[wycinek](<{md_escape(crop)}>) · [zdjęcie](<{md_escape(photo)}>) — {_cell(sign.creator)}, [{_cell(sign.source)}]({md_escape(sign.source_url)}), {_cell(sign.license)}")


def _rows_table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "0 pozycji\n"
    # Link cells contain Markdown syntax and are prepared separately by the caller.
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join("| " + " | ".join(str(v) if isinstance(v, _MarkdownCell) else _cell(v) for v in row) + " |\n" for row in rows)


def render_report(ctx: ReportContext) -> str:
    results = sorted(ctx.results, key=lambda r: r.sign.photo_id)
    counts = summary_counts(ctx.results)
    lines = ["# Raport Map QA: znaki ograniczenia prędkości", f"Wygenerowano: {ctx.generated_at}", "## Nagłówek", ""]
    source_rows = [[s.dataset, s.source_kind, s.location, s.chosen_by] for s in ctx.sources]
    lines += [_table(["dataset", "źródło", "lokalizacja", "wybrane przez"], source_rows), ""]
    lines += [
        f"- Migawka OSM: {_cell(ctx.osm_snapshot)}",
        f"- Tryb modeli: `{ctx.model_mode}`",
        f"- Eksperyment: `{ctx.experiment_id}`",
        f"- Próg pewności poziomu 1: {ctx.threshold:.2f}",
        f"- Wersja pytania: `{models.QUESTION_VERSION}`, reguła akceptacji: `{models.ACCEPT_RULE}`",
        "",
    ]
    tier_rows = [
        [name, config.model, config.max_requests, f"{config.max_cost_usd:.4f}", f"{config.timeout_s:g}"]
        for name, config in ((t, ctx.tiers[t]) for t in ("tier1", "tier2"))
    ]
    lines += [_table(["poziom", "model", "Limit żądań", "limit kosztu USD", "limit czasu s"], tier_rows), ""]
    if ctx.warnings:
        lines += ["Ostrzeżenia:", ""] + [f"- {_cell(w)}" for w in ctx.warnings] + [""]
    lines += ["Rozbieżność znaku i mapy nie oznacza automatycznie błędu mapy.", "", "## Podsumowanie", ""]
    lines += [_table(["outcome", "liczba"], [[k, v] for k, v in counts["outcomes"].items()]), "", f"Kolejka ręczna: {counts['queue']}", f"Kandydaci: {counts['candidates']}", f"Zweryfikowani: {counts['reviewed']}", f"Potwierdzeni: {counts['confirmed']}", ""]
    stat_rows = [[tier, ctx.tiers[tier].model, st.sent, st.accepted, st.escalated, st.failed, st.cache_hits, st.requests, st.cost_usd, st.limit_reached or ""] for tier in ("tier1", "tier2") for st in [ctx.tier_stats[tier]]]
    lines += [_table(["tier", "model", "wysłane", "zaakceptowane", "eskalowane", "błędy", "z cache", "żądania", "koszt USD", "limit"], stat_rows), "", "## Kandydaci do weryfikacji", ""]
    cand = [r for r in results if r.comparison.outcome in CANDIDATE_OUTCOMES]
    rows = []
    for r in cand:
        tags = r.tags
        raw = "; ".join(f"{k}={v}" for k, v in (("maxspeed", tags.maxspeed), ("maxspeed:forward", tags.maxspeed_forward), ("maxspeed:backward", tags.maxspeed_backward), ("maxspeed:conditional", tags.maxspeed_conditional), (tags.implicit_tag, tags.implicit_value)) if v is not None) if tags else ""
        rows.append([r.sign.photo_id, _crop_cell(r, ctx), r.reading.value, r.reading.tier, r.link.osm_way_id, raw, r.link.distance_m, r.verdict or "do przeglądu"])
    lines += [_rows_table(["photo_id", "wycinek / zdjęcie", "wartość", "tier", "OSM way id", "tagi prędkości", "odległość m", "status"], rows), "", "## Kolejka ręczna", ""]
    queue = [r for r in results if r.reading.value is None]
    rows = [[r.sign.photo_id, _crop_cell(r, ctx), r.reading.tier1.answer if r.reading.tier1 else "", f"{r.reading.tier1.top_probability:.4f}" if r.reading.tier1 and r.reading.tier1.top_probability is not None else "", r.reading.tier2.answer if r.reading.tier2 else "", ";".join(r.reading.escalation_reasons), r.comparison.outcome] for r in queue]
    lines += [_rows_table(["photo_id", "wycinek / zdjęcie", "tier-1 odpowiedź", "prawdopodobieństwo", "tier-2 odpowiedź", "powody eskalacji", "wynik"], rows), "", "## Nie da się porównać", ""]
    nc = [r for r in results if r.comparison.outcome in {models.ABSTAIN, models.NOT_COMPARABLE}]
    rows = [[r.sign.photo_id, r.comparison.outcome, r.comparison.abstain_reason or r.comparison.not_comparable_reason or ""] for r in nc]
    lines += [_rows_table(["photo_id", "wynik", "powód"], rows), "", "## Ewaluacja", "", ctx.evaluation_md or "0 pozycji", ""]
    return "\n".join(lines)


def replace_evaluation_section(report_text: str, evaluation_md: str) -> str:
    return report_text[:report_text.index("## Ewaluacja")] + "## Ewaluacja\n\n" + (evaluation_md or "0 pozycji") + "\n"


def run_payload(ctx: ReportContext) -> dict:
    def clean(obj):
        data = asdict(obj)
        data.pop("raw_response", None)
        return data
    return {
        "schema_version": 1, "generated_at": ctx.generated_at, "app_version": ctx.app_version,
        "experiment_id": ctx.experiment_id, "model_mode": ctx.model_mode,
        "question_version": models.QUESTION_VERSION, "accept_rule": models.ACCEPT_RULE,
        "threshold": ctx.threshold,
        "tiers": {k: {name: getattr(v, name) for name in ("model", "max_requests", "max_cost_usd", "timeout_s", "reservation_usd")} for k, v in ctx.tiers.items()},
        "sample": ctx.sample, "sources": [asdict(s) for s in ctx.sources], "warnings": ctx.warnings,
        "input_hashes": ctx.input_hashes, "counts": summary_counts(ctx.results),
        "tier_stats": {k: asdict(v) for k, v in ctx.tier_stats.items()},
        "answers": [clean(a) for a in ctx.answers], "sql_log": ctx.sql_log,
    }


def write_run_json(out_dir: Path, ctx: ReportContext) -> None:
    text = json.dumps(run_payload(ctx), indent=2, sort_keys=True, ensure_ascii=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    experiments = out_dir / "experiments"
    experiments.mkdir(parents=True, exist_ok=True)
    (experiments / f"{ctx.experiment_id}.json").write_text(text + "\n", encoding="utf-8", newline="\n")
