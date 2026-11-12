"""Compare accepted sign readings with OSM, preserving abstention precedence."""

from __future__ import annotations

from map_qa import models as m
from map_qa.osm_speed import parse_speed


def abstain_reason(sign: m.Sign, link: m.RoadLink, tags: m.OsmSpeedTags | None) -> str | None:
    reasons = []
    if sign.area != m.KRAKOW:
        reasons.append(m.ABSTAIN_OUTSIDE_KRAKOW)
    if tags is None or link.osm_way_id is None:
        reasons.append(m.ABSTAIN_ROAD_NOT_IN_OSM)
    reasons.extend(m.ABSTAIN_FLAG_PREFIX + flag for flag in link.match_flags)
    return ";".join(reasons) or None


def compare(sign: m.Sign, link: m.RoadLink, tags: m.OsmSpeedTags | None,
            reading: m.SignReading) -> m.Comparison:
    reason = abstain_reason(sign, link, tags)
    if reason is not None:
        return m.Comparison(sign.photo_id, m.ABSTAIN, abstain_reason=reason)
    if reading.value is None:
        return m.Comparison(sign.photo_id, m.PENDING_HUMAN)
    if reading.value == m.NONE:
        return m.Comparison(sign.photo_id, m.NOT_COMPARABLE, not_comparable_reason=m.NC_SIGN_NONE)
    if reading.value == m.UNREADABLE:
        return m.Comparison(sign.photo_id, m.NOT_COMPARABLE, not_comparable_reason=m.NC_SIGN_UNREADABLE)

    assert tags is not None  # Missing roads have already abstained.
    if any(raw is not None for raw in (tags.maxspeed_forward, tags.maxspeed_backward, tags.maxspeed_conditional)):
        return m.Comparison(sign.photo_id, m.NOT_COMPARABLE,
                            not_comparable_reason=m.NC_DIRECTIONAL_OR_CONDITIONAL)
    if tags.maxspeed is not None:
        rules = parse_speed("maxspeed", tags.maxspeed)
        if len(rules) == 1 and rules[0].kind == m.KIND_NUMERIC:
            outcome = m.MATCH if rules[0].value_kmh == int(reading.value) else m.MISMATCH
            return m.Comparison(sign.photo_id, outcome)
        return m.Comparison(sign.photo_id, m.NOT_COMPARABLE,
                            not_comparable_reason=m.NC_MAXSPEED_NOT_NUMERIC)
    if tags.implicit_tag is not None:
        return m.Comparison(sign.photo_id, m.NOT_COMPARABLE, not_comparable_reason=m.NC_IMPLICIT_LIMIT)
    return m.Comparison(sign.photo_id, m.MAP_MISSING)
