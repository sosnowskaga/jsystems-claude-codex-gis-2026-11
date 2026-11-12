from __future__ import annotations

import pytest

from map_qa import models as m
from map_qa.osm_speed import normalize, parse_speed


@pytest.mark.parametrize(
    "raw,kind,value,unit,condition",
    [
        ("50", m.KIND_NUMERIC, 50, None, None),
        ("50 km/h", m.KIND_NUMERIC, 50, None, None),
        (" \t50 km/h\n", m.KIND_NUMERIC, 50, None, None),
        ("0", m.KIND_NUMERIC, 0, None, None),
        ("30 mph", m.KIND_NUMERIC_OTHER_UNIT, None, "mph", None),
        ("20 knots", m.KIND_NUMERIC_OTHER_UNIT, None, "knots", None),
        (" 30 furlongs/hour ", m.KIND_NUMERIC_OTHER_UNIT, None, "furlongs/hour", None),
        ("PL:urban", m.KIND_IMPLICIT, None, None, None),
        ("PL:rural", m.KIND_IMPLICIT, None, None, None),
        ("PL:motorway", m.KIND_IMPLICIT, None, None, None),
        ("none", m.KIND_SPECIAL, None, None, None),
        ("walk", m.KIND_SPECIAL, None, None, None),
        (" SiGnAlS ", m.KIND_SPECIAL, None, None, None),
        ("VARIABLE", m.KIND_SPECIAL, None, None, None),
        ("60 @ (22:00-06:00)", m.KIND_CONDITIONAL, 60, None, "(22:00-06:00)"),
        ("110 @ (Mo-Fr 06:00-18:00)", m.KIND_CONDITIONAL, 110, None, "(Mo-Fr 06:00-18:00)"),
        ("none @ (wet)", m.KIND_CONDITIONAL, None, None, "(wet)"),
        ("abc", m.KIND_UNPARSED, None, None, None),
        ("-50", m.KIND_UNPARSED, None, None, None),
    ],
)
def test_parse_speed(raw: str, kind: str, value: int | None,
                     unit: str | None, condition: str | None) -> None:
    assert parse_speed("maxspeed", raw) == [
        m.SpeedRule("maxspeed", raw, kind, value, unit, condition)
    ]


@pytest.mark.parametrize("raw", [None, "", "  ", "\t\n"])
def test_empty_tag_has_no_rules(raw: str | None) -> None:
    assert parse_speed("maxspeed", raw) == []


@pytest.mark.parametrize(
    "raw,parts",
    [
        ("50;70", [(m.KIND_NUMERIC, 50, None, None), (m.KIND_NUMERIC, 70, None, None)]),
        (" 50 ; 30 mph ;none;abc;60 @ (wet) ", [
            (m.KIND_NUMERIC, 50, None, None),
            (m.KIND_NUMERIC_OTHER_UNIT, None, "mph", None),
            (m.KIND_SPECIAL, None, None, None),
            (m.KIND_UNPARSED, None, None, None),
            (m.KIND_CONDITIONAL, 60, None, "(wet)"),
        ]),
    ],
)
def test_multi_preserves_full_raw_and_each_part(raw: str, parts: list[tuple]) -> None:
    assert parse_speed("maxspeed:forward", raw) == [
        m.SpeedRule("maxspeed:forward", raw, kind, value, unit, condition, multi_value=True)
        for kind, value, unit, condition in parts
    ]


def test_normalize_preserves_tag_order_and_raw() -> None:
    tags = m.OsmSpeedTags(1, " 50 ", "60", "70", "80 @ (wet)", "maxspeed:type", " PL:rural ")
    assert normalize(tags) == [
        m.SpeedRule("maxspeed", " 50 ", m.KIND_NUMERIC, 50),
        m.SpeedRule("maxspeed:forward", "60", m.KIND_NUMERIC, 60),
        m.SpeedRule("maxspeed:backward", "70", m.KIND_NUMERIC, 70),
        m.SpeedRule("maxspeed:conditional", "80 @ (wet)", m.KIND_CONDITIONAL, 80, condition_raw="(wet)"),
        m.SpeedRule("maxspeed:type", " PL:rural ", m.KIND_IMPLICIT),
    ]


def test_normalize_absent_tags() -> None:
    assert normalize(m.OsmSpeedTags(1)) == []
