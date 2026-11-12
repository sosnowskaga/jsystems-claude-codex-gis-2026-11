from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from map_qa.sources.databricks import (
    DatabricksError, DatabricksRoadsSource, batches, build_in_query, check_select, convert_rows,
)

CAPTURES = Path(__file__).parents[1] / "fixtures" / "captures"
TABLE = "workspace.map_qa.osm_roads"


def captured(name: str) -> dict:
    return json.loads((CAPTURES / f"databricks_{name}.json").read_text(encoding="utf-8"))["steps"][0]["body"]


def test_parameterized_query_and_batches() -> None:
    ids = list(range(1000000000, 1000000095))
    sql, params = build_in_query(TABLE, ["osm_id", "maxspeed"], ids)
    assert sql == f"SELECT osm_id, maxspeed FROM {TABLE} WHERE osm_id IN ({', '.join(f':id_{i}' for i in range(95))})"
    assert params == [{"name": f"id_{i}", "value": str(value), "type": "BIGINT"} for i, value in enumerate(ids)]
    assert all(str(value) not in sql for value in ids)
    assert [len(batch) for batch in batches(list(range(257)))] == [256, 1]


@pytest.mark.parametrize("sql", ["DELETE FROM x", "SELECT 1; DROP TABLE x", "/* c */ delete from x", "SELECTED x", "/* unfinished"])
def test_reject_statement(sql: str) -> None:
    with pytest.raises(DatabricksError, match="rejected_statement"):
        check_select(sql)


@pytest.mark.parametrize("sql", ["-- note\nselect 1", " /* c */ -- note\nSELECT 1", "SELECT 1"])
def test_accept_select(sql: str) -> None:
    check_select(sql)


@pytest.mark.parametrize("table", ["osm_roads", "a.b.c;DROP", "a.b.c.d"])
def test_reject_table(table: str) -> None:
    with pytest.raises(DatabricksError):
        build_in_query(table, ["osm_id"], [1])


def test_convert_real_captures() -> None:
    count = captured("count")
    assert convert_rows(count["manifest"], count["result"]["data_array"]) == [{"n": 99768}]
    for name in ("limit5", "params"):
        body = captured(name)
        rows = convert_rows(body["manifest"], body["result"]["data_array"])
        assert type(rows[0]["osm_id"]) is int
        assert rows[0]["tags"] == body["result"]["data_array"][0][3]
    assert rows[1]["maxspeed"] is None


@pytest.mark.parametrize("change", ["missing", "unknown", "extra_value"])
def test_schema_errors(change: str) -> None:
    body = copy.deepcopy(captured("params"))
    if change == "missing":
        body["manifest"]["schema"]["columns"].pop()
    elif change == "unknown":
        body["manifest"]["schema"]["columns"][0]["type_name"] = "UNSUPPORTED"
    else:
        body["result"]["data_array"][0].append("untyped")
    with pytest.raises(DatabricksError, match="unexpected_schema"):
        convert_rows(body["manifest"], body["result"]["data_array"])


@pytest.mark.parametrize("variable", ["DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_SQL_WAREHOUSE_ID"])
@pytest.mark.parametrize("value", [None, "", " "])
def test_missing_variable_no_requests(variable: str, value: str | None) -> None:
    env = {"DATABRICKS_HOST": "https://example.invalid", "DATABRICKS_TOKEN": "test-token-value", "DATABRICKS_SQL_WAREHOUSE_ID": "warehouse"}
    if value is None:
        env.pop(variable)
    else:
        env[variable] = value
    requests = []
    with pytest.raises(DatabricksError, match=f"missing_variable: {variable}") as error:
        DatabricksRoadsSource(table=TABLE, env=env, transport=httpx.MockTransport(lambda request: requests.append(request))).read_roads([1])
    assert requests == []
    assert "test-token-value" not in str(error.value)


def test_invalid_host_does_not_leak_value() -> None:
    with pytest.raises(DatabricksError, match="DATABRICKS_HOST") as error:
        DatabricksRoadsSource(table=TABLE, env={"DATABRICKS_HOST": "http://private.invalid", "DATABRICKS_TOKEN": "test-token-value", "DATABRICKS_SQL_WAREHOUSE_ID": "warehouse"}).read_roads([1])
    assert "private.invalid" not in str(error.value)
    assert "test-token-value" not in str(error.value)


@pytest.mark.parametrize("kind", ["LONG", "INT", "SHORT", "BYTE"])
def test_integer_types_follow_schema(kind: str) -> None:
    body = captured("count")
    body["manifest"]["schema"]["columns"][0]["type_name"] = kind
    assert convert_rows(body["manifest"], body["result"]["data_array"]) == [{"n": 99768}]


def test_unknown_type_null_stays_missing() -> None:
    body = captured("params")
    body["manifest"]["schema"]["columns"][2]["type_name"] = "UNSUPPORTED"
    assert convert_rows(body["manifest"], body["result"]["data_array"][1:])[0]["maxspeed"] is None
