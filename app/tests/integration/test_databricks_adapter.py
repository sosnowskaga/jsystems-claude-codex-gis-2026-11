from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from map_qa.sources.databricks import DatabricksError, DatabricksRoadsSource, check_select

CAPTURES = Path(__file__).parents[1] / "fixtures" / "captures"
TABLE = "workspace.map_qa.osm_roads"


def steps(name: str) -> list[dict]:
    return json.loads((CAPTURES / f"databricks_{name}.json").read_text(encoding="utf-8"))["steps"]


def environment() -> dict[str, str]:
    return {"DATABRICKS_HOST": "https://example.invalid", "DATABRICKS_TOKEN": "test-token-value", "DATABRICKS_SQL_WAREHOUSE_ID": steps("count")[0]["request"]["body"]["warehouse_id"]}


def ids() -> list[int]:
    return [int(p["value"]) for p in steps("params")[0]["request"]["body"]["parameters"]]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_read_capture_contract(asynchronous: bool) -> None:
    expected = steps("count") + steps("async" if asynchronous else "params")
    requests = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        step = expected[len(requests)]
        requests.append(request)
        assert request.method == step["request"]["method"]
        assert request.url.path == step["request"]["path"]
        assert request.headers["Authorization"] == "Bearer test-token-value"
        assert request.extensions["timeout"]["read"] == 60
        if request.method == "POST":
            body = json.loads(request.content)
            check_select(body["statement"])
            original = step["request"]["body"]
            assert body == {**original, "wait_timeout": "10s"}
        return httpx.Response(step["status"], headers=step["headers"], json=step["body"])

    result = DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler), sleep=sleeps.append, chosen_by="option").read_roads(ids())
    assert len(requests) == len(expected)
    assert sum(r.method == "POST" for r in requests) == 2
    assert sum(r.method == "GET" for r in requests) == len(expected) - 2
    assert sleeps == [2.0] * (len(expected) - 2)
    assert result.total_rows == 99768
    assert result.rows[0]["osm_id"] == ids()[0]
    assert type(result.rows[0]["osm_id"]) is int
    assert result.rows[1]["maxspeed"] is None
    assert result.meta.dataset == "osm_roads"
    assert result.meta.source_kind == "databricks"
    assert result.meta.location == TABLE
    assert result.meta.chosen_by == "option"
    assert result.meta.rows == 3
    assert datetime.fromisoformat(result.meta.read_at).utcoffset().total_seconds() == 0
    assert result.meta.statement_id == expected[-1]["body"]["statement_id"]
    assert result.sql_log == [{"dataset": "osm_roads", "statement": step["request"]["body"]["statement"], "statement_id": final["body"]["statement_id"], "state": "SUCCEEDED"} for step, final in ((expected[0], expected[0]), (expected[1], expected[-1]))]
    assert "test-token-value" not in repr(result)
    assert "example.invalid" not in repr(result)


def test_failed_capture() -> None:
    step = steps("failed")[0]
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(step["status"], json=step["body"])

    with pytest.raises(DatabricksError, match="query_failed: BAD_REQUEST") as error:
        DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler)).read_roads(ids())
    assert len(requests) == 1
    assert "test-token-value" not in str(error.value)


@pytest.mark.parametrize(("status", "category"), [(401, "auth"), (403, "permission"), (302, "http_302"), (500, "http_500")])
def test_http_status(status: int, category: str) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={} if status in (401, 403) else steps("count")[0]["body"])

    with pytest.raises(DatabricksError, match=category) as error:
        DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler)).read_roads(ids())
    assert len(requests) == 1
    assert "test-token-value" not in str(error.value)
    assert "example.invalid" not in str(error.value)


def test_poll_timeout_never_reposts() -> None:
    pending = steps("async")[0]
    requests = []
    now = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(pending["status"], json=pending["body"])

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(DatabricksError, match="timeout"):
        DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: now[0], poll_timeout_s=5).read_roads(ids())
    assert sum(r.method == "POST" for r in requests) == 1
    assert sum(r.method == "GET" for r in requests) == 2
    assert now[0] == 5


def test_httpx_timeout_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test-token-value https://example.invalid", request=request)

    with pytest.raises(DatabricksError, match="timeout") as error:
        DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler)).read_roads(ids())
    assert "test-token-value" not in str(error.value)
    assert "example.invalid" not in str(error.value)


@pytest.mark.parametrize("change", ["chunks", "truncated", "missing_column"])
def test_reject_incomplete_or_missing_required_columns(change: str) -> None:
    count = steps("count")[0]
    projected = steps("params")[0]
    if change == "chunks":
        projected["body"]["manifest"]["total_chunk_count"] = 2
    elif change == "truncated":
        projected["body"]["manifest"]["truncated"] = True
    else:
        projected["body"]["manifest"]["schema"]["columns"][3]["name"] = "other"
    responses = iter([count, projected])

    def handler(request: httpx.Request) -> httpx.Response:
        step = next(responses)
        return httpx.Response(step["status"], json=step["body"])

    category = "unexpected_schema" if change == "missing_column" else "unexpected_result"
    with pytest.raises(DatabricksError, match=category):
        DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler)).read_roads(ids())


def test_multiple_batches_and_credentials_read_at_call_time() -> None:
    env = {}
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        step = steps("count" if "count(*)" in body["statement"] else "params")[0]
        return httpx.Response(step["status"], json=step["body"])

    source = DatabricksRoadsSource(table=TABLE, env=env, host_env="DB_HOST", token_env="DB_TOKEN", warehouse_env="DB_WAREHOUSE", warehouse_id="configured-warehouse", transport=httpx.MockTransport(handler))
    env.update({"DB_HOST": "https://example.invalid", "DB_TOKEN": "test-token-value"})
    result = source.read_roads(list(range(1000000, 1000257)))
    assert len(requests) == 3
    assert [len(body["parameters"]) for body in requests[1:]] == [256, 1]
    assert all(body["warehouse_id"] == "configured-warehouse" for body in requests)
    assert len(result.sql_log) == 3


def test_poll_response_after_deadline_is_timeout() -> None:
    responses = iter(steps("count") + steps("async"))
    now = [0.0]
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        step = next(responses)
        if request.method == "GET":
            now[0] += 10
        return httpx.Response(step["status"], json=step["body"])

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(DatabricksError, match="timeout"):
        DatabricksRoadsSource(table=TABLE, env=environment(), transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: now[0], poll_timeout_s=5).read_roads(ids())
    assert [r.method for r in requests] == ["POST", "POST", "GET"]
