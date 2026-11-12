"""Post-run evaluation against working labels (ADR-001 §7)."""

from __future__ import annotations

import csv
import json
import re
import statistics
from pathlib import Path

from map_qa import compare, models
from map_qa.inputs import load_inputs
from map_qa.models import SignReading
from map_qa.sources.local import LocalRoadsSource

_DETECTOR_SPEED = re.compile(r"^regulatory--maximum-speed-limit-(\d+)--")
# AC-45: how the models accepted the request, from the captures in tests/fixtures/captures (ADR-001 §4a).
_REQUEST_DIFFERENCES = (
    "Różnice w akceptacji żądania (z capture'ów, ADR-001 §4a):",
    "",
    "- Oba modele decyzyjne przyjęły to samo pytanie `choice` z 24 etykietami i ten sam `state` (tekst + obraz).",
    "- `typesafe/jev-1.14` odrzucił oryginalny wycinek 135×141 px i wersję 274×287 px (`400 Invalid image dimensions`); "
    "przyjął obraz 512×512 px. `openai/gpt-6-luna-decisions` przyjął też oryginalny mały JPEG.",
    "- Dlatego oba modele dostają ten sam obraz PNG przeskalowany do 512–1568 px na bok.",
    "- Oba modele działają jako BYOK: `usage.cost = 0`, koszt w `cost_details.upstream_inference_cost`.",
)
_THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)
_ACCEPTED_TIERS = {models.TIER_DECISION_MODEL, models.TIER_LLM}


def expected_map_outcomes(data_dir: Path) -> dict[str, int]:
    labels = _read_csv(data_dir / "signs" / "labels.csv", "labels.csv")
    bundle = load_inputs(data_dir, LocalRoadsSource(data_dir / "osm_roads.parquet"))
    signs = {sign.photo_id: sign for sign in bundle.signs}
    counts = {outcome: 0 for outcome in (models.MATCH, models.MISMATCH, models.MAP_MISSING, models.NOT_COMPARABLE)}
    for row in labels:
        value = row.get("sign_value", "")
        if not _numeric(value):
            continue
        photo_id = row.get("photo_id", "")
        sign = signs.get(photo_id)
        if sign is None:
            continue
        link = bundle.links[photo_id]
        tags = bundle.roads.get(link.osm_way_id) if link.osm_way_id is not None else None
        reading = SignReading(photo_id=photo_id, value=value, tier=models.TIER_HUMAN, model="human")
        outcome = compare.compare(sign, link, tags, reading).outcome
        if outcome != models.ABSTAIN:
            counts[outcome] += 1
    return counts


def evaluation_markdown(out_dir: Path, settings) -> str:
    run = _read_json(out_dir / "run.json")
    result_rows = _read_csv(out_dir / "results.csv", "results.csv")
    labels = {row["photo_id"]: row for row in _read_csv(settings.data_dir / "signs" / "labels.csv", "labels.csv")}
    detector_rows = {row["photo_id"]: row for row in _read_csv(settings.data_dir / "signs" / "signs.csv", "signs.csv")}
    results = {row["photo_id"]: row for row in result_rows}
    evaluated = set(results)
    measurable = {pid for pid in evaluated if _measurable(labels.get(pid))}
    numeric = {pid for pid in measurable if _numeric(labels[pid]["sign_value"])}
    negatives = {pid for pid in measurable if labels[pid]["sign_value"] == "none"}
    excluded = {pid: _exclude_reason(labels.get(pid)) for pid in evaluated if not _measurable(labels.get(pid))}
    answers = run.get("answers", [])
    stats = run.get("tier_stats", {})
    uncertain_count = sum((labels.get(pid) or {}).get("status") == "uncertain" for pid in excluded)
    unreadable_count = sum((labels.get(pid) or {}).get("sign_value") == "unreadable" for pid in excluded)
    lines = ["### Zbiór ewaluacyjny", "", f"Oceniane znaki: {len(evaluated)}; mierzalne etykiety: {len(measurable)}; numeryczne: {len(numeric)}; negatywne: {len(negatives)}; wykluczone: {len(excluded)} (niepewne: {uncertain_count}, nieczytelne: {unreadable_count}).", "", "Etykiety są roboczymi etykietami AI, a nie prawdą referencyjną (PRD §8).", "", "Wykluczone etykiety:"]
    if excluded:
        lines.extend(f"- {pid}: {reason}" for pid, reason in sorted(excluded.items()))
    else:
        lines.append("- 0 pozycji")
    lines += ["", "### Metryki poziomów", "", "Metryki zgodności: trafienia/liczba przyjętych odpowiedzi z mierzalną etykietą. Każda wartość ma mianownik.", "", "| Poziom | Wysłane | Niepowodzenia | Przyjęte | Eskalowane | Trafność numeryczna | Błędne akceptacje | Obsłużone negatywy | Żądania | Koszt USD | Koszt/przyjętą |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for tier, title, result_tier in ((1, "poziom 1", models.TIER_DECISION_MODEL), (2, "poziom 2", models.TIER_LLM)):
        tier_answers = [a for a in answers if a.get("tier") == tier]
        accepted_ids = {r["photo_id"] for r in result_rows if r.get("tier") == result_tier}
        lines.append(_tier_row(title, tier_answers, accepted_ids, results, labels, numeric, negatives, stats.get(f"tier{tier}", {})))
    chain = {r["photo_id"] for r in result_rows if r.get("tier") in _ACCEPTED_TIERS}
    # The chain is counted per sign: one pseudo-answer per sign that entered the cascade (its final value).
    chain_signs = sorted({a["photo_id"] for a in answers})
    if run.get("model_mode") == "off":
        chain, chain_signs = set(), []
    last_status = {}
    for a in answers:
        last_status[a["photo_id"]] = a.get("status")
    chain_answers = [{"photo_id": pid, "answer": results.get(pid, {}).get("value") or None, "status": last_status.get(pid)} for pid in chain_signs]
    chain_stats = {"sent": len(chain_signs), "accepted": len(chain), "escalated": len(set(chain_signs) - chain), "failed": sum(s == "model_unavailable" for pid, s in last_status.items() if pid in set(chain_signs) and pid not in chain), "requests": sum((v or {}).get("requests", 0) for v in stats.values()), "cost_usd": sum((v or {}).get("cost_usd", 0) or 0 for v in stats.values())}
    lines.append(_tier_row("łańcuch", chain_answers, chain, results, labels, numeric, negatives, chain_stats))
    detector_values = {pid: _detector_value((detector_rows.get(pid) or {}).get("mapillary_value", "")) for pid in evaluated}
    detector_correct, detector_false, detector_neg = _metric_counts(evaluated, detector_values, labels, measurable, numeric, negatives)
    lines.append(f"| detektor Mapillary (bazowy) | {len(evaluated)}/{len(evaluated)} | 0/{len(evaluated)} | {len(evaluated)}/{len(evaluated)} | 0/{len(evaluated)} | {_fraction(detector_correct, sum(pid in numeric for pid in evaluated))} | {_fraction(detector_false, sum(pid in measurable for pid in evaluated))} | {_fraction(detector_neg, sum(pid in negatives for pid in evaluated))} | 0/0 | 0 USD | — |")
    human = sum(r.get("tier") == models.TIER_HUMAN for r in result_rows)
    lines.append(f"| człowiek | {human}/{len(evaluated)} | — | — | — | — | — | — | — | — | — |")
    lines += ["", "### Cele MVP (PRD §12)"]
    if run.get("model_mode") == "off":
        lines += ["", "Nie dotyczy w trybie off."]
    else:
        chain_values = {pid: results[pid].get("value", "") for pid in chain}
        _, false_chain, _ = _metric_counts(chain, chain_values, labels, measurable, numeric, negatives)
        hard = {pid for pid in negatives if _numeric(_detector_value((detector_rows.get(pid) or {}).get("mapillary_value", "")))}
        hard_handled = sum(chain_values.get(pid) == "none" or not results.get(pid, {}).get("value") for pid in hard)
        unreadable_ids = {pid for pid in evaluated if (labels.get(pid) or {}).get("sign_value") == "unreadable"}
        unreadable_queue = sum(not results[pid].get("value") for pid in unreadable_ids)
        queue = sum(not r.get("value") for r in result_rows)
        request_count = sum((v or {}).get("requests", 0) for v in stats.values())
        lines += ["", _goal("Błędne akceptacje łańcucha ≤ 1", false_chain, 1, false_chain <= 1), _goal("Trudne przypadki obsłużone jako none lub eskalowane", hard_handled, len(hard), hard_handled >= 2 and len(hard) == 3), _goal("Nieczytelny znak w kolejce (jeśli w próbce)", unreadable_queue, len(unreadable_ids), not unreadable_ids or unreadable_queue == len(unreadable_ids)), _goal("Kolejka ≤ 40% ocenianych", queue, len(evaluated), queue <= len(evaluated) * .4), _goal("Żądania w tym uruchomieniu (ponowne uruchomienie cache: 0)", request_count, request_count, request_count == 0)]
    lines += ["", "### Eksploracja progu (AC-36, eksploracyjne)", "", "Analiza eksploracyjna; wyników nie należy raportować jako trafności.", "", "| Próg | Przyjęte | Błędne akceptacje |", "|---:|---:|---:|"]
    tier1 = [a for a in answers if a.get("tier") == 1 and a.get("probabilities")]
    if tier1:
        for threshold in _THRESHOLDS:
            chosen = {a["photo_id"]: a.get("answer") for a in tier1 if a.get("answer") != "unreadable" and float((a.get("probabilities") or {}).get(a.get("answer"), 0)) >= threshold}
            false = sum(pid in measurable and chosen[pid] != labels[pid]["sign_value"] for pid in chosen)
            lines.append(f"| {threshold:.2f} | {len(chosen)}/{len(tier1)} | {false}/{sum(pid in measurable for pid in chosen)} |")
    else:
        lines.append("0 pozycji")
    lines += ["", "### Porównanie modeli decyzyjnych (AC-45)", "", "| Eksperyment | Model poziomu 1 | Odpowiedziane/wysłane | Przyjęte | Trafność | Błędne akceptacje | Negatywy obsłużone | Odpowiedzi unreadable | Błędy | Mediana ms | Maks. ms | Koszt USD | Koszt/przyjętą |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    experiments = []
    exp_dir = out_dir / "experiments"
    if exp_dir.is_dir():
        for path in exp_dir.glob("*.json"):
            data = _read_json(path)
            exp_answers = [a for a in data.get("answers", []) if a.get("tier") == 1]
            if exp_answers:
                experiments.append((data.get("experiment_id", path.stem), data, exp_answers))
    for exp_id, exp, exp_answers in sorted(experiments, key=lambda x: x[0]):
        threshold = float(exp.get("threshold", 0.8))
        accepted = {a["photo_id"]: a.get("answer") for a in exp_answers if a.get("status") == "answered" and a.get("answer") != "unreadable" and (a.get("probabilities") or {}) and float((a.get("probabilities") or {}).get(a.get("answer"), 0)) >= threshold}
        correct, false, _ = _metric_counts(set(accepted), accepted, labels, measurable, numeric, negatives)
        neg_sent = {a["photo_id"] for a in exp_answers if a["photo_id"] in negatives}
        neg_handled = sum(a.get("answer") == "none" or a.get("status") != "answered" or a["photo_id"] not in accepted for a in exp_answers if a["photo_id"] in neg_sent)
        latencies = [a["latency_ms"] for a in exp_answers if isinstance(a.get("latency_ms"), (int, float))]
        costs = sum(a.get("cost_usd") or 0 for a in exp_answers)
        sent = len(exp_answers)
        lines.append(f"| {exp_id} | {exp.get('tiers', {}).get('tier1', {}).get('model', exp_answers[0].get('model', ''))} | {sum(a.get('status') == 'answered' for a in exp_answers)}/{sent} | {len(accepted)}/{sent} | {_fraction(correct, sum(pid in numeric for pid in accepted))} | {false}/{sum(pid in measurable for pid in accepted)} | {neg_handled}/{len(neg_sent)} | {sum(a.get('answer') == 'unreadable' for a in exp_answers)}/{sent} | {sum(a.get('status') == 'model_unavailable' for a in exp_answers)}/{sent} | {statistics.median(latencies) if latencies else '—'} | {max(latencies) if latencies else '—'} | {costs:.6f} | {f'{costs/len(accepted):.6f} USD' if accepted else '—'} |")
    if not experiments:
        lines.append("0 pozycji")
    lines += ["", *_REQUEST_DIFFERENCES, "", "### Kontrola logiki porównania (etykiety jako odczyty)", ""]
    required = ("signs/nearest_osm.csv", "sources.json", "osm_roads.parquet")
    if all((settings.data_dir / path).is_file() for path in required):
        lines += ["| Wynik | Liczba |", "|---|---:|"]
        counts = expected_map_outcomes(settings.data_dir)
        lines.extend(f"| {key} | {value} |" for key, value in counts.items())
    else:
        lines.append("0 pozycji")
    return "\n".join(lines)


def rebuild_report(out_dir: Path, settings) -> Path:
    from map_qa.export import replace_evaluation_section
    from map_qa.models import MapQaError

    report = out_dir / "report.md"
    if not report.is_file():
        raise MapQaError(f"evaluate: report not found: {report}")
    text = report.read_text(encoding="utf-8")
    report.write_text(replace_evaluation_section(text, evaluation_markdown(out_dir, settings)), encoding="utf-8", newline="\n")
    return report


def _read_csv(path: Path, dataset: str) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))
    except OSError as exc:
        raise models.MapQaError(f"{dataset}: cannot read {path.name}: {exc}") from exc


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise models.MapQaError(f"run: cannot read {path.name}: {exc}") from exc


def _numeric(value: str | None) -> bool:
    return bool(value and value.isdigit())


def _measurable(row: dict[str, str] | None) -> bool:
    return bool(row and row.get("status") == "certain" and row.get("sign_value") != "unreadable")


def _exclude_reason(row: dict[str, str] | None) -> str:
    if row is None:
        return "brak etykiety"
    if row.get("sign_value") == "unreadable":
        return "unreadable"
    return f"status {row.get('status', '')}"


def _detector_value(value: str | None) -> str:
    match = _DETECTOR_SPEED.match(value or "")
    return match.group(1) if match else "none"


def _fraction(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def _metric_counts(ids, values, labels, measurable, numeric, negatives):
    correct = sum(pid in numeric and values.get(pid) == labels[pid]["sign_value"] for pid in ids)
    false = sum(pid in measurable and values.get(pid) != labels[pid]["sign_value"] for pid in ids)
    neg = sum(pid in negatives and values.get(pid) == "none" for pid in ids)
    return correct, false, neg


def _tier_row(title, answers, accepted_ids, results, labels, numeric, negatives, stat):
    values = {pid: results[pid].get("value", "") for pid in accepted_ids if pid in results}
    measurable = {pid for pid in labels if _measurable(labels[pid])}
    correct, false, _ = _metric_counts(accepted_ids, values, labels, measurable, numeric, negatives)
    neg_sent = {a["photo_id"] for a in answers if a.get("photo_id") in negatives}
    neg_handled = sum(a.get("answer") == "none" or a.get("photo_id") not in accepted_ids for a in answers if a.get("photo_id") in neg_sent)
    accepted = len(accepted_ids)
    cost = float(stat.get("cost_usd", 0) or 0)
    requests = int(stat.get("requests", 0) or 0)
    return f"| {title} | {stat.get('sent', len(answers))}/{len(answers)} | {stat.get('failed', sum(a.get('status') == 'model_unavailable' for a in answers))}/{len(answers)} | {accepted}/{len(answers)} | {stat.get('escalated', max(0, len(answers)-accepted))}/{len(answers)} | {_fraction(correct, sum(pid in numeric for pid in accepted_ids))} | {false}/{sum(pid in labels and labels[pid].get('status') == 'certain' and labels[pid].get('sign_value') != 'unreadable' for pid in accepted_ids)} | {neg_handled}/{len(neg_sent)} | {requests}/{len(answers)} | {cost:.6f} USD | {f'{cost/accepted:.6f} USD' if accepted else '—'} |"


def _goal(name: str, n: int, d: int, met: bool) -> str:
    return f"- {name}: {'spełniony' if met else 'niespełniony'} ({n}/{d})."
