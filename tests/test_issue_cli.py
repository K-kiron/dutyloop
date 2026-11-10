import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dutyloop import cli, issues


def test_issue_init_reads_spec_without_starting_runner(tmp_path, monkeypatch, capsys):
    spec = {"mission": "Resolve the captured backlog", "github": {"repository": "example/app"}}
    path = tmp_path / "issues.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    calls = []
    monkeypatch.setattr(issues, "initialize", lambda supplied, state: calls.append((supplied, state)) or {"status": "ready"})
    monkeypatch.setattr(issues, "run", lambda *args, **kwargs: pytest.fail("init must not run models"))
    assert cli.main(["issues", "init", "--spec", str(path), "--state", str(tmp_path)]) == 0
    assert calls == [(spec, tmp_path)]
    assert json.loads(capsys.readouterr().out) == {"status": "ready"}


@pytest.mark.parametrize("flags,plan_only", [([], False), (["--plan-only"], True)])
def test_issue_run_preserves_plan_only_boundary(tmp_path, monkeypatch, capsys, flags, plan_only):
    calls = []
    monkeypatch.setattr(issues, "run", lambda state, **kwargs: calls.append((state, kwargs)) or {"status": "planned"})
    assert cli.main(["issues", "run", "--state", str(tmp_path), *flags]) == 0
    assert calls == [(tmp_path, {"plan_only": plan_only})]
    assert json.loads(capsys.readouterr().out) == {"status": "planned"}


def test_issue_status_human_lists_blocked_and_actionable_rows(tmp_path, monkeypatch, capsys):
    snapshot = {"status": "waiting", "invocations": 2,
                "spec": {"github": {"repository": "example/app", "delivery": "local"},
                         "limits": {"max_invocations": 20}},
                "issues": [{"number": 12, "state": "needs_decision", "source": {"title": "Choose export format"},
                            "reason": "Business direction required"},
                           {"number": 13, "state": "planned", "source": {"title": "Repair escaping"}}]}
    monkeypatch.setattr(issues, "status", lambda state: snapshot)
    assert cli.main(["issues", "status", "--state", str(tmp_path), "--human"]) == 0
    text = capsys.readouterr().out
    assert "Repository: example/app" in text
    assert "Delivery: local" in text
    assert "Calls: 2/20" in text
    assert "#12 [needs_decision] Choose export format" in text
    assert "Business direction required" in text
    assert "#13 [planned] Repair escaping" in text
    assert cli.main(["issues", "status", "--state", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == snapshot


def test_issue_events_forwards_durable_cursor_and_limit(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(issues, "events", lambda state, **kwargs: calls.append((state, kwargs)) or {"events": [], "next_cursor": 14})
    assert cli.main(["issues", "events", "--state", str(tmp_path), "--after", "14", "--limit", "5"]) == 0
    assert calls == [(tmp_path, {"after_id": 14, "limit": 5})]
    assert json.loads(capsys.readouterr().out)["next_cursor"] == 14


def test_issue_stop_dispatches_to_batch_not_mission(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(issues, "stop", lambda state: calls.append(state) or {"stop_requested": True})
    monkeypatch.setattr(cli, "_engine", lambda: pytest.fail("issue commands must use batch state"))
    assert cli.main(["issues", "stop", "--state", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert json.loads(capsys.readouterr().out)["stop_requested"] is True


def test_issue_recover_never_invents_acknowledgment(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(issues, "recover", lambda state, **kwargs: calls.append((state, kwargs)) or {"status": "ready"})
    for flags in ([], ["--acknowledge"]):
        assert cli.main(["issues", "recover", "--state", str(tmp_path), *flags]) == 0
    assert calls == [(tmp_path, {"acknowledge": False}), (tmp_path, {"acknowledge": True})]
    capsys.readouterr()


def test_issue_resolve_forwards_operator_decision(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(issues, "resolve", lambda *args: calls.append(args) or {"status": "ready"})
    assert cli.main(["issues", "resolve", "--state", str(tmp_path), "--number", "12", "--decision", "Preserve the CSV format"]) == 0
    assert calls == [(tmp_path, 12, "Preserve the CSV format")]
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_issue_start_dispatches_background_batch(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "start_background", lambda state, **kwargs: calls.append((state, kwargs)) or {"status": "starting"})
    assert cli.main(["issues", "start", "--state", str(tmp_path)]) == 0
    assert calls == [(tmp_path, {"issue_batch": True})]
    capsys.readouterr()


def test_issue_background_process_uses_nested_command_without_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(issues, "status", lambda state: {"status": "ready"})
    monkeypatch.setattr(cli, "_engine", lambda: pytest.fail("must read issue-batch status"))

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    result = cli.start_background(tmp_path, issue_batch=True)
    argv, options = calls[0]
    assert argv == [sys.executable, "-m", "dutyloop", "issues", "run", "--state", str(tmp_path.resolve())]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["close_fds"] is True
    assert not options.get("shell", False)
    if sys.platform == "win32":
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert options["start_new_session"] is True
    assert result["pid"] == 123


def test_issue_cli_reports_validation_errors_as_nonzero_json(tmp_path, monkeypatch, capsys):
    def failure(_state):
        raise ValueError("Issue state does not exist")

    monkeypatch.setattr(issues, "status", failure)
    assert cli.main(["issues", "status", "--state", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error"]["message"] == "Issue state does not exist"
