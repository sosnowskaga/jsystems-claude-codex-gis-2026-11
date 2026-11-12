"""Live E2E, no mocks: real OpenRouter calls on the stage-1 sample, then a cache re-run (AC-03, AC-15…AC-18).

Uses its own experiment id and a temporary cache, so it never consumes the stage ledger (D4).
Needs OPENROUTER_API_KEY. Run with --run-live. Cost: at most 5 tier-1 + 5 tier-2 requests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import run_cli

pytestmark = pytest.mark.live


def _without_timestamp(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("Wygenerowano:"))


def test_stage1_live_then_cache_rerun(tmp_path: Path):
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY is not set")
    out, cache = tmp_path / "run", tmp_path / "cache"
    common = ["--sample", "stage1", "--out", str(out), "--cache-dir", str(cache), "--experiment-id", "e2e-live",
              "--max-requests-tier2", "5"]
    live = run_cli(["run", "--model-mode", "live", "--yes", *common])
    assert live.returncode == 0, live.stderr
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    stats = run["tier_stats"]
    assert 1 <= stats["tier1"]["requests"] <= 5 and stats["tier2"]["requests"] <= 5
    tier1 = [a for a in run["answers"] if a["tier"] == 1]
    assert len(tier1) == 5 and all(a["model"] == "typesafe/jev-1.14" for a in tier1)
    answered = [a for a in tier1 if a["status"] == "answered"]
    assert answered, "no tier-1 answer from the real endpoint"
    assert all(a["probabilities"] for a in answered)
    ledger = json.loads((cache / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["experiments"]["e2e-live"]["tier1"]["attempts"] == stats["tier1"]["requests"]
    report_live = (out / "report.md").read_text(encoding="utf-8")

    rerun = run_cli(["run", "--model-mode", "cache", *common])
    assert rerun.returncode == 0, rerun.stderr
    run2 = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert run2["tier_stats"]["tier1"]["requests"] == 0 and run2["tier_stats"]["tier2"]["requests"] == 0
    assert run2["tier_stats"]["tier1"]["cache_hits"] == len(answered)
    # AC-03 needs a complete cache: identical report apart from the time line and the mode/request lines
    readings = lambda r: sorted((a["photo_id"], a["tier"], a["answer"]) for a in r["answers"] if a["status"] == "answered")
    assert readings(run2) == readings(run)
    report_cache = (out / "report.md").read_text(encoding="utf-8")
    rerun2 = run_cli(["run", "--model-mode", "cache", *common])
    assert rerun2.returncode == 0, rerun2.stderr
    assert _without_timestamp((out / "report.md").read_text(encoding="utf-8")) == _without_timestamp(report_cache)
    assert "## Kolejka ręczna" in report_live

    key = os.environ["OPENROUTER_API_KEY"]
    for path in list(out.rglob("*")) + list(cache.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert key not in text and "base64," not in text and "data:image" not in text  # AC-04, TAC-000-04
