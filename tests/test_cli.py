import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dutyloop import cli


def test_init_reads_spec_and_outputs_json(tmp_path, monkeypatch, capsys):
    spec = {"mission": "Repair the project"}
    spec_file = tmp_path / "mission.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    state = tmp_path / "state"
    calls = []
    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(
        initialize=lambda supplied, directory: calls.append((supplied, directory)) or {"status": "ready"}
    ))

    assert cli.main(["init", "--spec", str(spec_file), "--state", str(state)]) == 0
    assert calls == [(spec, state)]
    assert json.loads(capsys.readouterr().out) == {"status": "ready"}


@pytest.mark.parametrize("command,method", [("run", "run"), ("status", "status"), ("stop", "request_stop")])
def test_commands_dispatch_to_engine(command, method, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(**{
        method: lambda state: calls.append(state) or {"status": command}
    }))
    assert cli.main([command, "--state", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert json.loads(capsys.readouterr().out)["status"] == command


def test_recover_requires_explicit_acknowledgment(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(
        recover=lambda state, *, acknowledge: calls.append(acknowledge) or {"status": "ready"}
    ))
    assert cli.main(["recover", "--state", str(tmp_path)]) == 0
    assert cli.main(["recover", "--state", str(tmp_path), "--acknowledge"]) == 0
    assert calls == [False, True]
    capsys.readouterr()


def test_errors_are_json_and_nonzero(tmp_path, monkeypatch, capsys):
    def failing(_state):
        raise ValueError("Invalid mission configuration")

    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(status=failing))
    assert cli.main(["status", "--state", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "ok": False,
        "error": {"type": "ValueError", "message": "Invalid mission configuration"},
    }


def test_start_launches_background_without_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(status=lambda state: {"status": "ready"}))

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    result = cli.start_background(tmp_path)
    argv, options = calls[0]
    assert argv == [sys.executable, "-m", "dutyloop", "run", "--state", str(tmp_path.resolve())]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["close_fds"] is True
    assert "shell" not in options or options["shell"] is False
    assert options["cwd"] == str(tmp_path.resolve())
    if sys.platform == "win32":
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert options["start_new_session"] is True
    assert result["pid"] == 1234
    assert result["status"] == "starting"
    assert (tmp_path / "runner.stdout.log").exists()
    assert (tmp_path / "runner.stderr.log").exists()


def test_start_does_not_launch_an_invalid_state(tmp_path, monkeypatch):
    def invalid(_state):
        raise ValueError("Mission does not exist")

    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(status=invalid))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="does not exist"):
        cli.start_background(tmp_path)


@pytest.mark.parametrize("snapshot", [
    {"status": "stopped"}, {"status": "limited"}, {"status": "blocked"},
    {"status": "waiting", "stop_requested": True},
])
def test_start_never_clears_stop_or_limits(tmp_path, monkeypatch, snapshot):
    monkeypatch.setattr(cli, "_engine", lambda: SimpleNamespace(status=lambda state: snapshot))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="cannot restart"):
        cli.start_background(tmp_path)


def test_doctor_only_probes_drivers(monkeypatch, capsys):
    probes = []
    monkeypatch.setattr(cli, "_probe_driver", lambda name: probes.append(name) or {"name": name, "available": False})
    assert cli.main(["doctor"]) == 0
    assert probes == ["codex", "claude"]
    assert len(json.loads(capsys.readouterr().out)["drivers"]) == 2


def test_mcp_command_passes_explicit_allowlist(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_serve_mcp", lambda root, projects: calls.append((root, projects)))
    assert cli.main(["mcp", "--root", str(tmp_path / "states"), "--allow-project", str(tmp_path)]) == 0
    assert calls == [(tmp_path / "states", [tmp_path])]
    assert capsys.readouterr().out == ""


def _real_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True, capture_output=True)
    (project / "health.txt").write_text("healthy\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "health.txt"], check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(project), "-c", "user.name=Fixture User", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Add health fixture",
    ], check=True, capture_output=True)
    return project


def _real_spec(project):
    return {
        "mission": "Inspect project health",
        "project": str(project),
        "allowed_paths": ["."],
        "verification": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "limits": {"max_invocations": 1, "max_wall_seconds": 10},
        "poll_seconds": 0.05,
    }


def test_cli_real_engine_initialize_status_and_stop_without_driver(tmp_path, capsys):
    project = _real_project(tmp_path)
    spec_file = tmp_path / "mission.json"
    spec_file.write_text(json.dumps(_real_spec(project)), encoding="utf-8")
    state = tmp_path / "state"
    assert cli.main(["init", "--spec", str(spec_file), "--state", str(state)]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "ready"
    assert created["invocations"] == 0
    assert created["branch"].startswith("work/")
    assert cli.main(["status", "--state", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["invocations"] == 0
    assert cli.main(["stop", "--state", str(state)]) == 0
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["status"] == "stopped"
    assert stopped["stop_requested"] is True
    assert stopped["calls"] == []
    assert not (state / "runner.stdout.log").exists()


def test_real_background_runner_finishes_under_local_fake_driver(tmp_path, monkeypatch):
    from dutyloop import engine

    project = _real_project(tmp_path)
    driver_script = tmp_path / "fake_driver.py"
    driver_script.write_text(
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'payload': {'status': 'wait', 'summary': 'No supported work', 'candidates': []}}))\n",
        encoding="utf-8",
    )
    spec = _real_spec(project)
    spec["driver"] = {"name": "command", "argv": [sys.executable, str(driver_script)]}
    state = tmp_path / "state"
    engine.initialize(spec, state)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    launched = cli.start_background(state)
    assert launched["status"] == "starting"
    until = time.monotonic() + 12
    snapshot = engine.status(state)
    while snapshot["status"] not in {"limited", "blocked", "stopped"} and time.monotonic() < until:
        time.sleep(0.05)
        snapshot = engine.status(state)
    if snapshot["status"] not in {"limited", "blocked", "stopped"}:
        engine.request_stop(state)
        pytest.fail("The bounded background runner did not finish")
    assert snapshot["status"] == "limited", (state / "runner.stderr.log").read_text(encoding="utf-8")
    assert snapshot["invocations"] == 1
    assert len(snapshot["calls"]) == 1
    assert snapshot["calls"][0]["state"] == "finished"
    assert (project / "health.txt").read_text(encoding="utf-8") == "healthy\n"
