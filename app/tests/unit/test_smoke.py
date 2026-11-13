from conftest import run_cli


def test_help_lists_commands():
    result = run_cli(["--help"])
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout
    assert "evaluate" in result.stdout


def test_network_guard_blocks_child_sockets(no_network_env):
    import subprocess
    import sys

    code = "import socket; socket.create_connection(('example.invalid', 80))"
    result = subprocess.run([sys.executable, "-c", code], env=no_network_env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "NetworkBlocked" in result.stderr
