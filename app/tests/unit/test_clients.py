"""OpenRouter clients: contracts and responses derived from T15 captures only."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from map_qa.decisions_client import DecisionsBackend, error_category, usage_cost
from map_qa.image import model_image
from map_qa.llm_client import ChatBackend
from map_qa.models import MapQaError
from map_qa.question import QUESTION_KEY

ROOT = Path(__file__).resolve().parents[3]
CAPTURES = ROOT / "app/tests/fixtures/captures"
CROP = ROOT / "course-materials/exercise-data/map-qa/signs/crops/mapillary_1022418992130552.jpg"
KEY = "t16-test-secret"
CASES = [
    (DecisionsBackend, "openrouter_decisions_jev", 0.99, 7.8246e-05),
    (DecisionsBackend, "openrouter_decisions_luna", 0.36, 9.27e-05),
    (ChatBackend, "openrouter_chat_luna", None, 7.96e-05),
]


def capture(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("backend_type,name,probability,cost", CASES)
def test_capture_request_and_response(backend_type, name, probability, cost):
    recorded = capture(name)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(recorded["status"], json=recorded["body"])

    ticks = iter([1.0, 1.125, 2.0, 2.125])
    env = {"OPENROUTER_API_KEY": "old-key"}
    backend = backend_type(recorded["model"], env=env, transport=httpx.MockTransport(respond),
                           clock=lambda: next(ticks))
    env["OPENROUTER_API_KEY"] = KEY
    image, mime, sha = model_image(CROP)
    outcome = backend.call(image, mime)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == recorded["request"]["method"]
    assert request.url.path == recorded["request"]["path"]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    assert request.extensions["timeout"] == dict.fromkeys(["connect", "read", "write", "pool"], 40.0)
    body = json.loads(request.content)
    image_url = (body["state"][1] if "state" in body else body["messages"][0]["content"][1])["image_url"]
    assert image_url["url"].startswith(f"data:{mime};base64,")
    assert base64.b64decode(image_url["url"].split(",", 1)[1]) == image
    assert sha == hashlib.sha256(image).hexdigest()
    image_url["url"] = f"<image sha256={sha}>"
    assert body == recorded["request"]["body"]
    serialized = json.dumps(body).lower()
    for forbidden in ("photo_id", "mapillary", CROP.name.lower(), "map_value", "mapillary_value", "labels"):
        assert forbidden not in serialized
    if backend_type is ChatBackend:
        for forbidden in ("probabilities", "confidence", "tier1", "questions", "state"):
            assert forbidden not in serialized
    assert outcome.error_category is None
    assert outcome.latency_ms == 125
    assert outcome.entry == {"endpoint": backend.endpoint, "http_status": 200,
                             "response": recorded["body"], "usage": recorded["body"]["usage"], "latency_ms": 125}
    assert KEY not in repr(outcome)
    parsed = backend.parse(outcome.entry)
    assert parsed.answer == "30" and parsed.error_category is None
    assert parsed.cost_usd == pytest.approx(cost)
    if probability is None:
        assert parsed.probabilities is None
    else:
        assert parsed.probabilities["30"] == probability
        assert all(isinstance(value, float) for value in parsed.probabilities.values())
    assert "reasoning" not in json.dumps(asdict(parsed))
    env["OPENROUTER_API_KEY"] = "rotated-key"
    backend.call(image, mime)
    assert len(requests) == 2
    assert requests[1].headers["authorization"] == "Bearer rotated-key"


STATUS_CASES = [(400, "bad_request"), (413, "bad_request"), (401, "auth"), (403, "auth"),
                (402, "payment"), (404, "not_found"), (408, "timeout"), (524, "timeout"),
                (429, "rate_limit"), (500, "provider"), (503, "provider"), (529, "provider"),
                (302, "provider"), (418, "provider")]


@pytest.mark.parametrize("status,category", STATUS_CASES)
@pytest.mark.parametrize("backend_type", [DecisionsBackend, ChatBackend])
def test_http_error_one_attempt(status, category, backend_type):
    requests = []

    def respond(request):
        requests.append(request)
        if status == 400:
            recorded = capture("openrouter_decisions_jev_http400")
            return httpx.Response(recorded["status"], json=recorded["body"])
        return httpx.Response(status, content=b"", headers={"location": "https://openrouter.ai/redirect"})

    backend = backend_type("model", env={"OPENROUTER_API_KEY": KEY}, transport=httpx.MockTransport(respond))
    outcome = backend.call(b"image", "image/png")
    assert outcome.entry is None and outcome.error_category == category
    assert len(requests) == 1
    assert error_category(status) == category
    assert KEY not in repr(outcome)


@pytest.mark.parametrize("backend_type", [DecisionsBackend, ChatBackend])
@pytest.mark.parametrize("error,category", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "provider")])
def test_transport_error_is_safe_and_never_retried(backend_type, error, category):
    requests = []

    def fail(request):
        requests.append(request)
        raise error(KEY, request=request)

    backend = backend_type("model", env={"OPENROUTER_API_KEY": KEY}, transport=httpx.MockTransport(fail))
    outcome = backend.call(b"image", "image/png")
    assert outcome.entry is None and outcome.error_category == category
    assert len(requests) == 1 and KEY not in repr(outcome)
    assert error_category(None, timeout=True) == "timeout"
    assert error_category(None) == "provider"


@pytest.mark.parametrize("backend_type", [DecisionsBackend, ChatBackend])
def test_non_json_200_and_missing_key(backend_type):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"not json")

    env = {}
    backend = backend_type("model", env=env, transport=httpx.MockTransport(respond))
    with pytest.raises(MapQaError) as exc:
        backend.call(b"image", "image/png")
    assert str(exc.value) == "model mode 'live': environment variable OPENROUTER_API_KEY is not set"
    assert requests == []
    env["OPENROUTER_API_KEY"] = KEY
    outcome = backend.call(b"image", "image/png")
    assert outcome.entry is None and outcome.error_category == "parse_error" and len(requests) == 1


def test_usage_cost_from_capture_variants():
    usage = capture("openrouter_decisions_jev")["body"]["usage"]
    assert usage_cost(usage) == pytest.approx(7.8246e-05)
    usage["cost"] = 0.1
    assert usage_cost(usage) == pytest.approx(0.100078246)
    usage["is_byok"] = False
    assert usage_cost(usage) == 0.1
    del usage["cost"]
    assert usage_cost(usage) is None
    assert usage_cost(None) is None
    assert usage_cost({}) is None


@pytest.mark.parametrize("missing", ["probabilities", "answers", "question", "choice"])
def test_decisions_missing_fields(missing):
    recorded = capture("openrouter_decisions_jev")
    body = deepcopy(recorded["body"])
    if missing == "answers":
        del body["answers"]
    elif missing == "question":
        del body["answers"][QUESTION_KEY]
    else:
        del body["answers"][QUESTION_KEY][missing]
    parsed = DecisionsBackend(recorded["model"], env={}).parse({"response": body})
    assert parsed.cost_usd == pytest.approx(7.8246e-05)
    assert parsed.probabilities is None
    if missing == "probabilities":
        assert parsed.answer == "30" and parsed.error_category is None
    else:
        assert parsed.answer is None and parsed.error_category == "parse_error"


@pytest.mark.parametrize("mutation", ["invalid_json", "missing_value", "missing_choices", "empty_choices", "non_string"])
def test_chat_parse_errors(mutation):
    recorded = capture("openrouter_chat_luna")
    body = deepcopy(recorded["body"])
    if mutation == "missing_choices":
        del body["choices"]
    elif mutation == "empty_choices":
        body["choices"].clear()
    else:
        content = {"invalid_json": "not json", "missing_value": "{}", "non_string": '{"value":30}'}[mutation]
        body["choices"][0]["message"]["content"] = content
    parsed = ChatBackend(recorded["model"], env={}).parse({"response": body})
    assert parsed.answer is None and parsed.error_category == "parse_error"
    assert parsed.probabilities is None and parsed.cost_usd == pytest.approx(7.96e-05)
    assert "reasoning" not in json.dumps(asdict(parsed))
