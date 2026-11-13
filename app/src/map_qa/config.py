"""Configuration file and source selection (ADR-002 §3, ADR-004 §2, ADR-000 §7, AC-37, AC-38, AC-40).

Precedence is resolved here, once, for every run. The configuration file never holds credential
values: only names of environment variables (``*_env``) and non-secret identifiers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from map_qa.models import MODEL_MODES, MapQaError

DATASETS: tuple[str, ...] = ("signs", "nearest_osm", "crops", "osm_roads")
DATABRICKS_DATASETS: tuple[str, ...] = ("osm_roads",)
SOURCE_KINDS: tuple[str, ...] = ("local", "databricks")

DEFAULT_LOCATIONS: dict[str, str] = {
    "signs": "signs/signs.csv",
    "nearest_osm": "signs/nearest_osm.csv",
    "crops": "signs/crops/",
    "osm_roads": "osm_roads.parquet",
}
DEFAULT_DATABRICKS_LOCATION = "workspace.map_qa.osm_roads"

_TABLE_RE = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+")
_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_SECRET_KEYS = frozenset({"token", "api_key", "secret", "password"})
_SECRET_VALUE_PREFIXES: tuple[str, ...] = ("dapi", "Bearer ", "sk-", "ctx7sk")


@dataclass(frozen=True)
class DatasetSource:
    dataset: str
    kind: str  # "local" / "databricks"
    location: str  # local relative path or catalog.schema.table
    chosen_by: str  # "option" / "env" / "config" / "default"


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


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# --- configuration file --------------------------------------------------------------------------


def load_config_file(path: Path) -> dict:
    """Read and validate a configuration file; raises ``MapQaError`` naming the field on failure."""
    path = Path(path)
    if not path.is_file():
        raise MapQaError(f"config: file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MapQaError(f"config: cannot read {path}: {exc.strerror}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise MapQaError(f"config: invalid JSON in {path}") from exc
    if not isinstance(data, dict):
        raise MapQaError(f"config: top level must be a JSON object in {path}")

    _reject_credentials(data, "")
    _validate_fields(data)
    return data


def _reject_credentials(node: Any, path: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if lowered in _SECRET_KEYS:
                _raise_credential(child)
            if lowered.endswith("_env") and not (isinstance(value, str) and _ENV_NAME_RE.fullmatch(value)):
                _raise_credential(child)
            _reject_credentials(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_credentials(item, f"{path}[{index}]")
    elif isinstance(node, str):
        if node.startswith(_SECRET_VALUE_PREFIXES):
            _raise_credential(path)


def _raise_credential(field: str) -> None:
    raise MapQaError(f"config: field '{field}' looks like a credential; put credentials in environment variables")


def _validate_fields(data: dict) -> None:
    if "data_dir" in data:
        _expect_str(data["data_dir"], "data_dir")
    if "source" in data and not _valid_source(data["source"]):
        raise MapQaError("config: field 'source' must be 'local' or 'databricks'")
    if "model_mode" in data and not _valid_model_mode(data["model_mode"]):
        raise MapQaError(f"config: field 'model_mode' must be one of {', '.join(MODEL_MODES)}")

    datasets = data.get("datasets", {})
    if not isinstance(datasets, dict):
        raise MapQaError("config: field 'datasets' must be an object")
    for name, entry in datasets.items():
        field = f"datasets.{name}"
        if not isinstance(entry, dict):
            raise MapQaError(f"config: field '{field}' must be an object")
        if "source" in entry and not _valid_source(entry["source"]):
            raise MapQaError(f"config: field '{field}.source' must be 'local' or 'databricks'")
        if "local" in entry:
            _expect_str(entry["local"], f"{field}.local")
        if "databricks" in entry:
            _expect_str(entry["databricks"], f"{field}.databricks")
            if not _TABLE_RE.fullmatch(entry["databricks"]):
                raise MapQaError(
                    f"config: field '{field}.databricks' must be a catalog.schema.table name (letters, digits, _)"
                )

    databricks = data.get("databricks", {})
    if not isinstance(databricks, dict):
        raise MapQaError("config: field 'databricks' must be an object")
    for key in ("host_env", "token_env", "warehouse_env", "warehouse_id"):
        if key in databricks:
            _expect_str(databricks[key], f"databricks.{key}")


def _expect_str(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise MapQaError(f"config: field '{field}' must be a string")


def _valid_source(value: Any) -> bool:
    return isinstance(value, str) and value in SOURCE_KINDS


def _valid_model_mode(value: Any) -> bool:
    return isinstance(value, str) and value in MODEL_MODES


# --- source selection ----------------------------------------------------------------------------


def resolve_settings(
    *,
    config: str | None,
    source: str | None,
    source_osm_roads: str | None,
    model_mode: str | None,
    env: Mapping[str, str],
) -> Settings:
    """Resolve the effective settings. Option > environment > configuration file > default (AC-38)."""
    config_path = _config_path(config, env)
    file_data = load_config_file(config_path) if config_path is not None else {}
    datasets_cfg = file_data.get("datasets", {})

    data_dir = _data_dir(file_data, config_path, env)
    sources = _resolve_sources(file_data, datasets_cfg, source, source_osm_roads, env)
    mode, mode_by = _resolve_model_mode(file_data, model_mode, env)

    db_cfg = file_data.get("databricks", {})
    databricks = DatabricksEnv(
        host_env=db_cfg.get("host_env", "DATABRICKS_HOST"),
        token_env=db_cfg.get("token_env", "DATABRICKS_TOKEN"),
        warehouse_env=db_cfg.get("warehouse_env", "DATABRICKS_SQL_WAREHOUSE_ID"),
        warehouse_id=env.get(db_cfg.get("warehouse_env", "DATABRICKS_SQL_WAREHOUSE_ID"))
        or db_cfg.get("warehouse_id")
        or None,
    )

    return Settings(
        config_path=config_path,
        data_dir=data_dir,
        sources=sources,
        model_mode=mode,
        model_mode_chosen_by=mode_by,
        databricks=databricks,
    )


def _config_path(config: str | None, env: Mapping[str, str]) -> Path | None:
    if config:
        return Path(config)
    if env.get("MAPQA_CONFIG"):
        return Path(env["MAPQA_CONFIG"])
    return None


def _data_dir(file_data: dict, config_path: Path | None, env: Mapping[str, str]) -> Path:
    if env.get("MAPQA_DATA_DIR"):
        return Path(env["MAPQA_DATA_DIR"])
    if "data_dir" in file_data and config_path is not None:
        return (config_path.parent / file_data["data_dir"]).resolve()
    return default_repo_root() / "course-materials" / "exercise-data" / "map-qa"


def _resolve_sources(
    file_data: dict,
    datasets_cfg: dict,
    source: str | None,
    source_osm_roads: str | None,
    env: Mapping[str, str],
) -> dict[str, DatasetSource]:
    osm_cfg = datasets_cfg.get("osm_roads", {})

    # Option and environment values are checked even when a higher level already wins.
    candidates: list[tuple[str, str, str]] = []  # (value, chosen_by, label)
    if source_osm_roads is not None:
        candidates.append((source_osm_roads, "option", "--source-osm-roads"))
    if source is not None:
        candidates.append((source, "option", "--source"))
    if env.get("MAPQA_SOURCE_OSM_ROADS"):
        candidates.append((env["MAPQA_SOURCE_OSM_ROADS"], "env", "MAPQA_SOURCE_OSM_ROADS"))
    if env.get("MAPQA_SOURCE"):
        candidates.append((env["MAPQA_SOURCE"], "env", "MAPQA_SOURCE"))
    for value, _, label in candidates:
        if not _valid_source(value):
            raise MapQaError(f"invalid {label} value: expected 'local' or 'databricks'")

    if candidates:
        osm_kind, osm_by = candidates[0][0], candidates[0][1]
    elif osm_cfg.get("source"):
        osm_kind, osm_by = osm_cfg["source"], "config"
    elif file_data.get("source"):
        osm_kind, osm_by = file_data["source"], "config"
    else:
        osm_kind, osm_by = "local", "default"

    sources: dict[str, DatasetSource] = {}
    for name in DATASETS:
        if name == "osm_roads":
            if osm_kind == "databricks":
                location = osm_cfg.get("databricks", DEFAULT_DATABRICKS_LOCATION)
            else:
                location = osm_cfg.get("local", DEFAULT_LOCATIONS[name])
            sources[name] = DatasetSource(name, osm_kind, location, osm_by)
        else:
            # Only osm_roads has a Databricks location; the other datasets always stay local.
            location = datasets_cfg.get(name, {}).get("local", DEFAULT_LOCATIONS[name])
            sources[name] = DatasetSource(name, "local", location, "default")
    return sources


def _resolve_model_mode(file_data: dict, model_mode: str | None, env: Mapping[str, str]) -> tuple[str, str]:
    if model_mode is not None:
        if not _valid_model_mode(model_mode):
            raise MapQaError(f"invalid --model-mode value: expected one of {', '.join(MODEL_MODES)}")
        return model_mode, "option"
    if env.get("MAPQA_MODEL_MODE"):
        value = env["MAPQA_MODEL_MODE"]
        if not _valid_model_mode(value):
            raise MapQaError(f"invalid MAPQA_MODEL_MODE value: expected one of {', '.join(MODEL_MODES)}")
        return value, "env"
    if file_data.get("model_mode"):
        return file_data["model_mode"], "config"
    return "off", "default"
