"""Evaluation samples (PRD §12, ADR-000 §8): fixed identifier lists checked against the real labels and signs.

The lists are selected once, outside the application, by ``reports/map-qa/select_samples.py``. These tests
read the real ``signs/labels.csv`` and ``signs/signs.csv`` (allowed here: evaluation-only check) and assert the
selection criteria, so an edited list that breaks the stage rules fails.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from conftest import DATA_DIR

APP_DIR = Path(__file__).resolve().parents[2]
SAMPLES_DIR = APP_DIR / "samples"
SIGNS_CSV = DATA_DIR / "signs" / "signs.csv"
LABELS_CSV = DATA_DIR / "signs" / "labels.csv"

SPEED_CLASS = re.compile(r"^regulatory--maximum-speed-limit-\d+--")
NUMERIC_VALUES = {"20", "30", "40", "50", "60", "70", "80", "90", "100", "110", "120"}
STAGE2_NEGATIVES = Counter(
    {
        "cancelled_sign": 1,
        "false_positive_object": 1,
        "zone_end": 1,
        "no_entry": 1,
        "weight": 1,
        "no_speed_sign": 1,
    }
)


def read_ids(name: str) -> list[str]:
    raw = (SAMPLES_DIR / name).read_bytes()
    assert b"\r" not in raw, f"{name} must use LF line endings"
    lines = raw.decode("utf-8").split("\n")
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_labels() -> dict[str, dict[str, str]]:
    rows = read_csv_rows(LABELS_CSV)
    return {row["photo_id"]: row for row in rows}


def load_signs() -> dict[str, dict[str, str]]:
    rows = read_csv_rows(SIGNS_CSV)
    return {row["photo_id"]: row for row in rows}


def is_numeric(label: dict[str, str]) -> bool:
    return label["sign_value"] in NUMERIC_VALUES and not label["negative_type"]


def test_list_sizes_are_5_and_20():
    assert len(read_ids("stage1.txt")) == 5
    assert len(read_ids("stage2.txt")) == 20


def test_ids_are_unique_and_disjoint_between_stages():
    stage1 = read_ids("stage1.txt")
    stage2 = read_ids("stage2.txt")
    assert len(set(stage1)) == len(stage1)
    assert len(set(stage2)) == len(stage2)
    assert not set(stage1) & set(stage2)


def test_every_id_exists_in_signs_csv():
    signs = load_signs()
    for photo_id in read_ids("stage1.txt") + read_ids("stage2.txt"):
        assert photo_id in signs, f"{photo_id} is not in signs.csv"


def test_no_uncertain_label_is_selected():
    labels = load_labels()
    for photo_id in read_ids("stage1.txt") + read_ids("stage2.txt"):
        assert labels[photo_id]["status"] != "uncertain", f"{photo_id} has an uncertain label"


def test_at_most_one_sign_per_sequence_across_both_stages():
    signs = load_signs()
    sequences = [signs[photo_id]["sequence"] for photo_id in read_ids("stage1.txt") + read_ids("stage2.txt")]
    duplicates = [seq for seq, count in Counter(sequences).items() if count > 1]
    assert not duplicates, f"sequences with more than one sign: {duplicates}"


def test_stage1_composition():
    labels = load_labels()
    stage1 = [labels[photo_id] for photo_id in read_ids("stage1.txt")]

    numeric = [label for label in stage1 if is_numeric(label)]
    assert len(numeric) == 3
    assert len({label["sign_value"] for label in numeric}) == 3

    zones = [label for label in stage1 if label["negative_type"] == "zone" and label["status"] == "certain"]
    assert len(zones) == 1

    cancelled = [label for label in stage1 if label["negative_type"] == "cancelled_sign"]
    assert len(cancelled) == 1


def test_stage2_composition():
    labels = load_labels()
    stage2 = [labels[photo_id] for photo_id in read_ids("stage2.txt")]

    numeric = [label for label in stage2 if is_numeric(label)]
    assert len(numeric) == 13

    unreadable = [label for label in stage2 if label["negative_type"] == "unreadable"]
    assert len(unreadable) == 1
    assert unreadable[0]["sign_value"] == "unreadable"

    negatives = Counter(
        label["negative_type"] for label in stage2 if label["negative_type"] and label["negative_type"] != "unreadable"
    )
    assert negatives == STAGE2_NEGATIVES

    weight = [label for label in stage2 if label["negative_type"] == "weight"]
    assert all(label["status"] == "certain" for label in weight)

    assert len(numeric) + len(unreadable) + sum(negatives.values()) == 20


def test_stage2_false_positive_has_speed_class():
    labels = load_labels()
    signs = load_signs()
    fps = [
        photo_id
        for photo_id in read_ids("stage2.txt")
        if labels[photo_id]["negative_type"] == "false_positive_object"
    ]
    assert len(fps) == 1
    assert SPEED_CLASS.match(signs[fps[0]]["mapillary_value"]), signs[fps[0]]["mapillary_value"]


def test_hard_cases_are_in_the_union():
    labels = load_labels()
    signs = load_signs()
    union = read_ids("stage1.txt") + read_ids("stage2.txt")

    cancelled = [pid for pid in union if labels[pid]["negative_type"] == "cancelled_sign"]
    assert len(cancelled) == 2

    fp = [
        pid
        for pid in union
        if labels[pid]["negative_type"] == "false_positive_object"
        and SPEED_CLASS.match(signs[pid]["mapillary_value"])
    ]
    assert len(fp) == 1

    unreadable = [pid for pid in union if labels[pid]["negative_type"] == "unreadable"]
    assert len(unreadable) == 1
    assert SPEED_CLASS.match(signs[unreadable[0]]["mapillary_value"]), signs[unreadable[0]]["mapillary_value"]


def test_union_covers_every_distinct_numeric_label_value():
    labels = load_labels()
    union = read_ids("stage1.txt") + read_ids("stage2.txt")
    all_numeric_values = {label["sign_value"] for label in labels.values() if is_numeric(label) and label["status"] != "uncertain"}
    assert len(all_numeric_values) == 11
    covered = {labels[pid]["sign_value"] for pid in union if is_numeric(labels[pid])}
    assert all_numeric_values <= covered, f"not covered: {sorted(all_numeric_values - covered, key=int)}"
