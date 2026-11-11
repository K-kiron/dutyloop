"""Repository command boundaries use only offline control-plane fixtures."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dutyloop import cli, issues, repository


def test_repository_init_reads_spec_without_running_or_reconciling(tmp_path, monkeypatch, capsys):
    spec = {"mission": "Maintain the authorized repository", "github": {"repository": "example/app"}}
    path = tmp_path / "repository.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    calls = []
    monkeypatch.setattr(repository, "initialize", lambda supplied, state: calls.append((supplied, state)) or {"status": "ready"})
    monkeypatch.setattr(repository, "run", lambda *_args: pytest.fail("init must not run models"))
    monkeypatch.setattr(repository, "reconcile", lambda *_args: pytest.fail("init dispatches only initialize"))
    assert cli.main(["repository", "init", "--spec", str(path), "--state", str(tmp_path)]) == 0
    assert calls == [(spec, tmp_path)]
    assert json.loads(capsys.readouterr().out) == {"status": "ready"}


@pytest.mark.parametrize("action", ["run", "reconcile", "stop"])
def test_repository_actions_use_repository_coordinator(tmp_path, monkeypatch, capsys, action):
    calls = []
    monkeypatch.setattr(repository, action, lambda state: calls.append(state) or {"status": action})
    monkeypatch.setattr(cli, "_engine", lambda: pytest.fail("must not dispatch through mission state"))
    assert cli.main(["repository", action, "--state", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert json.loads(capsys.readouterr().out) == {"status": action}


def _snapshot():
    return {"status": "waiting_for_executor", "reason": "host_required", "invocations": 2,
            "spec": {"driver": {"name": "desktop"}, "github": {"repository": "example/app", "delivery": "local"},
                     "limits": {"max_invocations": 20}},
            "intake": {"mode": "observe"},
            "executor": {"mode": "desktop", "host_driven": True, "cannot_wake_host": True,
                         "status": "waiting_for_executor"},
            "attention": [{"id": "issue:12", "type": "decision", "issue_number": 12,
                           "reason": "Business direction required", "required_action": "Choose the export format"}],
            "issues": [{"number": 12, "state": "needs_decision", "source": {"title": "Choose export format"}}]}


def test_repository_human_status_exposes_attention_intake_and_executor(tmp_path, monkeypatch, capsys):
    snapshot = _snapshot()
    monkeypatch.setattr(repository, "status", lambda _state: snapshot)
    assert cli.main(["repository", "status", "--state", str(tmp_path), "--human"]) == 0
    text = capsys.readouterr().out
    for expected in ("waiting_for_executor", "Repository: example/app", "Intake: observe", "Delivery: local",
                     "Executor: desktop", "cannot wake", "Calls: 2/20", "Attention: 1", "#12 Choose the export format"):
        assert expected in text
    assert cli.main(["repository", "status", "--state", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == snapshot


def test_repository_attention_is_readonly_projection(tmp_path, monkeypatch, capsys):
    snapshot = _snapshot()
    monkeypatch.setattr(repository, "status", lambda _state: snapshot)
    monkeypatch.setattr(repository, "reconcile", lambda *_args: pytest.fail("attention must not reconcile"))
    monkeypatch.setattr(repository, "run", lambda *_args: pytest.fail("attention must not run"))
    assert cli.main(["repository", "attention", "--state", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"attention": snapshot["attention"]}
    assert not list(tmp_path.iterdir())


def test_repository_events_recovery_decision_and_mode_preserve_arguments(tmp_path, monkeypatch, capsys):
    calls = []
    for action in ("events", "recover", "resolve", "set_mode"):
        monkeypatch.setattr(repository, action, lambda *args, _action=action, **options:
                            calls.append((_action, args, options)) or {"status": "ready"})
    assert cli.main(["repository", "events", "--state", str(tmp_path), "--after", "14", "--limit", "5"]) == 0
    assert cli.main(["repository", "recover", "--state", str(tmp_path)]) == 0
    assert cli.main(["repository", "recover", "--state", str(tmp_path), "--acknowledge"]) == 0
    assert cli.main(["repository", "resolve", "--state", str(tmp_path), "--number", "12", "--decision", "Preserve CSV"]) == 0
    assert cli.main(["repository", "set-mode", "--state", str(tmp_path), "--mode", "auto"]) == 0
    assert calls == [("events", (tmp_path,), {"after_id": 14, "limit": 5}),
                     ("recover", (tmp_path,), {"acknowledge": False}),
                     ("recover", (tmp_path,), {"acknowledge": True}),
                     ("resolve", (tmp_path, 12, "Preserve CSV"), {}),
                     ("set_mode", (tmp_path, "auto"), {})]
    capsys.readouterr()


def test_repository_start_uses_separate_background_coordinator(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "start_background", lambda state, **options:
                        calls.append((state, options)) or {"status": "starting"})
    assert cli.main(["repository", "start", "--state", str(tmp_path)]) == 0
    assert calls == [(tmp_path, {"repository": True})]
    capsys.readouterr()


def test_repository_background_argv_is_explicit_without_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "status", lambda _state: {"status": "waiting_for_executor"})
    calls = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **options:
                        calls.append((argv, options)) or SimpleNamespace(pid=123))
    result = cli.start_background(tmp_path, repository=True)
    argv, options = calls[0]
    assert argv == [sys.executable, "-m", "dutyloop", "repository", "run", "--state", str(tmp_path.resolve())]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["close_fds"] is True
    assert not options.get("shell", False)
    assert options["cwd"] == str(tmp_path.resolve())
    assert result["pid"] == 123


@pytest.mark.parametrize("snapshot", [
    {"status": "stopped"}, {"status": "limited"}, {"status": "blocked"},
    {"status": "waiting", "stop_requested": True},
])
def test_repository_start_cannot_reset_stop_or_limits(tmp_path, monkeypatch, snapshot):
    monkeypatch.setattr(repository, "status", lambda _state: snapshot)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="cannot restart"):
        cli.start_background(tmp_path, repository=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("options", [{"issue_batch": True, "repository": True}, {"repository": True, "plan_only": True}])
def test_repository_start_rejects_mixed_authority(tmp_path, options):
    with pytest.raises(ValueError, match="either|plan_only"):
        cli.start_background(tmp_path, **options)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("action", ["run", "start", "status", "stop", "events", "recover", "resolve"])
def test_finite_issue_commands_cannot_change_repository_state(tmp_path, monkeypatch, capsys, action):
    monkeypatch.setattr(repository, "is_repository", lambda _state: True)
    monkeypatch.setattr(issues, "run", lambda *_args, **_kwargs: pytest.fail("finite coordinator must not run"))
    extra = ["--number", "1", "--decision", "Preserve behavior"] if action == "resolve" else []
    assert cli.main(["issues", action, "--state", str(tmp_path), *extra]) == 1
    assert "Repository maintenance state requires repository commands" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_repository_errors_use_existing_json_contract(tmp_path, monkeypatch, capsys):
    def fail(_state):
        raise ValueError("No repository maintenance exists")
    monkeypatch.setattr(repository, "status", fail)
    assert cli.main(["repository", "status", "--state", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"ok": False, "error": {"type": "ValueError", "message": "No repository maintenance exists"}}


@pytest.mark.parametrize("command", ["init", "run", "start", "status", "stop", "events", "recover"])
def test_mission_commands_do_not_repurpose_repository_database(tmp_path, monkeypatch, capsys, command):
    (tmp_path / "issues.sqlite3").touch()
    monkeypatch.setattr(cli, "_engine", lambda: pytest.fail("must reject issue state before accessing mission engine"))
    extra = ["--spec", str(tmp_path / "missing-spec.json")] if command == "init" else []
    assert cli.main([command, "--state", str(tmp_path), *extra]) == 1
    assert "belongs to an issue batch or repository" in capsys.readouterr().err
    assert not (tmp_path / "mission.sqlite3").exists()


@pytest.mark.parametrize("prefix", [["repository"], ["issues"]])
def test_repository_and_issue_init_do_not_repurpose_mission_database(tmp_path, monkeypatch, capsys, prefix):
    (tmp_path / "mission.sqlite3").touch()
    monkeypatch.setattr(repository, "initialize", lambda *_args: pytest.fail("must not initialize"))
    monkeypatch.setattr(issues, "initialize", lambda *_args: pytest.fail("must not initialize"))
    assert cli.main([*prefix, "init", "--state", str(tmp_path), "--spec", str(tmp_path / "missing-spec.json")]) == 1
    assert "belongs to a mission" in capsys.readouterr().err
    assert not (tmp_path / "issues.sqlite3").exists()


@pytest.mark.parametrize("prefix", [[], ["repository"], ["issues"]])
def test_ambiguous_state_is_rejected_before_reading_either_database(tmp_path, capsys, prefix):
    for name in ("mission.sqlite3", "issues.sqlite3"):
        (tmp_path / name).touch()
    assert cli.main([*prefix, "status", "--state", str(tmp_path)]) == 1
    assert "Ambiguous state" in capsys.readouterr().err
