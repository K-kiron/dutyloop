"""Desktop handoffs reuse the real acceptance path without a model CLI login."""

import json
from pathlib import Path
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import test_issue_workflow as workflow

from dutyloop import cli, drivers, issues, mcp_server
from dutyloop.config import validate_spec
from dutyloop.drivers import DriverRequest
from dutyloop.issue_store import IssueStore
from dutyloop.workspace import git

batch = workflow.batch
contained_batch = workflow.contained_batch


def test_connection_config_roundtrips_and_does_not_write_or_launch(tmp_path):
    project = tmp_path / "a project"
    project.mkdir()
    root = tmp_path / "states"
    claude = json.loads(mcp_server.desktop_configuration("claude", root, [project]))["mcpServers"]["dutyloop"]
    codex = tomllib.loads(mcp_server.desktop_configuration("codex", root, [project]))["mcp_servers"]["dutyloop"]
    assert claude["command"] == codex["command"] == sys.executable
    assert claude["args"] == codex["args"] == [
        "-m", "dutyloop", "mcp", "--root", str(root), "--allow-project", str(project),
    ]
    assert not root.exists()
    with pytest.raises(ValueError, match="outside"):
        mcp_server.desktop_configuration("claude", project / "states", [project])


def test_desktop_config_and_factory_never_resolve_native_executables(batch, monkeypatch):
    _, state, spec, _ = batch
    spec.pop("github")
    spec["driver"] = {"name": "desktop"}
    assert validate_spec(spec, state)["driver"] == {"name": "desktop"}
    monkeypatch.setattr(drivers, "_native_argv", lambda *_: pytest.fail("No model CLI may be resolved"))
    assert drivers.get_driver(spec["driver"], state_dir=state).__class__.__name__ == "DesktopDriver"
    with pytest.raises(ValueError, match="state directory"):
        drivers.get_driver(spec["driver"])
    for extra in ({"model": "another-model"}, {"argv": ["some-program"]}):
        spec["driver"] = {"name": "desktop", **extra}
        with pytest.raises(ValueError, match="connected host"):
            validate_spec(spec, state)


@pytest.mark.parametrize("fail_gate", [False, True])
def test_current_host_completes_backlog_through_operator_acceptance(batch, monkeypatch, fail_gate):
    project, state, spec, github = batch
    spec["driver"] = {"name": "desktop"}
    spec["queue"] = {"numbers": [1]}
    spec["call_timeout_seconds"] = 120
    monkeypatch.setattr(issues, "_client", lambda *_args, **_kwargs: github)
    monkeypatch.setattr(drivers, "_native_argv", lambda *_: pytest.fail("No model CLI may run"))
    controller = mcp_server.ControlPlane(state.parent, [project])
    created = controller.issues_create(state.name, spec)
    assert created["invocations"] == 0
    assert controller.desktop_next(state.name, "host", "main") == {"status": "waiting"}
    assert not (state / "desktop.sqlite3").exists()
    before = git(project, "rev-parse", "HEAD")
    host = workflow.Host(decisions=(), fail_gate=fail_gate)
    results, errors = [], []
    last_submission = None

    def run():
        try:
            results.append(issues.run(state, github=github))
        except BaseException as error:
            errors.append(error)

    runner = threading.Thread(target=run)
    runner.start()
    deadline = time.monotonic() + 150
    try:
        while runner.is_alive() and time.monotonic() < deadline:
            context = f"fresh-context-{len(host.calls)}"
            job = controller.desktop_next(state.name, "host", context)
            if job["status"] != "claimed":
                time.sleep(.02)
                continue
            request = DriverRequest(job["phase"], job["prompt"], Path(job["cwd"]), job["schema"],
                                    Path(job["output_dir"]), 120)
            result = host.run(request, lambda: False)
            receipt = controller.desktop_submit(state.name, job["request_id"], job["token"],
                                                "host", context, result.payload, True)
            assert receipt
            last_submission = (job, context, result.payload, receipt)
        runner.join(5)
        assert not runner.is_alive(), "Desktop coordinator did not finish"
        assert not errors
        row = results[0]["issues"][0]
        assert row["state"] == ("failed" if fail_gate else "accepted_local")
        assert host.calls == (["discover", "discover", "implement"] if fail_gate
                              else ["discover", "discover", "implement", "review"])
        assert results[0]["planning"]["revision"] == 1
        assert sum(call["issue_number"] is None for call in results[0]["calls"]) == 1
        assert results[0]["invocations"] == len(host.calls)
        assert git(project, "rev-parse", "HEAD") == before
        assert (project / "app.py").read_text() == "value = 0\n"
        assert github.writes == []
        assert all(call["cleanup_scope"] == "host_declaration" for call in results[0]["calls"])
        assert all(call.get("cost_usd") is None for call in results[0]["calls"])
        last_job, last_context, last_payload, last_receipt = last_submission
        assert controller.desktop_submit(state.name, last_job["request_id"], last_job["token"],
                                         "host", last_context, last_payload, True) == last_receipt
        if not fail_gate:
            assert row["verification"][0]["returncode"] == 0
            assert row["verified_tree"] == git(project, "rev-parse", f"{row['head']}^{{tree}}")
    finally:
        if runner.is_alive():
            issues.stop(state)
            runner.join(10)


@pytest.mark.parametrize("status", ["stopped", "blocked", "limited", "completed"])
def test_terminal_batches_cannot_claim_or_submit_desktop_work(batch, monkeypatch, status):
    project, state, spec, github = batch
    spec["driver"] = {"name": "desktop"}
    issues.initialize(spec, state, github=github)
    IssueStore(state).set_status(status)
    controller = mcp_server.ControlPlane(state.parent, [project])
    assert controller.desktop_next(state.name, "host", "context")["work_available"] is False
    with pytest.raises(ValueError, match="exhausted"):
        controller.desktop_submit(state.name, "id", "token", "host", "context", {}, True)
    assert not (state / "desktop.sqlite3").exists()


def test_desktop_tools_recheck_stored_project_and_mode(batch):
    project, state, spec, github = batch
    issues.initialize(spec, state, github=github)
    controller = mcp_server.ControlPlane(state.parent, [project])
    with pytest.raises(ValueError, match="driver.name=desktop"):
        controller.desktop_next(state.name, "host", "context")
    with pytest.raises(ValueError, match="kind"):
        controller.desktop_next(state.name, "host", "context", "other")
    other = project.parent / "other"
    other.mkdir()
    outside = mcp_server.ControlPlane(state.parent, [other])
    with pytest.raises(ValueError, match="allowlist"):
        outside.issues_status(state.name)


def test_mcp_issue_start_supports_plan_only_without_dispatching_here(batch, monkeypatch):
    project, state, spec, github = batch
    spec["driver"] = {"name": "desktop"}
    issues.initialize(spec, state, github=github)
    calls = []
    monkeypatch.setattr(mcp_server, "start_background", lambda path, **options: calls.append((path, options)) or {})
    controller = mcp_server.ControlPlane(state.parent, [project])
    controller.issues_start(state.name, plan_only=True)
    assert calls == [(state, {"issue_batch": True, "plan_only": True})]
    assert controller.issues_status(state.name)["invocations"] == 0


def test_background_plan_only_flag_is_passed_to_issue_runner(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(issues, "status", lambda *_: {"status": "ready"})
    calls = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(pid=123))
    cli.start_background(tmp_path, issue_batch=True, plan_only=True)
    assert calls[0] == [sys.executable, "-m", "dutyloop", "issues", "run", "--state", str(tmp_path), "--plan-only"]


def test_state_name_cannot_mix_mission_and_issue_databases(batch):
    project, state, spec, github = batch
    issues.initialize(spec, state, github=github)
    controller = mcp_server.ControlPlane(state.parent, [project])
    with pytest.raises(ValueError, match="issue batch"):
        controller.mission_create(state.name, spec)
    assert not (state / "mission.sqlite3").exists()


def test_concurrent_mission_and_issue_creation_are_serialized(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    initializers, errors = [], []
    start = threading.Barrier(2)
    both_entered = threading.Event()

    def initialize(kind, state):
        initializers.append(kind)
        if len(initializers) == 2:
            both_entered.set()
        both_entered.wait(.15)
        (state / f"{kind}.sqlite3").touch()
        return {}

    monkeypatch.setattr(mcp_server, "_engine", lambda: SimpleNamespace(
        initialize=lambda spec, state: initialize("mission", state)))
    monkeypatch.setattr(issues, "initialize", lambda spec, state: initialize("issues", state))

    def create(kind):
        start.wait()
        try:
            getattr(controller, f"{kind}_create")("same-name", {"driver": {"name": "desktop"}})
        except (ValueError, RuntimeError) as error:
            errors.append(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(create, ["mission", "issues"]))
    assert len(initializers) == 1
    assert len(errors) == 1
    assert len(list((controller.root / "same-name").glob("*.sqlite3"))) == 1


def test_mixed_state_is_rejected_before_reading_either_database(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    state = controller.root / "ambiguous"
    state.mkdir(parents=True)
    (state / "mission.sqlite3").touch()
    (state / "issues.sqlite3").touch()
    for action in (controller.mission_status, controller.issues_status):
        with pytest.raises(ValueError, match="Ambiguous"):
            action("ambiguous")


def test_failed_remote_reconciliation_still_invalidates_desktop_claims(batch):
    import test_issue_safety as safety
    from dutyloop.desktop import DesktopDriver, DesktopQueue
    project, state, spec, _ = batch
    spec["driver"] = {"name": "desktop"}
    store, runner, github = safety.published(batch)
    driver = DesktopDriver(state)
    stop = threading.Event()
    results = []
    request = DriverRequest("discover", "Read only", project, {"type": "object"}, state / "orphan-output", 30)
    thread = threading.Thread(target=lambda: results.append(driver.run(request, stop.is_set)))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        queue = DesktopQueue(state)
        while queue.claim("orphan", "old-context")["status"] != "claimed":
            assert time.monotonic() < deadline
            time.sleep(.01)
        store.update_issue(1, "publishing")
        store.set_status("blocked", "github_outcome_unknown", runner_pid=None)
        before = store.control()
        github.prs[100]["body"] = "Changed outside this batch"
        with pytest.raises(RuntimeError, match="metadata differs"):
            issues.recover(state, acknowledge=True, github=github)
        assert queue.claim("another-worker", "new-context") == {"status": "waiting"}
        thread.join(5)
        assert not thread.is_alive()
        assert results[0].cleanup_confirmed is False
        assert store.control()["invocations"] == before["invocations"]
        assert store.control()["deadline"] == before["deadline"]
        assert github.writes == [("create", 100)]
    finally:
        stop.set()
        thread.join(5)
