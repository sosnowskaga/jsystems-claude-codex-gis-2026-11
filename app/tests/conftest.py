"""Shared pytest configuration for the Map QA tests.

* ``live`` tests call real external services and are skipped unless ``--run-live`` is given.
* ``no_network`` blocks every socket connection in this process.
* ``no_network_env`` returns an environment for subprocesses that blocks sockets in the child too.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parent
DATA_DIR = REPO_ROOT / "course-materials" / "exercise-data" / "map-qa"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_GUARD = '''
import socket


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise NetworkBlocked("network access is blocked in this test")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
'''


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that call real external services"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live: calls real OpenRouter/Databricks endpoints; run with --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def _blocked(*args, **kwargs):
    raise RuntimeError("network access is blocked in this test")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture
def no_network_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    guard_dir = tmp_path_factory.mktemp("netguard")
    (guard_dir / "sitecustomize.py").write_text(_GUARD, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(guard_dir), env.get("PYTHONPATH", "")] if p)
    env["PYTHONUTF8"] = "1"
    return env


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


def run_cli(
    args: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI in a subprocess with the test interpreter."""
    if env is None:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "map_qa", *args],
        cwd=cwd or APP_DIR,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
