"""``review.csv``: read, validate, merge and write (ADR-004 §5, PRD §9.3, AC-29…AC-31)."""

from __future__ import annotations

import csv
import os
import tempfile
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from map_qa.models import (
    ANSWER_SET,
    CANDIDATE_OUTCOMES,
    REVIEW_COLUMNS,
    TIER_DECISION_MODEL,
    TIER_LLM,
    VERDICTS,
    MapQaError,
    ReviewRow,
    SignResult,
)

QUEUE = "queue"
CANDIDATE = "candidate"
ROW_TYPES: frozenset[str] = frozenset({QUEUE, CANDIDATE})
# machine columns refreshed from the current run (AC-29)
MACHINE_COLUMNS: tuple[str, ...] = ("osm_way_id", "tier1_answer", "tier2_answer", "outcome")


def read_review(path: Path) -> list[ReviewRow]:
    """Read and validate the review CSV. A missing file is an empty review, never an error.

    The first invalid row raises ``MapQaError`` (exit 2) and the file is never written here;
    values are stored exactly as read, but comparisons trim surrounding spaces.
    """
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        for column in REVIEW_COLUMNS:
            if column not in fieldnames:
                raise MapQaError(f"review.csv: missing column '{column}'")
        rows: list[ReviewRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(reader, start=1):
            data = {column: (raw.get(column) or "") for column in REVIEW_COLUMNS}
            row_id = data["row_id"]
            if row_id in seen:
                raise MapQaError(f"review.csv row {number} ({row_id}): duplicate row_id")
            seen.add(row_id)
            _validate(number, row_id, data)
            rows.append(ReviewRow(**data))
    return rows


def _validate(number: int, row_id: str, data: dict[str, str]) -> None:
    human_value = data["human_value"].strip()
    if human_value and human_value not in ANSWER_SET:
        raise MapQaError(
            f"review.csv row {number} ({row_id}): invalid human_value '{data['human_value']}'"
        )
    verdict = data["verdict"].strip()
    if verdict and verdict not in VERDICTS:
        raise MapQaError(f"review.csv row {number} ({row_id}): invalid verdict '{data['verdict']}'")
    if data["row_type"] not in ROW_TYPES:
        raise MapQaError(
            f"review.csv row {number} ({row_id}): invalid row_type '{data['row_type']}'"
        )


def human_values(rows: list[ReviewRow]) -> dict[str, str]:
    """``{photo_id: value}`` from queue rows with a non-empty human value, trimmed."""
    values: dict[str, str] = {}
    for row in rows:
        if row.row_type != QUEUE:
            continue
        value = row.human_value.strip()
        if value:
            values[row.photo_id] = value
    return values


def verdicts(rows: list[ReviewRow]) -> dict[str, str]:
    """``{photo_id: verdict}`` from candidate rows with a non-empty verdict, trimmed."""
    result: dict[str, str] = {}
    for row in rows:
        if row.row_type != CANDIDATE:
            continue
        verdict = row.verdict.strip()
        if verdict:
            result[row.photo_id] = verdict
    return result


def machine_rows(results: list[SignResult]) -> list[ReviewRow]:
    """Queue row for every sign without a model-accepted value, candidate row for every
    ``mismatch``/``map_missing`` (ADR-004 §5). Human columns are always empty; they are
    restored by :func:`merge`.
    """
    rows: list[ReviewRow] = []
    for result in results:
        photo_id = result.sign.photo_id
        reading = result.reading
        machine = {
            "photo_id": photo_id,
            "osm_way_id": "" if result.link.osm_way_id is None else str(result.link.osm_way_id),
            "tier1_answer": reading.tier1.answer if reading.tier1 and reading.tier1.answer else "",
            "tier2_answer": reading.tier2.answer if reading.tier2 and reading.tier2.answer else "",
            "outcome": result.comparison.outcome,
        }
        if reading.tier not in (TIER_DECISION_MODEL, TIER_LLM):
            rows.append(ReviewRow(row_id=ReviewRow.make_id(photo_id, QUEUE), row_type=QUEUE, **machine))
        if result.comparison.outcome in CANDIDATE_OUTCOMES:
            rows.append(
                ReviewRow(row_id=ReviewRow.make_id(photo_id, CANDIDATE), row_type=CANDIDATE, **machine)
            )
    return rows


def merge(existing: list[ReviewRow], current: list[ReviewRow]) -> list[ReviewRow]:
    """Merge the rows of the previous review with the rows of the current run (AC-29).

    Rows are never deleted. Existing rows keep their human columns byte for byte and get
    their machine columns refreshed from ``current``; rows only in ``current`` are added.
    Output is sorted by ``photo_id``, queue before candidate.
    """
    merged: dict[str, ReviewRow] = {row.row_id: row for row in existing}
    for row in current:
        previous = merged.get(row.row_id)
        if previous is None:
            merged[row.row_id] = row
        else:
            refresh = {column: getattr(row, column) for column in MACHINE_COLUMNS}
            merged[row.row_id] = replace(previous, **refresh)
    return sorted(merged.values(), key=lambda row: (row.photo_id, row.row_type != QUEUE))


def write_review(path: Path, rows: list[ReviewRow]) -> None:
    """Write the review CSV atomically: header ``REVIEW_COLUMNS``, UTF-8 (no BOM), ``\\n``.

    The rows go to a temporary file in the same folder first, then ``os.replace`` swaps it
    in, so a failure never leaves a half-written review behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: getattr(row, column) for column in REVIEW_COLUMNS})
        os.replace(temp_name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp_name)
        raise
