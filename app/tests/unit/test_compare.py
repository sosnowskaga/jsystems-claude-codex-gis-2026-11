from __future__ import annotations

import pytest

from map_qa import models as m
from map_qa.compare import abstain_reason, compare


def make_sign(area: str = m.KRAKOW) -> m.Sign:
    return m.Sign("photo-1", "Mapillary", area, "sequence-1", "crop.jpg", "photo.jpg",
                  "synthetic-sha", "creator", "CC-BY-SA", "https://example.test/photo", None)


def make_link(way: int | None = 1, flags: tuple[str, ...] = ()) -> m.RoadLink:
    return m.RoadLink("photo-1", way, "3.4", flags, 1)


def make_reading(value: str | None = "50") -> m.SignReading:
    return m.SignReading("photo-1", value, m.TIER_HUMAN if value else None, "human" if value else None)


@pytest.mark.parametrize(
    "value,tags,outcome,reason",
    [
        ("50", m.OsmSpeedTags(1, maxspeed="50"), m.MATCH, None),
        ("50", m.OsmSpeedTags(1, maxspeed=" 50 km/h "), m.MATCH, None),
        ("50", m.OsmSpeedTags(1, maxspeed="40"), m.MISMATCH, None),
        ("50", m.OsmSpeedTags(1), m.MAP_MISSING, None),
        ("50", m.OsmSpeedTags(1, maxspeed_forward="50"), m.NOT_COMPARABLE, m.NC_DIRECTIONAL_OR_CONDITIONAL),
        ("50", m.OsmSpeedTags(1, maxspeed="50", maxspeed_backward="50"), m.NOT_COMPARABLE, m.NC_DIRECTIONAL_OR_CONDITIONAL),
        ("50", m.OsmSpeedTags(1, maxspeed="50", maxspeed_conditional="110 @ (Mo-Fr 06:00-18:00)"), m.NOT_COMPARABLE, m.NC_DIRECTIONAL_OR_CONDITIONAL),
        ("50", m.OsmSpeedTags(1, implicit_tag="maxspeed:type", implicit_value="PL:rural"), m.NOT_COMPARABLE, m.NC_IMPLICIT_LIMIT),
        ("50", m.OsmSpeedTags(1, maxspeed="50", implicit_tag="maxspeed:type", implicit_value="PL:rural"), m.MATCH, None),
        (m.NONE, m.OsmSpeedTags(1, maxspeed_forward="50"), m.NOT_COMPARABLE, m.NC_SIGN_NONE),
        (m.UNREADABLE, m.OsmSpeedTags(1), m.NOT_COMPARABLE, m.NC_SIGN_UNREADABLE),
        ("50", m.OsmSpeedTags(1, maxspeed="30 mph"), m.NOT_COMPARABLE, m.NC_MAXSPEED_NOT_NUMERIC),
        ("50", m.OsmSpeedTags(1, maxspeed="50;70"), m.NOT_COMPARABLE, m.NC_MAXSPEED_NOT_NUMERIC),
        ("50", m.OsmSpeedTags(1, maxspeed="PL:urban"), m.NOT_COMPARABLE, m.NC_MAXSPEED_NOT_NUMERIC),
        ("50", m.OsmSpeedTags(1, maxspeed="abc"), m.NOT_COMPARABLE, m.NC_MAXSPEED_NOT_NUMERIC),
        (None, m.OsmSpeedTags(1, maxspeed_forward="50"), m.PENDING_HUMAN, None),
    ],
)
def test_comparison(value: str | None, tags: m.OsmSpeedTags, outcome: str, reason: str | None) -> None:
    assert compare(make_sign(), make_link(), tags, make_reading(value)) == m.Comparison(
        "photo-1", outcome, not_comparable_reason=reason
    )


def test_source_maxspeed_sign_is_not_an_implicit_limit() -> None:
    # The loader filters source:maxspeed=sign before creating OsmSpeedTags.
    assert compare(make_sign(), make_link(), m.OsmSpeedTags(1), make_reading()).outcome == m.MAP_MISSING


@pytest.mark.parametrize("value", ["50", None, m.NONE, m.UNREADABLE])
@pytest.mark.parametrize(
    "area,way,tags,flags,reason",
    [
        ("Warszawa", 1, None, (), "outside_krakow;road_not_in_osm"),
        (m.KRAKOW, 1, m.OsmSpeedTags(1, maxspeed="50"),
         ("ambiguous_candidates", "service_way"), "flag:ambiguous_candidates;flag:service_way"),
        (m.KRAKOW, None, m.OsmSpeedTags(1), (), "road_not_in_osm"),
        (m.KRAKOW, 1, None, (), "road_not_in_osm"),
        ("Warszawa", None, None, ("service_way",), "outside_krakow;road_not_in_osm;flag:service_way"),
        ("Warszawa", 1, m.OsmSpeedTags(1), (), "outside_krakow"),
    ],
)
def test_abstain_precedence(value: str | None, area: str, way: int | None,
                            tags: m.OsmSpeedTags | None, flags: tuple[str, ...], reason: str) -> None:
    sign, link = make_sign(area), make_link(way, flags)
    assert abstain_reason(sign, link, tags) == reason
    assert compare(sign, link, tags, make_reading(value)) == m.Comparison(
        "photo-1", m.ABSTAIN, abstain_reason=reason
    )


def test_trusted_link_has_no_abstain_reason() -> None:
    assert abstain_reason(make_sign(), make_link(), m.OsmSpeedTags(1)) is None
