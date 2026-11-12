"""Question contract ``b33-v1`` (ADR-001 §4): the only text and image that reach a model.

Request bodies contain the crop image and this fixed question only: no file name, photo_id,
map value, detector value, label, area or date (AC-13). Any change here bumps QUESTION_VERSION.
"""

from __future__ import annotations

import base64

from map_qa.models import ANSWER_SET, NONE, QUESTION_VERSION, SPEED_VALUES, UNREADABLE

assert QUESTION_VERSION == "b33-v1"

QUESTION_KEY = "b33_value"
STATE_TEXT = "Street-level photo crop."
INSTRUCTIONS = (
    "Look at the round prohibitory speed-limit sign (Polish sign B-33: a white disc with a red border "
    "and a black number) in this street-level photo crop. Which speed limit value in km/h does it show? "
    "Answer 'none' if the crop does not show such a sign, for example a speed-limit zone sign, the end of "
    "a zone or of a speed limit, another sign, or no sign at all. Answer 'unreadable' if such a sign is "
    "present but its number cannot be read."
)

CRITERIA: dict[str, str] = {
    **{str(v): f"A B-33 speed-limit sign showing {v} km/h." for v in SPEED_VALUES},
    NONE: "No B-33 speed-limit sign: a zone sign, end of zone, end of limit, another sign, or no sign.",
    UNREADABLE: "A B-33 speed-limit sign is present but its value cannot be read.",
}
assert tuple(CRITERIA) == ANSWER_SET

TIER2_ANSWER_INSTRUCTION = (
    "Reply with JSON only, in the form {\"value\": \"<answer>\"}, where <answer> is one of: "
    + ", ".join(ANSWER_SET)
    + "."
)

TIER2_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string", "enum": list(ANSWER_SET)}},
    "required": ["value"],
    "additionalProperties": False,
}

TIER2_MAX_TOKENS = 4000


def data_url(image: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"


def decisions_body(model: str, image: bytes, mime_type: str) -> dict:
    """Tier-1 body for ``POST /api/alpha/decisions``: one ``choice`` question; state = [text, image]."""
    return {
        "model": model,
        "state": [STATE_TEXT, {"type": "image_url", "image_url": {"url": data_url(image, mime_type)}}],
        "questions": {
            QUESTION_KEY: {"type": "choice", "instructions": INSTRUCTIONS, "criteria": dict(CRITERIA)},
        },
    }


def chat_body(model: str, image: bytes, mime_type: str) -> dict:
    """Tier-2 body for ``POST /api/v1/chat/completions``: same image and question, no tier-1 answer."""
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": INSTRUCTIONS + "\n\n" + TIER2_ANSWER_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": data_url(image, mime_type)}},
                ],
            }
        ],
        "reasoning": {"effort": "medium"},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": QUESTION_KEY, "strict": True, "schema": TIER2_SCHEMA},
        },
        "max_tokens": TIER2_MAX_TOKENS,
    }
