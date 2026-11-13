"""Cache mode with an empty cache on the real files (AC-17, PRD §4.5 item 2).

The cache-hit path needs a real model response shape, so it is tested in T16 from captures.
"""

from __future__ import annotations

from pathlib import Path

from conftest import run_cli
from map_qa.config import resolve_settings
from map_qa.pipeline import RunOptions, execute


def test_cache_mode_with_empty_cache_sends_every_sign_to_the_queue(tmp_path: Path, no_network):
    settings = resolve_settings(config=None, source=None, source_osm_roads=None, model_mode="cache", env={})
    summary = execute(settings, RunOptions(out=tmp_path / "run", cache_dir=tmp_path / "cache", env={}))
    outcomes = [r.comparison.outcome for r in summary.ctx.results]
    assert outcomes.count("pending_human") == 61 and outcomes.count("abstain") == 58
    for r in summary.ctx.results:
        assert r.reading.value is None
        assert r.reading.escalation_reasons == ("tier1:cache_miss", "tier2:cache_miss")
        assert r.reading.tier1.status == "model_unavailable" and r.reading.tier1.error_category == "cache_miss"
    stats = summary.ctx.tier_stats
    assert stats["tier1"].sent == 119 and stats["tier1"].failed == 119 and stats["tier1"].requests == 0
    assert stats["tier2"].sent == 119 and stats["tier2"].cache_hits == 0
    assert not (tmp_path / "cache" / "ledger.json").exists()  # no attempt, no ledger write


def test_cache_mode_cli_opens_no_socket(tmp_path: Path, no_network_env):
    result = run_cli(
        ["run", "--model-mode", "cache", "--out", str(tmp_path / "run"), "--cache-dir", str(tmp_path / "cache"),
         "--sample", "mapillary_1029602998981114"],
        env=no_network_env,
    )
    assert result.returncode == 0, result.stderr
    assert "NetworkBlocked" not in result.stderr
