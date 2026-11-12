"""In-process off-mode run on the real files (TAC-000-02, AC-17, AC-44, PRD §4.7 item 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import run_cli
from map_qa.config import resolve_settings
from map_qa.pipeline import RunOptions, execute


def _settings(env: dict[str, str], mode: str | None = None):
    return resolve_settings(config=None, source=None, source_osm_roads=None, model_mode=mode, env=env)


def test_off_run_makes_no_request_and_never_touches_the_cache(tmp_path: Path, no_network):
    cache = tmp_path / "cache"
    cache.mkdir()
    sentinel = cache / "answers"  # a cache folder that must not be read or created in off mode
    summary = execute(_settings({}), RunOptions(out=tmp_path / "run", cache_dir=cache, env={}))
    assert len(summary.ctx.results) == 119
    assert not sentinel.exists()
    assert list(cache.iterdir()) == []
    stats = summary.ctx.tier_stats
    assert stats["tier1"].requests == 0 and stats["tier2"].requests == 0
    report = summary.paths["report"].read_text(encoding="utf-8")
    assert "## Ewaluacja\n\n0 pozycji" in report


def test_untrusted_link_with_human_value_stays_abstain_and_keeps_queue_row(tmp_path: Path, no_network):
    out = tmp_path / "run"
    execute(_settings({}), RunOptions(out=out, env={}))
    review = (out / "review.csv").read_text(encoding="utf-8").splitlines()
    target = "mapillary_1109576507471869:queue"
    idx = next(i for i, line in enumerate(review) if line.startswith(target))
    cols = review[idx].split(",")
    cols[6] = "50"
    review[idx] = ",".join(cols)
    (out / "review.csv").write_text("\n".join(review) + "\n", encoding="utf-8")
    summary = execute(_settings({}), RunOptions(out=out, env={}))
    result = next(r for r in summary.ctx.results if r.sign.photo_id == "mapillary_1109576507471869")
    assert result.comparison.outcome == "abstain"
    assert result.reading.tier == "human"
    assert target in (out / "review.csv").read_text(encoding="utf-8")


def test_sample_restricts_the_run(tmp_path: Path, no_network):
    ids = "mapillary_1029602998981114,mapillary_1071696194278486"
    summary = execute(_settings({}), RunOptions(out=tmp_path / "run", sample=ids, env={}))
    assert sorted(r.sign.photo_id for r in summary.ctx.results) == sorted(ids.split(","))


def test_unknown_sample_id_exits_2(tmp_path: Path, no_network_env):
    result = run_cli(["run", "--out", str(tmp_path), "--sample", "not_a_photo"], env=no_network_env)
    assert result.returncode == 2
    assert "not_a_photo" in result.stderr


@pytest.mark.parametrize("key", [None, "present"])
def test_live_mode_checks_key_then_reports_not_implemented(tmp_path: Path, no_network_env, key):
    env = {k: v for k, v in no_network_env.items() if k != "OPENROUTER_API_KEY"}
    if key:
        env["OPENROUTER_API_KEY"] = "test-key-value"
    result = run_cli(["run", "--model-mode", "live", "--out", str(tmp_path)], env=env)
    assert result.returncode == 2
    if key:
        assert "not implemented" in result.stderr
        assert "test-key-value" not in result.stderr + result.stdout
    else:
        assert "OPENROUTER_API_KEY" in result.stderr
    assert not (tmp_path / "report.md").exists()
