"""Capture-backed OpenRouter Decisions client, without retries (ADR-001 §4a–5)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time

import httpx

from map_qa.cascade import CallOutcome, ParsedAnswer
from map_qa.models import MapQaError
from map_qa.question import QUESTION_KEY, decisions_body


def error_category(status: int | None, timeout: bool = False) -> str:
    if timeout or status in (408, 524):
        return "timeout"
    return {400: "bad_request", 413: "bad_request", 401: "auth", 403: "auth",
            402: "payment", 404: "not_found", 429: "rate_limit"}.get(status, "provider")


def usage_cost(usage: dict | None) -> float | None:
    if not usage or usage.get("cost") is None:
        return None
    cost = float(usage["cost"])
    if usage.get("is_byok"):
        cost += float((usage.get("cost_details") or {}).get("upstream_inference_cost") or 0)
    return cost


class DecisionsBackend:
    endpoint = "/api/alpha/decisions"
    _body = staticmethod(decisions_body)

    def __init__(
        self, model: str, *, env: Mapping[str, str], timeout_s: float = 40.0,
        transport: httpx.BaseTransport | None = None, base_url: str = "https://openrouter.ai",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self._env = env
        self._clock = clock
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout_s, follow_redirects=False,
            transport=transport if transport is not None else httpx.HTTPTransport(retries=0),
        )

    def call(self, image: bytes, mime_type: str) -> CallOutcome:
        key = self._env.get("OPENROUTER_API_KEY")
        if not key:
            raise MapQaError("model mode 'live': environment variable OPENROUTER_API_KEY is not set")
        started = self._clock()
        entry = None
        category = None
        try:
            response = self._client.post(
                self.endpoint, json=self._body(self.model, image, mime_type),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            if response.status_code != 200:
                category = error_category(response.status_code)
            else:
                try:
                    body = response.json()
                except ValueError:
                    category = "parse_error"
                else:
                    if isinstance(body, dict):
                        entry = {"endpoint": self.endpoint, "http_status": 200,
                                 "response": body, "usage": body.get("usage")}
                    else:
                        category = "parse_error"
        except httpx.TimeoutException:
            category = "timeout"
        except httpx.HTTPError:
            category = "provider"
        latency_ms = round((self._clock() - started) * 1000)
        if entry is not None:
            entry["latency_ms"] = latency_ms
        return CallOutcome(entry=entry, error_category=category, latency_ms=latency_ms)

    def parse(self, entry: dict) -> ParsedAnswer:
        response = entry.get("response", {})
        cost = usage_cost(response.get("usage"))
        try:
            answer = response["answers"][QUESTION_KEY]
            choice = answer["choice"]
            if not isinstance(choice, str):
                raise ValueError("choice must be a string")
            probabilities = answer.get("probabilities")
            if probabilities is not None:
                probabilities = {label: float(value) for label, value in probabilities.items()}
        except (KeyError, TypeError, ValueError, AttributeError):
            return ParsedAnswer(None, None, cost, error_category="parse_error")
        return ParsedAnswer(choice, probabilities, cost)
