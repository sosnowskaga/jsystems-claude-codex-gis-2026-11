"""Read-only Databricks Statement Execution adapter (ADR-002)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx

from map_qa.models import MapQaError, SourceMeta
from map_qa.sources import RoadsResult

TABLE_PATTERN = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+")
ROAD_COLUMNS = ["osm_id", "highway", "maxspeed", "tags"]
INTEGER_TYPES = {"LONG", "INT", "SHORT", "BYTE"}


class DatabricksError(MapQaError):
    """A terminal source error, reported upstream with exit code 2."""


def _error(category: str, detail: str) -> DatabricksError:
    return DatabricksError(f"osm_roads: databricks {category}: {' '.join(detail.split())}")


def _check_table(table: str) -> None:
    if not TABLE_PATTERN.fullmatch(table):
        raise _error("rejected_statement", "invalid table identifier")


def batches(ids: list[int], size: int = 256) -> Iterator[list[int]]:
    if not 1 <= size <= 256:
        raise _error("rejected_statement", "batch size must be between 1 and 256")
    for offset in range(0, len(ids), size):
        yield ids[offset:offset + size]


def build_in_query(
    table: str, columns: list[str], ids: list[int], id_type: str = "BIGINT",
) -> tuple[str, list[dict]]:
    _check_table(table)
    if not columns or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) for column in columns):
        raise _error("rejected_statement", "invalid column identifier")
    if len(ids) > 256 or any(type(value) is not int for value in ids):
        raise _error("rejected_statement", "expected at most 256 integer IDs")
    parameters = [{"name": f"id_{i}", "value": str(value), "type": id_type} for i, value in enumerate(ids)]
    markers = ", ".join(f":id_{i}" for i in range(len(ids))) or "NULL"
    statement = f"SELECT {', '.join(columns)} FROM {table} WHERE osm_id IN ({markers})"
    check_select(statement)
    return statement, parameters


def check_select(statement: str) -> None:
    remaining = statement.lstrip()
    while remaining.startswith(("--", "/*")):
        if remaining.startswith("--"):
            remaining = remaining.partition("\n")[2].lstrip()
        else:
            _, end, remaining = remaining.partition("*/")
            if not end:
                raise _error("rejected_statement", "unterminated SQL comment")
            remaining = remaining.lstrip()
    if ";" in statement or not re.match(r"SELECT\b", remaining, re.IGNORECASE):
        raise _error("rejected_statement", "expected a single SELECT")


def convert_rows(manifest: dict, data_array: list[list]) -> list[dict]:
    try:
        columns = manifest["schema"]["columns"]
        if len(columns) != manifest["schema"].get("column_count", len(columns)):
            raise _error("unexpected_schema", "column count differs from schema")
        names = [column["name"] for column in columns]
        if len(set(names)) != len(names):
            raise _error("unexpected_schema", "duplicate column name")
        positions = [column.get("position", i) for i, column in enumerate(columns)]
        if sorted(positions) != list(range(len(columns))):
            raise _error("unexpected_schema", "invalid column positions")
        rows = []
        for values in data_array:
            if len(values) != len(columns):
                raise _error("unexpected_schema", "value without a schema entry")
            row = {}
            for column, position in zip(columns, positions):
                value = values[position]
                kind = column.get("type_name")
                if value is None:
                    converted = None
                elif kind in INTEGER_TYPES:
                    converted = int(value)
                elif kind == "STRING" and isinstance(value, str):
                    converted = value
                else:
                    raise _error("unexpected_schema", "unsupported column type or value")
                row[column["name"]] = converted
            rows.append(row)
        return rows
    except (KeyError, TypeError, ValueError, OverflowError):
        raise _error("unexpected_schema", "invalid manifest or typed value") from None


class DatabricksRoadsSource:
    def __init__(
        self, *, table: str, env: Mapping[str, str], host_env: str = "DATABRICKS_HOST",
        token_env: str = "DATABRICKS_TOKEN", warehouse_env: str = "DATABRICKS_SQL_WAREHOUSE_ID",
        warehouse_id: str | None = None, chosen_by: str = "default",
        transport: httpx.BaseTransport | None = None,
        poll_interval_s: float = 2.0, poll_timeout_s: float = 180.0,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
    ):
        _check_table(table)
        self.table = table
        self.env = env
        self.host_env = host_env
        self.token_env = token_env
        self.warehouse_env = warehouse_env
        self.warehouse_id = warehouse_id
        self.chosen_by = chosen_by
        self.transport = transport
        if poll_interval_s <= 0 or poll_timeout_s < 0:
            raise _error("timeout", "invalid polling configuration")
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s
        self.sleep = sleep
        self.clock = clock

    def _request(self, client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict:
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise _error("timeout", "HTTP request timed out") from None
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            raise _error("http_error", "HTTP request failed") from None
        if response.status_code != 200:
            category = {401: "auth", 403: "permission"}.get(response.status_code, f"http_{response.status_code}")
            raise _error(category, "HTTP request rejected")
        try:
            payload = response.json()
        except ValueError:
            raise _error("unexpected_result", "invalid JSON response") from None
        if not isinstance(payload, dict):
            raise _error("unexpected_result", "expected a JSON object")
        return payload

    def _execute(
        self, client: httpx.Client, host: str, warehouse: str, statement: str,
        parameters: list[dict], required: list[str], sql_log: list[dict],
    ) -> tuple[list[dict], dict, str]:
        check_select(statement)
        body = {"warehouse_id": warehouse, "statement": statement, "wait_timeout": "10s",
                "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY"}
        if parameters:
            body["parameters"] = parameters
        url = f"{host}/api/2.0/sql/statements"
        payload = self._request(client, "POST", url, json=body)
        statement_id = payload.get("statement_id")
        if not isinstance(statement_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", statement_id):
            raise _error("unexpected_result", "missing or invalid statement_id")
        deadline = self.clock() + self.poll_timeout_s
        while True:
            status = payload.get("status", {})
            state = status.get("state")
            if state not in {"PENDING", "RUNNING"}:
                break
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise _error("timeout", "statement polling deadline exceeded")
            self.sleep(min(self.poll_interval_s, remaining))
            if self.clock() >= deadline:
                raise _error("timeout", "statement polling deadline exceeded")
            payload = self._request(client, "GET", f"{url}/{statement_id}")
            if self.clock() >= deadline:
                raise _error("timeout", "statement polling deadline exceeded")
        sql_log.append({"dataset": "osm_roads", "statement": statement, "statement_id": statement_id, "state": state})
        if state == "FAILED":
            error = status.get("error", {})
            raise _error("query_failed", f"{error.get('error_code', 'UNKNOWN')}: {error.get('message', '')}")
        if state in {"CANCELED", "CLOSED"}:
            raise _error("canceled", "statement canceled or closed")
        if state != "SUCCEEDED":
            raise _error("unexpected_result", "unknown statement state")
        manifest = payload.get("manifest", {})
        if manifest.get("total_chunk_count", 1) > 1 or manifest.get("truncated") or payload.get("truncated"):
            raise _error("unexpected_result", "expected one complete INLINE chunk")
        try:
            names = {column["name"] for column in manifest["schema"]["columns"]}
            if not set(required) <= names:
                raise _error("unexpected_schema", "missing required columns")
            rows = convert_rows(manifest, payload["result"].get("data_array", []))
        except (KeyError, TypeError):
            raise _error("unexpected_schema", "missing manifest or result") from None
        return rows, manifest, statement_id

    def read_roads(self, way_ids: list[int]) -> RoadsResult:
        host = self.env.get(self.host_env, "")
        token = self.env.get(self.token_env, "")
        warehouse = self.env.get(self.warehouse_env) or self.warehouse_id or ""
        for variable, value in ((self.host_env, host), (self.token_env, token), (self.warehouse_env, warehouse)):
            if not value.strip():
                raise _error("missing_variable", variable)
        if not host.startswith("https://"):
            raise _error("missing_variable", self.host_env)
        sql_log: list[dict] = []
        rows: list[dict] = []
        try:
            with httpx.Client(timeout=60, follow_redirects=False, transport=self.transport,
                              headers={"Authorization": f"Bearer {token}"}) as client:
                counts, _, _ = self._execute(client, host.rstrip("/"), warehouse,
                                             f"SELECT count(*) AS n FROM {self.table}", [], ["n"], sql_log)
                if len(counts) != 1 or type(counts[0]["n"]) is not int:
                    raise _error("unexpected_schema", "expected one integer count")
                statement_id = None
                for batch in batches(way_ids) if way_ids else [[]]:
                    statement, parameters = build_in_query(self.table, ROAD_COLUMNS, batch)
                    result, _, statement_id = self._execute(client, host.rstrip("/"), warehouse,
                                                            statement, parameters, ROAD_COLUMNS, sql_log)
                    if any(type(row["osm_id"]) is not int for row in result):
                        raise _error("unexpected_schema", "osm_id must be an integer")
                    rows.extend(result)
        except DatabricksError as error:
            message = str(error).replace(token, "[redacted]").replace(host, "[redacted]")
            raise DatabricksError(message) from None
        return RoadsResult(rows=rows, total_rows=counts[0]["n"], sql_log=sql_log,
                           meta=SourceMeta(dataset="osm_roads", source_kind="databricks", location=self.table,
                                           chosen_by=self.chosen_by, rows=len(rows),
                                           read_at=datetime.now(timezone.utc).isoformat(), statement_id=statement_id))
