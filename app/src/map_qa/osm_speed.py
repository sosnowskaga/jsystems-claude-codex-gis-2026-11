"""Normalize OSM speed tags without deriving limits or converting units."""

from __future__ import annotations

import re
from dataclasses import replace

from map_qa import models as m


def parse_speed(source_tag: str, raw: str | None) -> list[m.SpeedRule]:
    if raw is None or not raw.strip():
        return []
    if ";" in raw:
        return [
            replace(rule, raw=raw, multi_value=True)
            for part in raw.split(";")
            for rule in parse_speed(source_tag, part)
        ]
    if "@" in raw:
        speed, condition = raw.split("@", 1)
        rules = parse_speed(source_tag, speed)
        value = rules[0].value_kmh if len(rules) == 1 and rules[0].kind == m.KIND_NUMERIC else None
        return [m.SpeedRule(source_tag, raw, m.KIND_CONDITIONAL,
                            value_kmh=value, condition_raw=condition.strip())]

    text = raw.strip()
    numeric = re.fullmatch(r"(\d+)\s*(?:km/h)?", text)
    if numeric:
        return [m.SpeedRule(source_tag, raw, m.KIND_NUMERIC, value_kmh=int(numeric.group(1)))]
    other_unit = re.fullmatch(r"\d+\s*([^\d\s].*)", text)
    if other_unit:
        return [m.SpeedRule(source_tag, raw, m.KIND_NUMERIC_OTHER_UNIT,
                            unit_raw=other_unit.group(1))]
    if re.match(r"[A-Za-z]{2}:", text):
        return [m.SpeedRule(source_tag, raw, m.KIND_IMPLICIT)]
    if text.lower() in {"none", "signals", "walk", "variable"}:
        return [m.SpeedRule(source_tag, raw, m.KIND_SPECIAL)]
    return [m.SpeedRule(source_tag, raw, m.KIND_UNPARSED)]


def normalize(tags: m.OsmSpeedTags) -> list[m.SpeedRule]:
    rules = [
        rule
        for source_tag, raw in (
            ("maxspeed", tags.maxspeed),
            ("maxspeed:forward", tags.maxspeed_forward),
            ("maxspeed:backward", tags.maxspeed_backward),
            ("maxspeed:conditional", tags.maxspeed_conditional),
        )
        for rule in parse_speed(source_tag, raw)
    ]
    if tags.implicit_tag is not None and tags.implicit_value is not None and tags.implicit_value.strip():
        rules.append(m.SpeedRule(tags.implicit_tag, tags.implicit_value, m.KIND_IMPLICIT))
    return rules
