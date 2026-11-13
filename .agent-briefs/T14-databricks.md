# T14 — Databricks REST source for `osm_roads`

Read `.agent-briefs/_common.md` first and follow it. Then read `docs/ADR/002-data-sources-and-reports.md` (all of §2, §3 "Databricks read contract", §5, §7) and PRD AC-39, AC-41, AC-42, §4.7 item 2.

## Editable files (only these)

- `app/src/map_qa/sources/databricks.py` (new)
- `app/tests/unit/test_databricks_sql.py` (new)
- `app/tests/integration/test_databricks_adapter.py` (new)

Read-only inputs: `app/src/map_qa/sources/__init__.py` (written concurrently by task T5; it defines `RoadsResult` and `RoadsSource` exactly as below — if it does not exist yet when you start, wait/re-check rather than creating it), `app/src/map_qa/models.py`, and the **real captures** `app/tests/fixtures/captures/databricks_*.json` (count, limit5, params, async, failed). Each capture file has `captured_at`, `target`, `httpx`, and `steps`: a list of `{request: {method, path, body}, status, headers, removed_header_names, body}`; for `async` the first step is the POST (state PENDING) and the next steps are GET polls.

```python
@dataclass
class RoadsResult:
    rows: list[dict]      # {"osm_id": int, "maxspeed": str | None, "tags": str | None}
    total_rows: int       # count(*) of the whole table
    meta: SourceMeta
    sql_log: list[dict]   # {"dataset", "statement", "statement_id", "state"}
```

## API

```python
class DatabricksError(MapQaError): ...   # message: "osm_roads: databricks <category>: <detail>"

def build_in_query(table: str, columns: list[str], ids: list[int], id_type: str = "BIGINT") -> tuple[str, list[dict]]
def check_select(statement: str) -> None
def convert_rows(manifest: dict, data_array: list[list]) -> list[dict]

class DatabricksRoadsSource:
    def __init__(self, *, table: str, env: Mapping[str, str], host_env: str = "DATABRICKS_HOST",
                 token_env: str = "DATABRICKS_TOKEN", warehouse_env: str = "DATABRICKS_SQL_WAREHOUSE_ID",
                 warehouse_id: str | None = None, chosen_by: str = "default",
                 transport: httpx.BaseTransport | None = None,
                 poll_interval_s: float = 2.0, poll_timeout_s: float = 180.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic)
    def read_roads(self, way_ids: list[int]) -> RoadsResult
```

Rules (ADR-002):
- **Missing variable (AC-42):** in `__init__` or at the start of `read_roads`, before any request: host, token, and warehouse (env var named by `warehouse_env`, else `warehouse_id`) must be non-empty; host must start with `https://`. Else `DatabricksError("osm_roads: databricks missing_variable: DATABRICKS_TOKEN")` naming the **variable name**, zero requests. Never put the token or host value in any message, log or result.
- `table` must match `^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$`.
- `build_in_query`: `SELECT <columns> FROM <table> WHERE osm_id IN (:id_0, :id_1, …)` and parameters `[{"name": "id_0", "value": "<id>", "type": id_type}, …]` — values never in the SQL text. At most 256 ids per batch: provide `batches(ids, size=256)` and run one query per batch (the MVP needs one).
- `check_select`: strip leading whitespace and SQL comments (`-- …` lines, `/* … */`); must start with `SELECT` (case-insensitive) and contain no `;`. Else `DatabricksError(... "rejected_statement" ...)` before sending.
- Request body exactly as in the captures: `warehouse_id`, `statement`, `wait_timeout` `"10s"`, `on_wait_timeout` `"CONTINUE"`, `disposition` `"INLINE"`, `format` `"JSON_ARRAY"`, plus `parameters` when present. `POST {host}/api/2.0/sql/statements`; while state is PENDING/RUNNING, `GET {host}/api/2.0/sql/statements/{statement_id}` every `poll_interval_s` up to `poll_timeout_s` (then `timeout`). **Never re-POST.** One `httpx.Client` with `timeout=60`, no retries, `follow_redirects=False`, header `Authorization: Bearer <token>` built at call time.
- Two statements per `read_roads`: `SELECT count(*) AS n FROM <table>` → `total_rows`; then the projected read of `osm_id, highway, maxspeed, tags` for the ids. Each statement is appended to `sql_log` with its `statement_id` and final state.
- `convert_rows`: values converted by `manifest.schema.columns[].type_name`: `LONG`, `INT`, `SHORT`, `BYTE` → `int`; `STRING` → str unchanged; JSON `null` → `None`; a column name without a schema entry or an unknown type with a non-null value → `DatabricksError(... "unexpected_schema" ...)` (no guessing). Check that the required columns are present.
- HTTP errors: 401 → `auth`, 403 → `permission`, other non-200 → `http_<status>`; state `FAILED` → `query_failed` with `status.error.error_code` (and the message, which is safe: it contains no secret); `CANCELED`/`CLOSED` → `canceled`; httpx timeout → `timeout`. Every error is a `DatabricksError` (exit 2 upstream).
- `meta`: `SourceMeta(dataset="osm_roads", source_kind="databricks", location=table, chosen_by=..., rows=len(rows), read_at=<UTC ISO>, statement_id=<id of the projected read>)`.
- If `manifest.total_chunk_count > 1` or `truncated` is true → `DatabricksError(... "unexpected_result" ...)` (the MVP needs one INLINE chunk).

## Tests

- **Unit** (`test_databricks_sql.py`): `build_in_query` with 95 ids → 95 markers, every value only in parameters (assert no id string in the SQL), 257 ids → batches of 256 + 1; `check_select` rejects `DELETE …`, `SELECT 1; DROP TABLE x`, `/* c */ delete`, accepts `-- note\nselect …`; `convert_rows` on the `manifest`/`data_array` **taken from the capture files** (`"99768"` → 99768; `null` → None; tags string unchanged); a value whose column has no schema entry → error; missing variable → error naming the variable and a transport that records zero requests; token value never appears in any error message (use a fake token string like `"test-token-value"` from your test env dict).
- **Integration** (`test_databricks_adapter.py`) with `httpx.MockTransport` whose responses are **built only from the capture files** (status + body from `steps`; for the POST of the projected read use the `params` capture body; for count use the `count` capture; for polling use `async` steps in order; for FAILED use `failed`). Assert: exactly one POST per statement and n GETs for the async path (TAC-002-02), only SELECT statements sent, parameters sent as in the capture request body, rows typed (`osm_id` int equals the local BIGINT), `total_rows == 99768`, `sql_log` entries; FAILED → `query_failed` with `BAD_REQUEST`; 401/403 → `auth`/`permission` — for these two there is no capture, so build only the status code with an empty JSON body `{}` and assert the category (status-code mapping does not depend on the body shape); poll timeout with a fake clock → `timeout` and no second POST. Use a fake `sleep` so tests are fast.
- **Live E2E is not part of this task** (the orchestrator runs it).

Verification: `uv run --frozen pytest tests/unit/test_databricks_sql.py tests/integration/test_databricks_adapter.py -q`.
