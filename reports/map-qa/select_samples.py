"""Select the MVP evaluation samples (PRD §12) from the labels and signs CSV files.

One-off script, not part of the application. Run from app/:

    uv run --frozen python ../reports/map-qa/select_samples.py

It writes app/samples/stage1.txt and app/samples/stage2.txt (overwrites only these two files) and prints
the selection. Selection is deterministic: candidates are sorted by (not Kraków, photo_id) and the first
candidate that fits the rule and the sequence/photo_id constraints is taken.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIGNS_DIR = ROOT / "course-materials" / "exercise-data" / "map-qa" / "signs"
SAMPLES_DIR = ROOT / "app" / "samples"
STAGE1_FILE = SAMPLES_DIR / "stage1.txt"
STAGE2_FILE = SAMPLES_DIR / "stage2.txt"

NUMERIC_VALUES = ("20", "30", "40", "50", "60", "70", "80", "90", "100", "110", "120")
SPEED_CLASS = re.compile(r"^regulatory--maximum-speed-limit-\d+--")
STAGE2_NUMERIC_SLOTS = 13


def fail(message: str) -> None:
    raise SystemExit(f"select_samples: {message}")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_candidates() -> list[dict[str, str]]:
    signs = {row["photo_id"]: row for row in read_rows(SIGNS_DIR / "signs.csv")}
    candidates: list[dict[str, str]] = []
    for label in read_rows(SIGNS_DIR / "labels.csv"):
        if label["status"] == "uncertain":
            continue
        sign = signs.get(label["photo_id"])
        if sign is None:
            fail(f"labels.csv photo_id {label['photo_id']} is not in signs.csv")
        candidates.append({**sign, **label})
    candidates.sort(key=lambda row: (row["area"] != "Kraków", row["photo_id"]))
    return candidates


def is_numeric(row: dict[str, str]) -> bool:
    return row["sign_value"] in NUMERIC_VALUES and not row["negative_type"]


class Picker:
    """Takes the first candidate that fits, keeping photo_ids and sequences unique across both stages."""

    def __init__(self, candidates: list[dict[str, str]]) -> None:
        self.candidates = candidates
        self.used_ids: set[str] = set()
        self.used_sequences: set[str] = set()

    def fits(self, row: dict[str, str]) -> bool:
        return row["photo_id"] not in self.used_ids and row["sequence"] not in self.used_sequences

    def take(self, row: dict[str, str]) -> dict[str, str]:
        self.used_ids.add(row["photo_id"])
        self.used_sequences.add(row["sequence"])
        return row

    def first(self, description: str, predicate) -> dict[str, str]:
        for row in self.candidates:
            if self.fits(row) and predicate(row):
                return self.take(row)
        fail(f"no candidate for {description}")
        raise AssertionError("unreachable")


def select() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    picker = Picker(load_candidates())

    # Stage 1: 3 numeric with 3 different values, 1 zone sign, 1 cancelled speed sign.
    stage1: list[dict[str, str]] = []
    seen_values: set[str] = set()
    for _ in range(3):
        row = picker.first(
            "stage 1 numeric with a new value",
            lambda r: is_numeric(r) and r["sign_value"] not in seen_values,
        )
        seen_values.add(row["sign_value"])
        stage1.append(row)
    stage1.append(picker.first("stage 1 zone sign", lambda r: r["negative_type"] == "zone" and r["status"] == "certain"))
    stage1.append(picker.first("stage 1 cancelled speed sign", lambda r: r["negative_type"] == "cancelled_sign"))

    # Stage 2 negatives, the unreadable sign and the speed-class false positive first (fewest candidates).
    stage2: list[dict[str, str]] = []
    stage2.append(picker.first("stage 2 cancelled speed sign", lambda r: r["negative_type"] == "cancelled_sign"))
    stage2.append(
        picker.first(
            "stage 2 false positive with a speed class",
            lambda r: r["negative_type"] == "false_positive_object" and bool(SPEED_CLASS.match(r["mapillary_value"])),
        )
    )
    stage2.append(picker.first("stage 2 zone end", lambda r: r["negative_type"] == "zone_end"))
    stage2.append(picker.first("stage 2 no entry", lambda r: r["negative_type"] == "no_entry"))
    stage2.append(
        picker.first("stage 2 weight limit", lambda r: r["negative_type"] == "weight" and r["status"] == "certain")
    )
    stage2.append(picker.first("stage 2 no speed sign", lambda r: r["negative_type"] == "no_speed_sign"))
    stage2.append(picker.first("stage 2 unreadable sign", lambda r: r["negative_type"] == "unreadable"))

    # Stage 2 numeric: every value not yet covered by stage 1, then fill the other slots,
    # always taking the least represented value (ties by the candidate order).
    numeric_counts: Counter[str] = Counter(row["sign_value"] for row in stage1 if is_numeric(row))
    stage2_numeric: list[dict[str, str]] = []
    for value in sorted((v for v in NUMERIC_VALUES if numeric_counts[v] == 0), key=int):
        row = picker.first(f"stage 2 numeric value {value}", lambda r, v=value: is_numeric(r) and r["sign_value"] == v)
        stage2_numeric.append(row)
        numeric_counts[value] += 1
    while len(stage2_numeric) < STAGE2_NUMERIC_SLOTS:
        pool = [row for row in picker.candidates if is_numeric(row) and picker.fits(row)]
        if not pool:
            fail("not enough numeric candidates for stage 2")
        best = min(enumerate(pool), key=lambda item: (numeric_counts[item[1]["sign_value"]], item[0]))[1]
        stage2_numeric.append(picker.take(best))
        numeric_counts[best["sign_value"]] += 1

    stage2 = stage2_numeric + stage2
    check(stage1, stage2)
    return stage1, stage2


def check(stage1: list[dict[str, str]], stage2: list[dict[str, str]]) -> None:
    if len(stage1) != 5 or len(stage2) != 20:
        fail(f"wrong sizes: stage1={len(stage1)} stage2={len(stage2)}")
    union = stage1 + stage2
    if len({row["photo_id"] for row in union}) != 25:
        fail("photo_id is not unique across the two stages")
    if len({row["sequence"] for row in union}) != 25:
        fail("more than one sign per sequence")
    numeric2 = [row for row in stage2 if is_numeric(row)]
    if len(numeric2) != STAGE2_NUMERIC_SLOTS:
        fail(f"stage 2 has {len(numeric2)} numeric signs, expected 13")
    covered = {row["sign_value"] for row in union if is_numeric(row)}
    if covered != set(NUMERIC_VALUES):
        fail(f"numeric values not covered: {sorted(set(NUMERIC_VALUES) - covered, key=int)}")


def write_list(path: Path, stage: str, rows: list[dict[str, str]]) -> None:
    header = f"# {stage}: selected by reports/map-qa/select_samples.py from signs/labels.csv (PRD §12)"
    body = "\n".join([header, *(row["photo_id"] for row in rows)]) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


def describe(stage: str, rows: list[dict[str, str]]) -> None:
    for row in rows:
        print(
            f"{stage} {row['photo_id']} label={row['sign_value']} "
            f"negative_type={row['negative_type'] or '-'} status={row['status']} "
            f"detector={row['mapillary_value'] or '-'} sequence={row['sequence']} area={row['area']}"
        )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    stage1, stage2 = select()
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    write_list(STAGE1_FILE, "stage1", stage1)
    write_list(STAGE2_FILE, "stage2", stage2)
    describe("stage1", stage1)
    describe("stage2", stage2)
    print(f"wrote {STAGE1_FILE} ({len(stage1)} ids) and {STAGE2_FILE} ({len(stage2)} ids)")


if __name__ == "__main__":
    main()
