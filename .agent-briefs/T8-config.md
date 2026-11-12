# T8 — Configuration and source selection

Read `.agent-briefs/_common.md` first and follow it. Also read `docs/ADR/002-data-sources-and-reports.md` §3 "Source selection", `docs/ADR/000-main-architecture.md` §7, ADR-004 §2 and §9, PRD AC-37, AC-38, AC-40.

## Editable files (only these)

- `app/src/map_qa/config.py` (new)
- `app/config.example.json` (new)
- `app/tests/unit/test_config.py` (new)

Concurrent tasks (do not touch their files): T4 `osm_speed.py`, `compare.py`; T5 `sources/`, `inputs.py`; T6 `review.py`; T7 `export.py`.

## Configuration file format (JSON)

```json
{
  "data_dir": "../course-materials/exercise-data/map-qa",
  "source": "local",
  "model_mode": "off",
  "datasets": {
    "osm_roads": {"source": "local", "local": "osm_roads.parquet", "databricks": "workspace.map_qa.osm_roads"},
    "signs": {"local": "signs/signs.csv"},
    "nearest_osm": {"local": "signs/nearest_osm.csv"},
    "crops": {"local": "signs/crops/"}
  },
  "databricks": {
    "host_env": "DATABRICKS_HOST",
    "token_env": "DATABRICKS_TOKEN",
    "warehouse_env": "DATABRICKS_SQL_WAREHOUSE_ID",
    "warehouse_id": "97c455ee6fb1fcb9"
  }
}
```

Every key is optional. A relative `data_dir` is resolved against the configuration file's folder. `warehouse_id` is allowed (it is not a secret); the environment variable named by `warehouse_env` overrides it.

## API

```python
DATASETS = ("signs", "nearest_osm", "crops", "osm_roads")
DATABRICKS_DATASETS = ("osm_roads",)

@dataclass(frozen=True)
class DatasetSource:
    dataset: str
    kind: str          # "local" / "databricks"
    location: str      # local relative path or catalog.schema.table
    chosen_by: str     # "option" / "env" / "config" / "default"

@dataclass(frozen=True)
class DatabricksEnv:
    host_env: str = "DATABRICKS_HOST"
    token_env: str = "DATABRICKS_TOKEN"
    warehouse_env: str = "DATABRICKS_SQL_WAREHOUSE_ID"
    warehouse_id: str | None = None

@dataclass(frozen=True)
class Settings:
    config_path: Path | None
    data_dir: Path
    sources: dict[str, DatasetSource]
    model_mode: str
    model_mode_chosen_by: str
    databricks: DatabricksEnv

def default_repo_root() -> Path            # Path(__file__).resolve().parents[3]
def load_config_file(path: Path) -> dict
def resolve_settings(*, config: str | None, source: str | None, source_osm_roads: str | None,
                     model_mode: str | None, env: Mapping[str, str]) -> Settings
```

- `load_config_file`: missing file → `MapQaError("config: file not found: <path>")`; invalid JSON → `MapQaError("config: invalid JSON in <path>")`. **Secret check (AC-40)**, recursively over all objects and lists, with the dotted field path (e.g. `databricks.token`): any key named `token`, `api_key`, `secret` or `password` (case-insensitive) → reject; any string value starting with `dapi`, `Bearer `, `sk-` or `ctx7sk` → reject; any key ending in `_env` whose value does not match `^[A-Z][A-Z0-9_]*$` → reject. Message: `config: field '<path>' looks like a credential; put credentials in environment variables`. **The value never appears in the message.** Unknown `source` values (not `local`/`databricks`) → error naming the field. A Databricks location must match `^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$`, else error naming the field.
- `resolve_settings`:
  - config path: `config` argument (chosen_by not needed) > `env["MAPQA_CONFIG"]` > none.
  - data_dir: `env["MAPQA_DATA_DIR"]` > config `data_dir` > `default_repo_root()/course-materials/exercise-data/map-qa`.
  - source of `osm_roads`, first hit wins: `source_osm_roads` (option) > `source` (option) > `MAPQA_SOURCE_OSM_ROADS` (env) > `MAPQA_SOURCE` (env) > config `datasets.osm_roads.source` (config) > config `source` (config) > `local` (default). `location`: local path from config or the default `osm_roads.parquet`; for Databricks the config value or `workspace.map_qa.osm_roads`.
  - the other datasets are always `local`, `chosen_by="default"`, with their default paths (`signs/signs.csv`, `signs/nearest_osm.csv`, `signs/crops/`) or config `local` overrides.
  - invalid source values from options or env → `MapQaError` naming the option or variable.
  - model_mode: option > `MAPQA_MODEL_MODE` > config `model_mode` > `off`; invalid → error naming where it came from.
  - databricks env names from config `databricks` or defaults.
- `app/config.example.json`: the example above (with `data_dir` `../course-materials/exercise-data/map-qa`, relative to `app/`). It must pass `load_config_file`.

## Tests

- nothing set → all local, `chosen_by="default"` (AC-37);
- option beats env beats file, for `osm_roads`, with `chosen_by` correct at each level; `--source databricks` leaves `signs` local (AC-38);
- secrets (AC-40): field `token`; nested `databricks.password`; value `dapiXXXX` under an innocent key; `token_env: "dapi123"`; value `Bearer abc`; `sk-…`; `ctx7sk…` — each rejected, message contains the field path and **does not contain the value**;
- accepted: `warehouse_id: "97c455ee6fb1fcb9"`, a 64-hex SHA-256 string value, `host_env: "DATABRICKS_HOST"`;
- `app/config.example.json` loads and resolves to `osm_roads` local;
- bad table name `workspace.map_qa.osm_roads; DROP` → error; bad source `s3` → error; bad model mode → error;
- data_dir precedence and relative resolution against the config folder.

Use `tmp_path` for config files and pass `env` dicts explicitly (never read `os.environ` in tests).

Verification: `uv run --frozen pytest tests/unit/test_config.py -q`.
