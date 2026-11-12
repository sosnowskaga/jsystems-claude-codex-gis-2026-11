"""Unit tests for ``map_qa.review`` (ADR-004 §5, PRD §9.3, AC-29…AC-31)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from map_qa.models import (
    REVIEW_COLUMNS,
    TIER_DECISION_MODEL,
    TIER_HUMAN,
    TIER_LLM,
    Comparison,
    MapQaError,
    ReviewRow,
    RoadLink,
    Sign,
    SignReading,
    SignResult,
    StageAnswer,
)
from map_qa.review import human_values, machine_rows, merge, read_review, verdicts, write_review


# --- helpers ------------------------------------------------------------------------------------


def _row(**overrides: object) -> ReviewRow:
    fields: dict[str, object] = {"row_id": "p1:queue", "row_type": "queue", "photo_id": "p1"}
    fields.update(overrides)
    return ReviewRow(**fields)  # type: ignore[arg-type]


def _raw(row_id: str, row_type: str, photo_id: str, **extra: str) -> dict[str, str]:
    row = {column: "" for column in REVIEW_COLUMNS}
    row.update(row_id=row_id, row_type=row_type, photo_id=photo_id)
    row.update(extra)
    return row


def _write_raw(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REVIEW_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _stage(photo_id: str, tier: int, answer: str) -> StageAnswer:
    return StageAnswer(
        photo_id=photo_id,
        tier=tier,
        model="test-model",
        question_version="b33-v1",
        image_sha256="0" * 64,
        status="answered",
        answer=answer,
    )


def _sign(photo_id: str) -> Sign:
    return Sign(
        photo_id=photo_id,
        source="Mapillary",
        area="Kraków",
        sequence="seq-1",
        crop_path=f"/crops/{photo_id}.jpg",
        photo_path=f"/photos/{photo_id}.jpg",
        crop_sha256="0" * 64,
        creator="tester",
        license="CC0",
        source_url="https://example.invalid/photo/1",
        mapillary_value=None,
    )


def _result(
    photo_id: str,
    tier: str | None,
    outcome: str,
    *,
    osm_way_id: int | None = 123,
    tier1_answer: str | None = None,
    tier2_answer: str | None = None,
) -> SignResult:
    reading = SignReading(
        photo_id=photo_id,
        value=None,
        tier=tier,
        model="human" if tier == TIER_HUMAN else ("test-model" if tier else None),
        tier1=_stage(photo_id, 1, tier1_answer) if tier1_answer else None,
        tier2=_stage(photo_id, 2, tier2_answer) if tier2_answer else None,
    )
    return SignResult(
        sign=_sign(photo_id),
        link=RoadLink(
            photo_id=photo_id,
            osm_way_id=osm_way_id,
            distance_m="4.0",
            match_flags=(),
            candidate_count=1,
        ),
        tags=None,
        reading=reading,
        comparison=Comparison(photo_id=photo_id, outcome=outcome),
    )


# --- round trip ---------------------------------------------------------------------------------


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    rows = [
        _row(
            row_id="b:queue",
            photo_id="b",
            osm_way_id="123",
            tier1_answer="50",
            outcome="pending_human",
            human_value=" 50 ",
            comment="odczyt ręczny",
        ),
        _row(
            row_id="a:candidate",
            row_type="candidate",
            photo_id="a",
            osm_way_id="456",
            tier1_answer="30",
            tier2_answer="30",
            outcome="mismatch",
            verdict="confirmed",
            comment='Kraków, inna jezdnia — komentarz "testowy", 50 km/h',
        ),
        _row(row_id="c:queue", photo_id="c", outcome="abstain"),
    ]

    write_review(path, rows)

    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), "review.csv must be UTF-8 without BOM"
    assert b"\r\n" not in data, "review.csv must use \\n line endings"
    assert data.split(b"\n", 1)[0].decode("utf-8") == ",".join(REVIEW_COLUMNS)
    assert read_review(path) == rows
    assert human_values(rows) == {"b": "50"}
    assert verdicts(rows) == {"a": "confirmed"}


def test_read_accepts_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    rows = [_row(row_id="p1:queue", photo_id="p1", outcome="pending_human")]
    write_review(path, rows)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    assert read_review(path) == rows


# --- validation (AC-31) -------------------------------------------------------------------------


def test_invalid_verdict_in_row_3_stops_and_leaves_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    _write_raw(
        path,
        [
            _raw("p1:queue", "queue", "p1", outcome="pending_human"),
            _raw("p2:candidate", "candidate", "p2", outcome="mismatch", verdict="confirmed"),
            _raw("p3:candidate", "candidate", "p3", outcome="map_missing", verdict="maybe"),
        ],
    )
    before = path.read_bytes()

    with pytest.raises(MapQaError) as excinfo:
        read_review(path)

    message = str(excinfo.value)
    assert "row 3" in message
    assert "p3:candidate" in message
    assert "'maybe'" in message
    assert "invalid verdict" in message
    assert path.read_bytes() == before


def test_invalid_human_value_rejected(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    _write_raw(
        path,
        [_raw("p1:queue", "queue", "p1", outcome="pending_human", human_value="55km")],
    )
    before = path.read_bytes()

    with pytest.raises(MapQaError) as excinfo:
        read_review(path)

    message = str(excinfo.value)
    assert "row 1" in message
    assert "p1:queue" in message
    assert "invalid human_value" in message
    assert "'55km'" in message
    assert path.read_bytes() == before


def test_valid_human_values_accepted_and_kept_as_read(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    values = ["50", " 50 ", "none", "unreadable", ""]
    rows = [
        _raw(f"p{i}:queue", "queue", f"p{i}", outcome="pending_human", human_value=value)
        for i, value in enumerate(values)
    ]
    rows.append(_raw("p9:candidate", "candidate", "p9", outcome="mismatch", human_value="70", verdict=" cannot_assess "))
    _write_raw(path, rows)

    parsed = read_review(path)

    assert parsed[1].human_value == " 50 "  # stored untrimmed, compared trimmed
    assert human_values(parsed) == {"p0": "50", "p1": "50", "p2": "none", "p3": "unreadable"}
    assert verdicts(parsed) == {"p9": "cannot_assess"}


def test_missing_column_reported(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    header = [column for column in REVIEW_COLUMNS if column != "outcome"]
    path.write_text(",".join(header) + "\n", encoding="utf-8")

    with pytest.raises(MapQaError) as excinfo:
        read_review(path)

    assert str(excinfo.value) == "review.csv: missing column 'outcome'"


def test_duplicate_row_id_reported(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    _write_raw(
        path,
        [
            _raw("p1:queue", "queue", "p1", outcome="pending_human"),
            _raw("p1:queue", "queue", "p1", outcome="abstain"),
        ],
    )

    with pytest.raises(MapQaError) as excinfo:
        read_review(path)

    assert str(excinfo.value) == "review.csv row 2 (p1:queue): duplicate row_id"


def test_missing_file_is_empty_review(tmp_path: Path) -> None:
    assert read_review(tmp_path / "does-not-exist.csv") == []


# --- merge (AC-29) ------------------------------------------------------------------------------


def test_merge_keeps_human_columns_refreshes_machine_and_never_deletes() -> None:
    comment_queue = "Kraków, Wielopole — odczyt ręczny, 50 km/h"
    comment_candidate = "znak dotyczy innej jezdni?"
    existing = [
        _row(
            row_id="p2:queue",
            photo_id="p2",
            osm_way_id="10",
            human_value=" 50 ",
            outcome="pending_human",
            comment=comment_queue,
        ),
        _row(
            row_id="p2:candidate",
            row_type="candidate",
            photo_id="p2",
            osm_way_id="10",
            outcome="mismatch",
            verdict="confirmed",
            comment=comment_candidate,
        ),
        _row(
            row_id="p1:queue",
            photo_id="p1",
            osm_way_id="77",
            human_value="none",
            outcome="pending_human",
            comment="stary komentarz",
        ),
    ]
    current = [
        _row(row_id="p2:queue", photo_id="p2", osm_way_id="99", tier1_answer="50", tier2_answer="50", outcome="not_comparable"),
        _row(
            row_id="p2:candidate",
            row_type="candidate",
            photo_id="p2",
            osm_way_id="99",
            tier1_answer="50",
            tier2_answer="50",
            outcome="map_missing",
        ),
        _row(row_id="p3:queue", photo_id="p3", osm_way_id="5", outcome="pending_human"),
    ]

    merged = merge(existing, current)
    by_id = {row.row_id: row for row in merged}

    assert set(by_id) == {"p1:queue", "p2:queue", "p2:candidate", "p3:queue"}
    # human columns copied byte for byte from existing
    assert by_id["p2:queue"].human_value == " 50 "
    assert by_id["p2:queue"].comment == comment_queue
    assert by_id["p2:candidate"].verdict == "confirmed"
    assert by_id["p2:candidate"].comment == comment_candidate
    # machine columns refreshed from current
    assert by_id["p2:queue"].osm_way_id == "99"
    assert by_id["p2:queue"].tier1_answer == "50"
    assert by_id["p2:queue"].tier2_answer == "50"
    assert by_id["p2:queue"].outcome == "not_comparable"
    assert by_id["p2:candidate"].outcome == "map_missing"
    # row absent from current (sign removed from input) is kept unchanged
    assert by_id["p1:queue"] == existing[2]
    # row only in current is added as is
    assert by_id["p3:queue"] == current[2]
    # sorted by photo_id, queue before candidate
    assert [row.row_id for row in merged] == ["p1:queue", "p2:queue", "p2:candidate", "p3:queue"]


def test_merge_sorts_by_photo_id_then_queue_before_candidate() -> None:
    existing = [
        _row(row_id="c:queue", photo_id="c"),
        _row(row_id="a:candidate", row_type="candidate", photo_id="a"),
        _row(row_id="b:queue", photo_id="b"),
    ]
    current = [
        _row(row_id="a:queue", photo_id="a"),
        _row(row_id="b:candidate", row_type="candidate", photo_id="b"),
    ]

    merged = merge(existing, current)

    assert [(row.photo_id, row.row_type) for row in merged] == [
        ("a", "queue"),
        ("a", "candidate"),
        ("b", "queue"),
        ("b", "candidate"),
        ("c", "queue"),
    ]


# --- machine rows -------------------------------------------------------------------------------


def test_machine_rows_model_accepted_sign_has_no_queue_row() -> None:
    result = _result("p1", TIER_DECISION_MODEL, "match", tier1_answer="50")

    assert machine_rows([result]) == []


def test_machine_rows_human_tier_sign_creates_queue_row() -> None:
    result = _result("p1", TIER_HUMAN, "pending_human", tier1_answer="50")

    rows = machine_rows([result])

    assert rows == [
        ReviewRow(
            row_id="p1:queue",
            row_type="queue",
            photo_id="p1",
            osm_way_id="123",
            tier1_answer="50",
            tier2_answer="",
            human_value="",
            outcome="pending_human",
            verdict="",
            comment="",
        )
    ]


def test_machine_rows_abstained_sign_creates_queue_row() -> None:
    result = _result("p2", None, "abstain", osm_way_id=None)

    rows = machine_rows([result])

    assert len(rows) == 1
    row = rows[0]
    assert row.row_id == "p2:queue"
    assert row.row_type == "queue"
    assert row.photo_id == "p2"
    assert row.osm_way_id == ""
    assert row.tier1_answer == "" and row.tier2_answer == ""
    assert row.outcome == "abstain"
    assert row.human_value == "" and row.verdict == "" and row.comment == ""


def test_machine_rows_mismatch_adds_candidate_row_too() -> None:
    human_mismatch = _result("p1", TIER_HUMAN, "mismatch", tier1_answer="50")
    rows = machine_rows([human_mismatch])

    assert [row.row_id for row in rows] == ["p1:queue", "p1:candidate"]
    assert rows[1].row_type == "candidate"
    assert rows[1].outcome == "mismatch"
    assert rows[1].osm_way_id == "123"
    assert rows[1].tier1_answer == "50"
    assert rows[1].human_value == "" and rows[1].verdict == "" and rows[1].comment == ""

    model_mismatch = _result("p2", TIER_LLM, "mismatch", tier2_answer="30")
    rows2 = machine_rows([model_mismatch])

    assert [row.row_id for row in rows2] == ["p2:candidate"]
    assert rows2[0].tier2_answer == "30"
