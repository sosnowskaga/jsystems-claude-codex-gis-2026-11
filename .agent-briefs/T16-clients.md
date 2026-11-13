# T16 — OpenRouter clients (tier backends)

Read `.agent-briefs/_common.md` first and follow it (this task does use HTTP, only through `httpx` and only mocked from the real captures listed below). Then read `docs/ADR/001-decision-cascade.md` §4, **§4a (capture findings)**, §5, §6, §9; PRD AC-08…AC-16; and the code you build on: `app/src/map_qa/cascade.py` (`TierBackend`, `ParsedAnswer`, `CallOutcome`), `app/src/map_qa/question.py` (`decisions_body`, `chat_body`, `QUESTION_KEY`), `app/src/map_qa/image.py`, `app/src/map_qa/models.py`, `app/tests/unit/test_cascade.py` (how the cascade uses backends).

## Editable files (only these)

- `app/src/map_qa/decisions_client.py` (new)
- `app/src/map_qa/llm_client.py` (new)
- `app/tests/unit/test_clients.py` (new)
- `app/tests/integration/test_live_cascade.py` (new)

Do not edit `cascade.py`, `pipeline.py`, `cli.py`, `question.py`, `image.py` (the orchestrator owns them; report needed changes). A concurrent worker edits `evaluate.py` and its tests.

## Captures (the only source of request and response shapes)

`app/tests/fixtures/captures/openrouter_decisions_jev.json`, `openrouter_decisions_luna.json`, `openrouter_chat_luna.json` (HTTP 200), `openrouter_decisions_jev_http400.json` (rejected request). Each has `request` {method, path, body with the image replaced by `<image sha256=…>`}, `status`, `headers`, `body` (unchanged response), `latency_ms`.

## API

```python
# decisions_client.py
class DecisionsBackend:            # implements cascade.TierBackend for tier 1 (model-agnostic: Jev and Luna Decisions)
    endpoint = "/api/alpha/decisions"
    def __init__(self, model: str, *, env: Mapping[str, str], timeout_s: float = 40.0,
                 transport: httpx.BaseTransport | None = None, base_url: str = "https://openrouter.ai",
                 clock: Callable[[], float] = time.monotonic): ...
    def call(self, image: bytes, mime_type: str) -> CallOutcome
    def parse(self, entry: dict) -> ParsedAnswer

# llm_client.py
class ChatBackend:                 # tier 2
    endpoint = "/api/v1/chat/completions"
    (same constructor and methods)

def error_category(status: int | None, timeout: bool = False) -> str   # shared helper (put it in decisions_client.py and import it in llm_client.py)
def usage_cost(usage: dict | None) -> float | None                      # shared helper, same place
```

Rules:
- `call`: body from `question.decisions_body(model, image, mime_type)` / `question.chat_body(...)`; one `POST` with `Authorization: Bearer <OPENROUTER_API_KEY read from env at call time>`, `Content-Type: application/json`; one `httpx.Client` per backend with `timeout=timeout_s`, `follow_redirects=False`, **no retries** (no transport retries, no loops). Measure latency in ms. HTTP 200 → `CallOutcome(entry={"endpoint", "http_status": 200, "response": <body json>, "usage": <body["usage"]>, "latency_ms": …}, error_category=None, latency_ms=…)`. Non-200 → `CallOutcome(entry=None, error_category=error_category(status), …)`. `httpx.TimeoutException` → `"timeout"`; other `httpx.HTTPError` → `"provider"`; a 200 whose body is not JSON → `entry=None`, `"parse_error"`. Missing key → raise `MapQaError("model mode 'live': environment variable OPENROUTER_API_KEY is not set")` (the pipeline checks earlier too). **The key never appears in any exception, entry, log or return value.** No request header besides Authorization and Content-Type is needed.
- `error_category` (ADR-001 §5): 400/413 → `bad_request`; 401/403 → `auth`; 402 → `payment`; 404 → `not_found`; 408/524 or timeout → `timeout`; 429 → `rate_limit`; 5xx/529 → `provider`; anything else → `provider`.
- `usage_cost` (§4a): `usage.cost + usage.cost_details.upstream_inference_cost` when `usage.is_byok` is true, else `usage.cost`; `None` when `usage` or `cost` is missing.
- `DecisionsBackend.parse(entry)`: from `entry["response"]["answers"][QUESTION_KEY]`: `answer` = `choice` (string), `probabilities` = the `probabilities` map with float values (do **not** substitute `confidence` when `probabilities` is missing: then `probabilities=None`), `cost_usd = usage_cost(response["usage"])`. Missing `answers`/question → `ParsedAnswer(None, None, cost, error_category="parse_error")`.
- `ChatBackend.parse(entry)`: `choices[0].message.content` is a JSON string `{"value": "<answer>"}`; `answer` = that value as a string; invalid JSON or missing `value` → `ParsedAnswer(None, ..., error_category="parse_error")`. Never return or store `reasoning`/`reasoning_details` in the ParsedAnswer. `probabilities=None`.

## Tests

- **Unit (`test_clients.py`)**, mocks from captures only:
  - request contract (TAC-001-02, AC-13): with an `httpx.MockTransport` that records the request and returns the capture's `status`+`body`, assert the sent body equals the capture's `request.body` once the data URL is replaced by the same `<image sha256=…>` marker (compute the sha of the bytes you pass — use `map_qa.image.model_image` on the real crop `course-materials/exercise-data/map-qa/signs/crops/mapillary_1022418992130552.jpg`, which is the crop of the captures); the body contains no `photo_id`, file name, `mapillary`, map value or label; tier-2 body contains no tier-1 field (AC-12); path equals the capture path; `Authorization` header present with the env value; the key string is absent from the returned entry.
  - parse each 200 capture: Jev → `30`, top p 0.99; Luna Decisions → `30`, p 0.36; Chat → `30`; costs per §4a (`7.8246e-05`, `9.27e-05`, `7.96e-05` when `cost` is 0 and BYOK).
  - the 400 capture → `CallOutcome(entry=None, error_category="bad_request")`.
  - status mapping table for `error_category` (status codes only; no invented bodies — use an empty body `b""` for statuses without a capture); timeout via a transport that raises `httpx.ReadTimeout` → `timeout`; exactly one request per `call` (TAC-001-01).
  - parse errors on modified copies of the capture bodies (remove `probabilities` → `probabilities is None`; content `"not json"` → `parse_error`).
- **Integration (`test_live_cascade.py`)**: build `Cascade(mode="live", …)` with your two backends on `MockTransport`s that replay capture bodies, a real `AnswerCache(tmp_path)` and `Ledger(tmp_path/"ledger.json", …)`, and the real crop above as a `Sign` (build it with `map_qa.inputs.load_inputs`, or construct `Sign` with the real path and `crop_sha256` of the crop file). Cases: Jev capture (p 0.99) → accepted tier 1, no tier-2 request; Luna Decisions capture as tier 1 (p 0.36) → escalated, tier 2 Chat capture → accepted `llm`; a 429 status on tier 1 → `model_unavailable:rate_limit`, one tier-2 request; second run in `mode="cache"` with the same cache → same readings and zero requests on both transports (AC-03, TAC-001-03); ledger attempts sum over two live runs (TAC-001-04); the cache files contain no `base64,` and no key (TAC-000-04, TAC-001-05). Note: the cascade sends `map_qa.image.model_image(crop_path)` bytes (PNG) and keys the cache by their SHA-256; your MockTransport replays the capture regardless of the image.

Verification: `uv run --frozen pytest tests/unit/test_clients.py tests/integration/test_live_cascade.py -q`.
