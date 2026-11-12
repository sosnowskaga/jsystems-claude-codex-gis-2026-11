"""Source selection through the real CLI without network (AC-38, AC-42, AC-44)."""

from __future__ import annotations

from pathlib import Path

from conftest import run_cli

DB_VARS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_SQL_WAREHOUSE_ID")


def test_dry_run_shows_databricks_source_chosen_by_option(tmp_path: Path, no_network_env):
    result = run_cli(["run", "--dry-run", "--source-osm-roads", "databricks", "--out", str(tmp_path)], env=no_network_env)
    assert result.returncode == 0, result.stderr
    assert "osm_roads: databricks workspace.map_qa.osm_roads (wybrane przez: option)" in result.stdout
    assert "signs: local" in result.stdout


def test_env_selects_databricks_and_missing_token_stops_before_any_query(tmp_path: Path, no_network_env):
    env = {k: v for k, v in no_network_env.items() if k not in DB_VARS}
    env.update(MAPQA_SOURCE="databricks", DATABRICKS_HOST="https://example.invalid", DATABRICKS_SQL_WAREHOUSE_ID="abc")
    result = run_cli(["run", "--out", str(tmp_path / "run")], env=env)
    assert result.returncode == 2
    assert "missing_variable" in result.stderr and "DATABRICKS_TOKEN" in result.stderr
    assert "NetworkBlocked" not in result.stderr  # stopped before any connection attempt
    assert not (tmp_path / "run" / "report.md").exists()
