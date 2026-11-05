import subprocess
import sys
import shlex

import pytest

from dutyloop import diagnostics
from dutyloop.process import ProcessResult


def test_doctor_executes_only_version_and_git_reads(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    commands = []
    monkeypatch.setattr(diagnostics, "probe_driver", lambda name: {"name": name, "available": True, "executable": name})
    monkeypatch.setattr(diagnostics, "_native_argv", lambda name: [name])

    def run(argv, **options):
        commands.append(argv)
        assert options["timeout_seconds"] == 2
        if "--show-toplevel" in argv:
            return ProcessResult(returncode=0, stdout=str(project))
        return ProcessResult(returncode=0, stdout="fixture")

    monkeypatch.setattr(diagnostics, "run_process", run)
    result = diagnostics.doctor(driver="codex", project=project, timeout_seconds=2)
    assert commands[0] == ["codex", "--version"]
    assert all(argv[0] in {"codex", "git"} for argv in commands)
    assert not any("exec" in argv or "headless" in argv or "login" in argv for argv in commands)
    assert result["drivers"][0]["authentication"] == "not_checked"
    assert result["model_calls"] == 0


def test_doctor_reports_missing_tools_without_launching_them(monkeypatch):
    monkeypatch.setattr(diagnostics, "probe_driver", lambda name: {"name": name, "available": False, "executable": None})
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    monkeypatch.setattr(diagnostics, "run_process", lambda *args, **kwargs: pytest.fail("No executable exists"))
    result = diagnostics.doctor(driver="claude")
    assert result["drivers"][0]["status"] == "not_installed"
    assert result["git"]["available"] is False


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 61])
def test_doctor_rejects_unbounded_or_invalid_deadlines(timeout):
    with pytest.raises(ValueError):
        diagnostics.doctor(timeout_seconds=timeout)


def test_doctor_can_inspect_a_real_git_root_without_an_agent(tmp_path, monkeypatch):
    project = tmp_path / "project"
    subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    for args in (["add", "app.py"], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Add fixture"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    monkeypatch.setattr(diagnostics, "probe_driver", lambda name: {"name": name, "available": False, "executable": None})
    result = diagnostics.doctor(driver="codex", project=project)
    assert result["python"] == sys.version.split()[0]
    assert result["project"]["git_root"]
    assert len(result["project"]["committed_head"]) == 40
    assert result["project"]["uncommitted_changes"] is False


def test_doctor_rejects_unknown_protocol_version_and_disables_updates(monkeypatch):
    monkeypatch.setattr(diagnostics, "probe_driver", lambda name: {"name": name, "available": True, "executable": name})
    monkeypatch.setattr(diagnostics, "_native_argv", lambda name: [name])
    calls = []

    def run(argv, **options):
        calls.append((argv, options))
        return ProcessResult(returncode=0, stdout="2.3.0")

    monkeypatch.setattr(diagnostics, "run_process", run)
    result = diagnostics.doctor(driver="opencode")
    assert result["drivers"][0]["status"] == "unsupported_version"
    assert result["drivers"][0]["protocol_supported"] is False
    assert calls[0][1]["env"] == {"OPENCODE_DISABLE_AUTOUPDATE": "1"}
    assert calls[0][0] == ["opencode", "--version"]


def test_read_only_preflight_never_executes_repository_fsmonitor_hook(tmp_path, monkeypatch):
    from dutyloop.onboarding import validation_report

    project = tmp_path / "project"
    subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    for args in (["add", "app.py"], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Add fixture"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    hook = project / ".git" / "hooks" / "monitor"
    marker = hook.parent / "called"
    hook.write_text("#!/bin/sh\nprintf called > " + shlex.quote(marker.as_posix()) + "\nprintf 'token\\0'\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(project), "config", "core.fsmonitor", shlex.quote(hook.as_posix())], check=True)
    # Positive control: prove this Git/environment actually runs the hook.
    control = subprocess.run(["git", "-C", str(project), "status", "--porcelain"], check=True, capture_output=True)
    assert marker.exists(), control.stderr.decode("utf-8", "replace")
    marker.unlink()
    spec = {"mission": "Maintain reliability", "project": str(project), "allowed_paths": ["app.py"],
            "verification": [[sys.executable, "-c", "raise SystemExit(0)"]],
            "limits": {"max_invocations": 3, "max_wall_seconds": 60}}
    validation_report(spec, tmp_path / "state")
    assert not marker.exists()
    monkeypatch.setattr(diagnostics, "probe_driver", lambda name: {"name": name, "available": False, "executable": None})
    diagnostics.doctor(driver="codex", project=project)
    assert not marker.exists()
