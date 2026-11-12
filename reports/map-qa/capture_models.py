"""One-off capture of real OpenRouter responses for the three models (ADR-001 §5, T15).

Sends one request per model for one stage-1 crop, built from ``map_qa.question`` (b33-v1):
Jev 1.14 and Luna Decisions on ``/api/alpha/decisions``, GPT-6 Luna on ``/api/v1/chat/completions``.
Saves the accepted request (image replaced by ``<image sha256=…>``, no Authorization header) and the
response (status, whitelisted headers, unchanged body) to ``app/tests/fixtures/captures/openrouter_*.json``.

Run from ``app/``: ``uv run python ../reports/map-qa/capture_models.py [photo_id]``.
Requires OPENROUTER_API_KEY. No retries; every attempt is printed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from map_qa.image import model_image
from map_qa.question import chat_body, decisions_body

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "app" / "tests" / "fixtures" / "captures"
CROPS = ROOT / "course-materials" / "exercise-data" / "map-qa" / "signs" / "crops"
BASE = "https://openrouter.ai"
KEEP_HEADERS = {"content-type", "date"}
TARGETS = [
    ("openrouter_decisions_jev", "/api/alpha/decisions", "typesafe/jev-1.14", decisions_body),
    ("openrouter_decisions_luna", "/api/alpha/decisions", "openai/gpt-6-luna-decisions", decisions_body),
    ("openrouter_chat_luna", "/api/v1/chat/completions", "openai/gpt-6-luna", chat_body),
]


def redact_image(body: dict, image_sha: str) -> dict:
    text = json.dumps(body)
    start = text.find("data:image/")
    end = text.find('"', start)
    return json.loads(text[:start] + f"<image sha256={image_sha}>" + text[end:])


def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("missing variable: OPENROUTER_API_KEY")
        return 2
    photo_id = sys.argv[1] if len(sys.argv) > 1 else "mapillary_1022418992130552"
    crop = CROPS / f"{photo_id}.jpg"
    image, mime_type, image_sha = model_image(crop)  # normalized PNG, as the app sends it
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    client = httpx.Client(base_url=BASE, timeout=40.0, follow_redirects=False)
    for name, path, model, build in TARGETS:
        if only and name not in only:
            continue
        body = build(model, image, mime_type)
        started = time.monotonic()
        try:
            resp = client.post(path, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.TimeoutException:
            print(f"{name}: timeout")
            continue
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"_non_json_text": resp.text}
        capture = {
            "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "target": f"OpenRouter {path}",
            "model": model,
            "app_question_version": "b33-v1",
            "httpx": httpx.__version__,
            "latency_ms": latency_ms,
            "request": {"method": "POST", "path": path, "body": redact_image(body, image_sha)},
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS},
            "removed_header_names": sorted(k.lower() for k in resp.headers if k.lower() not in KEEP_HEADERS),
            "body": payload,
        }
        text = json.dumps(capture, indent=2, ensure_ascii=False)
        if key in text or "base64," in text:
            print(f"{name}: secret or image bytes found in capture, not saved")
            return 3
        suffix = "" if resp.status_code == 200 else f"_http{resp.status_code}"
        (OUT / f"{name}{suffix}.json").write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"{name}: HTTP {resp.status_code} in {latency_ms} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
