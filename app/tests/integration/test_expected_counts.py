from __future__ import annotations

from map_qa.evaluate import expected_map_outcomes


def test_expected_map_outcomes_from_labeled_readings(data_dir):
    assert expected_map_outcomes(data_dir) == {
        "match": 13,
        "mismatch": 18,
        "map_missing": 5,
        "not_comparable": 9,
    }
