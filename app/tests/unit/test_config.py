from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from map_qa.config import (
    DATASETS,
    default_repo_root,
    load_config_file,
    resolve_settings,
)
from map_qa.models import MapQaError

APP_DIR = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = APP_DIR / "config.example.json"


def write_config(tmp_path: Path, data: object, name: str = "config.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def resolve(**kwargs):
    args = {"config": None, "source": None, "source_osm_roads": None, "model_mode": None, "env": {}}
    args.update(kwargs)
    return resolve_settings(**args)


# --- defaults (AC-37) ----------------------------------------------------------------------------


def test_nothing_set_reads_all_datasets_locally_by_default():
    settings = resolve()

    assert settings.config_path is None
    assert settings.data_dir == default_repo_root() / "course-materials" / "exercise-data" / "map-qa"
    assert tuple(settings.sources) == DATASETS
    for name, source in settings.sources.items():
        assert source.dataset == name
        assert source.kind == "local"
        assert source.chosen_by == "default"
    assert settings.sources["osm_roads"].location == "osm_roads.parquet"
    assert settings.sources["signs"].location == "signs/signs.csv"
    assert settings.sources["nearest_osm"].location == "signs/nearest_osm.csv"
    assert settings.sources["crops"].location == "signs/crops/"
    assert settings.model_mode == "off"
    assert settings.model_mode_chosen_by == "default"
    assert settings.databricks.host_env == "DATABRICKS_HOST"
    assert settings.databricks.token_env == "DATABRICKS_TOKEN"
    assert settings.databricks.warehouse_env == "DATABRICKS_SQL_WAREHOUSE_ID"
    assert settings.databricks.warehouse_id is None


# --- precedence for osm_roads (AC-38) ------------------------------------------------------------


def test_osm_roads_source_from_config_file_only(tmp_path):
    cfg = write_config(tmp_path, {"source": "databricks"})

    settings = resolve(config=str(cfg))

    assert settings.sources["osm_roads"].kind == "databricks"
    assert settings.sources["osm_roads"].location == "workspace.map_qa.osm_roads"
    assert settings.sources["osm_roads"].chosen_by == "config"


@pytest.mark.parametrize(
    ("file_data", "env", "option_source", "option_osm", "expected_kind", "expected_by"),
    [
        # top-level config source is the lowest level
        ({"source": "databricks"}, {}, None, None, "databricks", "config"),
        # per-dataset config beats top-level config
        ({"source": "databricks", "datasets": {"osm_roads": {"source": "local"}}}, {}, None, None, "local", "config"),
        # MAPQA_SOURCE beats config
        ({"datasets": {"osm_roads": {"source": "local"}}}, {"MAPQA_SOURCE": "databricks"}, None, None, "databricks", "env"),
        # MAPQA_SOURCE_OSM_ROADS beats MAPQA_SOURCE
        ({}, {"MAPQA_SOURCE": "databricks", "MAPQA_SOURCE_OSM_ROADS": "local"}, None, None, "local", "env"),
        # --source beats env and config
        ({"source": "databricks"}, {"MAPQA_SOURCE_OSM_ROADS": "databricks"}, "local", None, "local", "option"),
        # --source-osm-roads beats --source
        ({}, {}, "local", "databricks", "databricks", "option"),
    ],
)
def test_osm_roads_source_precedence(tmp_path, file_data, env, option_source, option_osm, expected_kind, expected_by):
    cfg = write_config(tmp_path, file_data)

    settings = resolve(config=str(cfg), source=option_source, source_osm_roads=option_osm, env=env)

    assert settings.sources["osm_roads"].kind == expected_kind
    assert settings.sources["osm_roads"].chosen_by == expected_by


def test_source_option_leaves_other_datasets_local(tmp_path):
    settings = resolve(source="databricks")

    assert settings.sources["osm_roads"].kind == "databricks"
    assert settings.sources["osm_roads"].chosen_by == "option"
    for name in ("signs", "nearest_osm", "crops"):
        assert settings.sources[name].kind == "local"
        assert settings.sources[name].chosen_by == "default"


def test_env_source_applies_to_osm_roads_only():
    settings = resolve(env={"MAPQA_SOURCE": "databricks"})

    assert settings.sources["osm_roads"].kind == "databricks"
    assert settings.sources["osm_roads"].chosen_by == "env"
    assert settings.sources["signs"].kind == "local"
    assert settings.sources["signs"].chosen_by == "default"


# --- local overrides and databricks location -----------------------------------------------------


def test_config_local_overrides_are_used_with_default_chosen_by(tmp_path):
    cfg = write_config(
        tmp_path,
        {
            "datasets": {
                "osm_roads": {"local": "roads/custom.parquet"},
                "signs": {"local": "custom/signs.csv"},
                "nearest_osm": {"local": "custom/nearest.csv"},
                "crops": {"local": "custom/crops/"},
            }
        },
    )

    settings = resolve(config=str(cfg))

    assert settings.sources["osm_roads"].location == "roads/custom.parquet"
    assert settings.sources["osm_roads"].chosen_by == "default"
    assert settings.sources["signs"].location == "custom/signs.csv"
    assert settings.sources["signs"].chosen_by == "default"
    assert settings.sources["nearest_osm"].location == "custom/nearest.csv"
    assert settings.sources["crops"].location == "custom/crops/"


def test_databricks_location_from_config_is_used_for_osm_roads(tmp_path):
    cfg = write_config(tmp_path, {"datasets": {"osm_roads": {"databricks": "main.maps.roads_2026"}}})

    settings = resolve(config=str(cfg), source="databricks")

    assert settings.sources["osm_roads"].location == "main.maps.roads_2026"


def test_other_datasets_stay_local_even_when_config_names_source(tmp_path):
    cfg = write_config(tmp_path, {"datasets": {"signs": {"source": "local", "local": "x/signs.csv"}}})

    settings = resolve(config=str(cfg), source="databricks")

    assert settings.sources["signs"].kind == "local"
    assert settings.sources["signs"].location == "x/signs.csv"


# --- model mode ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected_mode", "expected_by"),
    [
        ({"model_mode": "cache", "env": {"MAPQA_MODEL_MODE": "live"}}, "cache", "option"),
        ({"env": {"MAPQA_MODEL_MODE": "live"}}, "live", "env"),
        ({"config": "cfg", "env": {}}, "cache", "config"),
        ({}, "off", "default"),
    ],
)
def test_model_mode_precedence(tmp_path, kwargs, expected_mode, expected_by):
    if kwargs.get("config") == "cfg":
        kwargs = dict(kwargs, config=str(write_config(tmp_path, {"model_mode": "cache"})))

    settings = resolve(**kwargs)

    assert settings.model_mode == expected_mode
    assert settings.model_mode_chosen_by == expected_by


# --- data_dir ------------------------------------------------------------------------------------


def test_data_dir_env_beats_config(tmp_path):
    cfg = write_config(tmp_path, {"data_dir": "from-config"})
    env_dir = tmp_path / "from-env"

    settings = resolve(config=str(cfg), env={"MAPQA_DATA_DIR": str(env_dir)})

    assert settings.data_dir == env_dir


def test_relative_config_data_dir_resolves_against_config_folder(tmp_path):
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    cfg = write_config(config_dir, {"data_dir": "../data"})

    settings = resolve(config=str(cfg))

    assert settings.data_dir == (tmp_path / "data").resolve()


def test_config_data_dir_beats_default(tmp_path):
    cfg = write_config(tmp_path, {"data_dir": "inputs"})

    settings = resolve(config=str(cfg))

    assert settings.data_dir == (tmp_path / "inputs").resolve()


# --- config path selection -----------------------------------------------------------------------


def test_config_option_beats_mapqa_config_env(tmp_path):
    option_cfg = write_config(tmp_path, {"source": "databricks"}, name="option.json")
    env_cfg = write_config(tmp_path, {"source": "local"}, name="env.json")

    settings = resolve(config=str(option_cfg), env={"MAPQA_CONFIG": str(env_cfg)})

    assert settings.config_path == option_cfg
    assert settings.sources["osm_roads"].kind == "databricks"


def test_mapqa_config_env_is_used_when_no_option(tmp_path):
    env_cfg = write_config(tmp_path, {"source": "databricks"})

    settings = resolve(env={"MAPQA_CONFIG": str(env_cfg)})

    assert settings.config_path == env_cfg
    assert settings.sources["osm_roads"].kind == "databricks"
    assert settings.sources["osm_roads"].chosen_by == "config"


# --- databricks environment names and warehouse ---------------------------------------------------


def test_databricks_env_names_come_from_config(tmp_path):
    cfg = write_config(
        tmp_path,
        {
            "databricks": {
                "host_env": "MY_HOST",
                "token_env": "MY_TOKEN",
                "warehouse_env": "MY_WAREHOUSE",
                "warehouse_id": "97c455ee6fb1fcb9",
            }
        },
    )

    settings = resolve(config=str(cfg))

    assert settings.databricks.host_env == "MY_HOST"
    assert settings.databricks.token_env == "MY_TOKEN"
    assert settings.databricks.warehouse_env == "MY_WAREHOUSE"
    assert settings.databricks.warehouse_id == "97c455ee6fb1fcb9"


def test_warehouse_env_overrides_config_warehouse_id(tmp_path):
    cfg = write_config(tmp_path, {"databricks": {"warehouse_id": "aaaa", "warehouse_env": "WH"}})

    settings = resolve(config=str(cfg), env={"WH": "bbbb"})

    assert settings.databricks.warehouse_id == "bbbb"


# --- load_config_file: file errors ---------------------------------------------------------------


def test_missing_config_file_is_rejected(tmp_path):
    missing = tmp_path / "nope.json"

    with pytest.raises(MapQaError, match="config: file not found: ") as exc:
        load_config_file(missing)
    assert str(missing) in str(exc.value)


def test_invalid_json_is_rejected(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(MapQaError, match="config: invalid JSON in ") as exc:
        load_config_file(path)
    assert str(path) in str(exc.value)


# --- secrets (AC-40) -----------------------------------------------------------------------------

SECRET_CASES = [
    pytest.param({"token": "abc123xyz"}, "token", "abc123xyz", id="key-token"),
    pytest.param({"Token": "tok-upper-9"}, "Token", "tok-upper-9", id="key-token-case"),
    pytest.param({"databricks": {"password": "hunter2pw"}}, "databricks.password", "hunter2pw", id="nested-password"),
    pytest.param({"api_key": "apikeyvalue42"}, "api_key", "apikeyvalue42", id="key-api_key"),
    pytest.param({"secret": "s3cr3tvalue"}, "secret", "s3cr3tvalue", id="key-secret"),
    pytest.param({"note": "dapiXXXX1234"}, "note", "dapiXXXX1234", id="dapi-value-innocent-key"),
    pytest.param(
        {"databricks": {"token_env": "dapi123"}}, "databricks.token_env", "dapi123", id="env-name-not-a-name"
    ),
    pytest.param({"datasets": {"osm_roads": {"note": "Bearer abc"}}}, "datasets.osm_roads.note", "Bearer abc", id="bearer"),
    pytest.param({"note": "sk-live9876"}, "note", "sk-live9876", id="sk-value"),
    pytest.param({"note": "ctx7skABC999"}, "note", "ctx7skABC999", id="ctx7sk-value"),
    pytest.param(
        {"datasets": {"crops": [{"token": "zz-list-secret"}]}},
        "datasets.crops[0].token",
        "zz-list-secret",
        id="inside-list",
    ),
]


@pytest.mark.parametrize(("data", "field", "value"), SECRET_CASES)
def test_credentials_in_config_are_rejected_without_printing_value(tmp_path, data, field, value):
    cfg = write_config(tmp_path, data)

    with pytest.raises(MapQaError) as exc:
        load_config_file(cfg)

    message = str(exc.value)
    assert field in message
    assert "looks like a credential" in message
    assert value not in message


def test_credentials_are_rejected_through_resolve_settings(tmp_path):
    cfg = write_config(tmp_path, {"databricks": {"token": "dapiSECRETVALUE"}})

    with pytest.raises(MapQaError, match="databricks.token") as exc:
        resolve(config=str(cfg))
    assert "dapiSECRETVALUE" not in str(exc.value)


ACCEPTED_CASES = [
    pytest.param({"databricks": {"warehouse_id": "97c455ee6fb1fcb9"}}, id="warehouse-id"),
    pytest.param({"datasets": {"osm_roads": {"sha256": "0123456789abcdef" * 4}}}, id="sha256-hex"),
    pytest.param({"databricks": {"host_env": "DATABRICKS_HOST"}}, id="host-env-name"),
    pytest.param({"databricks": {"token_env": "DATABRICKS_TOKEN"}}, id="token-env-name"),
]


@pytest.mark.parametrize("data", ACCEPTED_CASES)
def test_non_secret_values_are_accepted(tmp_path, data):
    cfg = write_config(tmp_path, data)

    assert load_config_file(cfg) == data


# --- validation of source, location and model mode -----------------------------------------------


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"source": "s3"}, "source"),
        ({"datasets": {"osm_roads": {"source": "s3"}}}, "datasets.osm_roads.source"),
        ({"datasets": {"osm_roads": {"databricks": "workspace.map_qa.osm_roads; DROP"}}}, "datasets.osm_roads.databricks"),
        ({"datasets": {"osm_roads": {"databricks": "a.b"}}}, "datasets.osm_roads.databricks"),
        ({"model_mode": "turbo"}, "model_mode"),
    ],
)
def test_invalid_config_values_name_the_field(tmp_path, data, field):
    cfg = write_config(tmp_path, data)

    with pytest.raises(MapQaError, match=re.escape(f"'{field}'")) as exc:
        resolve(config=str(cfg))
    assert "config" in str(exc.value)


def test_bad_databricks_table_name_is_rejected_when_loading(tmp_path):
    cfg = write_config(tmp_path, {"datasets": {"osm_roads": {"databricks": "workspace.map_qa.osm_roads; DROP"}}})

    with pytest.raises(MapQaError, match="datasets.osm_roads.databricks"):
        load_config_file(cfg)


def test_bad_source_option_is_rejected_and_names_option():
    with pytest.raises(MapQaError, match="--source") as exc:
        resolve(source="s3")
    assert "--source-osm-roads" not in str(exc.value)


def test_bad_source_option_for_osm_roads_names_option():
    with pytest.raises(MapQaError, match="--source-osm-roads"):
        resolve(source_osm_roads="s3")


def test_bad_source_env_is_rejected_and_names_variable():
    with pytest.raises(MapQaError, match="MAPQA_SOURCE"):
        resolve(env={"MAPQA_SOURCE": "s3"})


def test_bad_source_osm_roads_env_is_rejected_and_names_variable():
    with pytest.raises(MapQaError, match="MAPQA_SOURCE_OSM_ROADS"):
        resolve(env={"MAPQA_SOURCE_OSM_ROADS": "s3"})


@pytest.mark.parametrize(
    ("kwargs", "where"),
    [
        ({"model_mode": "turbo"}, "--model-mode"),
        ({"env": {"MAPQA_MODEL_MODE": "turbo"}}, "MAPQA_MODEL_MODE"),
    ],
)
def test_bad_model_mode_names_its_origin(kwargs, where):
    with pytest.raises(MapQaError, match=where):
        resolve(**kwargs)


# --- the example configuration -------------------------------------------------------------------


def test_example_config_loads_and_passes_validation():
    data = load_config_file(EXAMPLE_CONFIG)

    assert data["data_dir"] == "../course-materials/exercise-data/map-qa"


def test_example_config_resolves_osm_roads_as_local():
    settings = resolve(config=str(EXAMPLE_CONFIG))

    assert settings.config_path == EXAMPLE_CONFIG
    assert settings.sources["osm_roads"].kind == "local"
    assert settings.sources["osm_roads"].location == "osm_roads.parquet"
    assert settings.sources["osm_roads"].chosen_by == "config"
    assert settings.data_dir == (APP_DIR / "../course-materials/exercise-data/map-qa").resolve()
