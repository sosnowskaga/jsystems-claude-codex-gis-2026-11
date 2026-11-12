"""One-off capture of real Databricks SQL Statement Execution API responses (ADR-002 §7, T13).

Saves verbatim response bodies with HTTP status and a whitelist of response headers to
``app/tests/fixtures/captures/databricks_*.json``. Never stores the token, the host or
account-identifying headers. Run from ``app/`` (uses the app's locked httpx):

    uv run python ../reports/map-qa/capture_databricks.py

Requires DATABRICKS_HOST, DATABRICKS_TOKEN and DATABRICKS_SQL_WAREHOUSE_ID in the environment.
Read-only: only SELECT statements are sent.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[2] / "app" / "tests" / "fixtures" / "captures"
TABLE = "workspace.map_qa.osm_roads"
COLUMNS = "osm_id, highway, maxspeed, tags"
KEEP_HEADERS = {"content-type", "date"}
IDS = [187014751, 557114409, 1266239200]


def main() -> int:
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "")
    warehouse = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")
    missing = [n for n, v in (("DATABRICKS_HOST", host), ("DATABRICKS_TOKEN", token), ("DATABRICKS_SQL_WAREHOUSE_ID", warehouse)) if not v]
    if missing:
        print("missing variables: " + ", ".join(missing))
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        base_url=host,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
        follow_redirects=False,
    )

    def record(resp: httpx.Response, request_body: dict | None) -> dict:
        return {
            "request": {
                "method": resp.request.method,
                "path": resp.request.url.path,
                "body": request_body,
            },
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS},
            "removed_header_names": sorted(k.lower() for k in resp.headers if k.lower() not in KEEP_HEADERS),
            "body": resp.json(),
        }

    def post(body: dict) -> dict:
        assert body["statement"].lstrip().upper().startswith("SELECT") and ";" not in body["statement"]
        return record(client.post("/api/2.0/sql/statements", json=body), body)

    def base(statement: str, **extra) -> dict:
        body = {
            "warehouse_id": warehouse,
            "statement": statement,
            "wait_timeout": "10s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
        body.update(extra)
        return body

    def poll(first: dict) -> list[dict]:
        steps = [first]
        statement_id = first["body"].get("statement_id")
        started = time.monotonic()
        while steps[-1]["body"].get("status", {}).get("state") in ("PENDING", "RUNNING"):
            if time.monotonic() - started > 180:
                break
            time.sleep(2)
            steps.append(record(client.get(f"/api/2.0/sql/statements/{statement_id}"), None))
        return steps

    meta = {
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "target": "Databricks SQL Statement Execution API 2.0, workspace host omitted",
        "httpx": httpx.__version__,
    }
    params = [{"name": f"id_{i}", "value": str(v), "type": "BIGINT"} for i, v in enumerate(IDS)]
    markers = ", ".join(f":id_{i}" for i in range(len(IDS)))
    captures = {
        "databricks_count": poll(post(base(f"SELECT count(*) AS n FROM {TABLE}"))),
        "databricks_limit5": poll(post(base(f"SELECT {COLUMNS} FROM {TABLE} ORDER BY osm_id LIMIT 5"))),
        "databricks_params": poll(post(base(f"SELECT {COLUMNS} FROM {TABLE} WHERE osm_id IN ({markers})", parameters=params))),
        "databricks_async": poll(
            post(base(f"SELECT {COLUMNS} FROM {TABLE} WHERE osm_id IN ({markers})", parameters=params, wait_timeout="0s"))
        ),
        "databricks_failed": poll(post(base(f"SELECT no_such_column FROM {TABLE} LIMIT 1"))),
    }
    for name, steps in captures.items():
        text = json.dumps({**meta, "steps": steps}, indent=2, ensure_ascii=False)
        if token in text or host.split("//")[-1] in text:
            print(f"{name}: secret or host found in capture, not saved")
            return 3
        (OUT / f"{name}.json").write_text(text + "\n", encoding="utf-8")
        states = [s["body"].get("status", {}).get("state") for s in steps]
        print(f"{name}: HTTP {[s['status'] for s in steps]} states {states}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
