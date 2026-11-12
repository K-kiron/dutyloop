"""CLI pipes and runner logs retain Unicode under inherited legacy encodings."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from dutyloop import engine
from dutyloop.store import Store
import test_cli as workflow


UNICODE_MISSION = "Inspect ⇒ café 漢字 🚀"


def legacy_env():
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "gbk"
    environment["PYTHONUTF8"] = "0"
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (source, environment.get("PYTHONPATH"))))
    return environment


def create_mission_state(tmp_path, mission=UNICODE_MISSION):
    state = tmp_path / "state"
    Store(state).create({"mission": mission, "project": str(tmp_path / "project"),
                         "driver": {"name": "desktop"},
                         "limits": {"max_invocations": 1, "max_wall_seconds": 10}},
                        "a" * 40, "work/encoding")
    return state


@pytest.fixture
def mission_state(tmp_path):
    return create_mission_state(tmp_path)


@pytest.mark.parametrize("human", [False, True])
def test_redirected_status_is_utf8_under_inherited_gbk(mission_state, human):
    result = subprocess.run([sys.executable, "-m", "dutyloop", "status", "--state", str(mission_state),
                             *(["--human"] if human else [])],
                            env=legacy_env(), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    output = result.stdout.decode("utf-8")
    assert UNICODE_MISSION in output
    if not human:
        assert json.loads(output)["mission"] == UNICODE_MISSION


def test_redirected_unicode_error_is_utf8_json_without_secondary_encoding_failure(tmp_path):
    missing = tmp_path / "missing-⇒-🚀"
    result = subprocess.run([sys.executable, "-m", "dutyloop", "status", "--state", str(missing)],
                            env=legacy_env(), capture_output=True, timeout=15)
    assert result.returncode == 1 and result.stdout == b""
    error = json.loads(result.stderr.decode("utf-8"))
    assert error["error"]["type"] == "ValueError"
    assert str(missing) in error["error"]["message"]
    assert "UnicodeEncodeError" not in result.stderr.decode("utf-8")


def test_direct_main_entrypoint_uses_utf8_without_changing_inherited_environment(mission_state):
    script = (
        "import os, sys\n"
        "from dutyloop.cli import main\n"
        "result = main(['status', '--state', sys.argv[1]])\n"
        "assert os.environ['PYTHONIOENCODING'] == 'gbk'\n"
        "raise SystemExit(result)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(mission_state)],
                            env=legacy_env(), capture_output=True, timeout=15)
    assert result.returncode == 0 and result.stderr == b""
    assert json.loads(result.stdout.decode("utf-8"))["mission"] == UNICODE_MISSION


def test_native_terminal_encoding_is_preserved(tmp_path):
    mission = "Inspect café 漢字"
    state = create_mission_state(tmp_path, mission)
    script = (
        "import io, json, sys\n"
        "from dutyloop.cli import main\n"
        "class Terminal(io.TextIOWrapper):\n"
        "    def isatty(self): return True\n"
        "original = sys.stdout\n"
        "sys.stdout = Terminal(original.buffer, encoding='gbk')\n"
        "result = main(['status', '--state', sys.argv[1], '--human'])\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write(json.dumps({'encoding': sys.stdout.encoding}))\n"
        "raise SystemExit(result)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(state)],
                            env=legacy_env(), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert mission in result.stdout.decode("gbk")
    assert json.loads(result.stderr.decode("utf-8"))["encoding"] == "gbk"


def test_unicode_argument_errors_are_utf8_before_dispatch():
    result = subprocess.run([sys.executable, "-m", "dutyloop", "unknown-⇒-🚀"],
                            env=legacy_env(), capture_output=True, timeout=15)
    assert result.returncode == 2 and result.stdout == b""
    assert "unknown-⇒-🚀" in result.stderr.decode("utf-8")


def test_background_runner_logs_are_utf8_under_inherited_gbk(tmp_path):
    project = workflow._real_project(tmp_path)
    driver = tmp_path / "fixture_driver.py"
    driver.write_text(
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "print('{\"payload\": {\"status\": \"wait\", \"summary\": \"No supported work\", \"candidates\": []}}')\n",
        encoding="utf-8",
    )
    spec = workflow._real_spec(project)
    spec.update(mission=UNICODE_MISSION, driver={"name": "command", "argv": [sys.executable, str(driver)]})
    state = tmp_path / "state"
    engine.initialize(spec, state)
    launched = subprocess.run([sys.executable, "-m", "dutyloop", "start", "--state", str(state)],
                              env=legacy_env(), capture_output=True, timeout=15)
    assert launched.returncode == 0, launched.stderr
    launch = json.loads(launched.stdout.decode("utf-8"))
    stdout, stderr = Path(launch["stdout"]), Path(launch["stderr"])
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if stdout.read_bytes().endswith(b"\n") or stderr.read_bytes().endswith(b"\n"):
                break
            time.sleep(.02)
        error_log = stderr.read_bytes().decode("utf-8")
        assert error_log == ""
        output = stdout.read_bytes().decode("utf-8")
        assert UNICODE_MISSION in output
        result = json.loads(output)
        assert result["status"] == "limited" and result["invocations"] == 1
        assert result["runner_pid"] is None
        assert result["mission"] == UNICODE_MISSION
        assert (project / "health.txt").read_text() == "healthy\n"
    finally:
        if engine.status(state)["runner_pid"] is not None:
            engine.request_stop(state)
            cleanup_deadline = time.monotonic() + 5
            while engine.status(state)["runner_pid"] is not None and time.monotonic() < cleanup_deadline:
                time.sleep(.02)
            assert engine.status(state)["runner_pid"] is None, "The bounded background runner did not stop"
