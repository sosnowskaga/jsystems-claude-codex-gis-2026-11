"""Live E2E, no mocks: osm_roads from Databricks gives the same results as local files (AC-39, TAC-002-01).

Needs DATABRICKS_HOST and DATABRICKS_TOKEN in the environment; the warehouse id comes from
app/config.example.json unless DATABRICKS_SQL_WAREHOUSE_ID is set. Run with --run-live.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import APP_DIR, run_cli

pytestmark = pytest.mark.live


def test_databricks_osm_roads_parity(tmp_path: Path):
    for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        if not os.environ.get(name):
            pytest.skip(f"{name} is not set")
    config = str(APP_DIR / "config.example.json")
    local = run_cli(["run", "--config", config, "--out", str(tmp_path / "local")])
    remote = run_cli(["run", "--config", config, "--source-osm-roads", "databricks", "--out", str(tmp_path / "db")])
    assert local.returncode == 0, local.stderr
    assert remote.returncode == 0, remote.stderr
    assert (tmp_path / "local" / "results.csv").read_bytes() == (tmp_path / "db" / "results.csv").read_bytes()
    run = json.loads((tmp_path / "db" / "run.json").read_text(encoding="utf-8"))
    osm = next(s for s in run["sources"] if s["dataset"] == "osm_roads")
    assert osm["source_kind"] == "databricks" and osm["chosen_by"] == "option"
    assert osm["rows"] == 88 and osm["snapshot_rows"] == 99768
    assert [q["state"] for q in run["sql_log"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert all(q["statement"].startswith("SELECT") for q in run["sql_log"])
    text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "db").rglob("*") if p.is_file())
    assert os.environ["DATABRICKS_TOKEN"] not in text
    assert os.environ["DATABRICKS_HOST"].split("//")[-1] not in text + remote.stdout + remote.stderr
