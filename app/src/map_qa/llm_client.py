"""Tier-2 Chat Completions backend; reasoning remains in the raw cache body only."""

from __future__ import annotations

import json

from map_qa.cascade import ParsedAnswer
from map_qa.decisions_client import DecisionsBackend, usage_cost
from map_qa.question import chat_body


class ChatBackend(DecisionsBackend):
    endpoint = "/api/v1/chat/completions"
    _body = staticmethod(chat_body)

    def parse(self, entry: dict) -> ParsedAnswer:
        response = entry.get("response", {})
        cost = usage_cost(response.get("usage"))
        try:
            content = response["choices"][0]["message"]["content"]
            answer = json.loads(content)["value"]
            if not isinstance(answer, str):
                raise ValueError("value must be a string")
        except (KeyError, IndexError, TypeError, ValueError):
            return ParsedAnswer(None, None, cost, error_category="parse_error")
        return ParsedAnswer(answer, None, cost)
