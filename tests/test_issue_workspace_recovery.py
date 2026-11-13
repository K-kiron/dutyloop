"""Retry clean local workspace preparation without replacing a finite batch."""

import copy
import json
from pathlib import Path

import pytest

from dutyloop import engine, issues
from dutyloop.issue_store import IssueStore
from dutyloop.store import Store
from dutyloop.workspace import WorkspacePreparationError, git
import test_issue_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


class RecordingHost(workflow.Host):
    def __init__(self):
        super().__init__(decisions=())
        self.requests = []

    def run(self, request, should_stop):
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        self.requests.append({"phase": request.phase, "data": copy.deepcopy(data)})
        return super().run(request, should_stop)


@pytest.mark.parametrize("workspace_phase", ["observations", "worktrees"])
@pytest.mark.parametrize("legacy_reconciled,typed", [(False, False), (True, False), (False, True)],
                         ids=["initial-failure", "already-reconciled-row", "typed-preparation-failure"])
def test_clean_selected_workspace_failure_recovers_same_batch_without_reinvestigation(
        batch, monkeypatch, workspace_phase, legacy_reconciled, typed):
    project, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    store = IssueStore(state)
    store.update_issue(1, "needs_decision", reason="Choose the existing API behavior")
    decision = "Preserve the existing public API and implement the documented requirement"
    approved = issues.resolve(state, 1, decision, github=github)
    decision_id = approved["issues"][0]["decision_id"]
    original_worktree = engine.make_worktree
    failures = []

    def long_path_failure(project, directory, head):
        if Path(directory).parent.name == workspace_phase:
            failures.append(str(directory))
            error = RuntimeError(
                f"Preparing worktree (detached HEAD {head[:7]})\n"
                "error: unable to create file tests/deep/fixture.py: Filename too long")
            if typed:
                raise WorkspacePreparationError(directory, head, error)
            raise error
        return original_worktree(project, directory, head)

    monkeypatch.setattr(engine, "make_worktree", long_path_failure)
    first_host = RecordingHost()
    failed = issues.run(state, driver=first_host, github=github)
    assert failures and failed["status"] == "blocked"
    assert failed["issues"][0]["state"] in {"interrupted", "needs_decision"}
    assert failed["invocations"] == 2 and first_host.calls == ["discover", "discover"]
    assert failed["planning"]["status"] == "ready" and failed["planning"]["revision"] == 1
    child = Store(failed["issues"][0]["child_state"])
    assert child.snapshot()["calls"] == [] and child.snapshot()["tasks"] == []
    assert any("Filename too long" in event["data"].get("error", "") for event in child.events())
    original_calls = copy.deepcopy(failed["calls"])
    original_plan = copy.deepcopy(failed["issues"][0]["plan"])

    if legacy_reconciled:
        # Older recovery already changed the row, losing previous_state, while
        # leaving its parent blocked and its clean failed child as evidence.
        store.update_issue(1, "needs_decision", reason="interrupted_work_requires_inspection", previous_state=None)
        child.set_status("stopped", "operator_stop", runner_pid=None)

    monkeypatch.setattr(engine, "make_worktree", original_worktree)
    with pytest.raises(ValueError):
        issues.recover(state, github=github)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] == "ready"
    assert recovered["invocations"] == failed["invocations"] and recovered["deadline"] == failed["deadline"]
    assert recovered["calls"] == original_calls
    assert recovered["spec"] == failed["spec"]
    assert recovered["issues"][0]["decision"] == decision
    assert recovered["issues"][0]["decision_id"] == decision_id
    assert recovered["issues"][0]["plan"] == original_plan

    retry_host = RecordingHost()
    result = issues.run(state, driver=retry_host, github=github)
    assert result["issues"][0]["state"] == "accepted_local"
    assert retry_host.calls == ["implement", "review"]
    assert not any(request["data"].get("planning_operation") for request in retry_host.requests)
    assert result["planning"]["revision"] == failed["planning"]["revision"]
    assert result["issues"][0]["decision_id"] == decision_id
    assert result["deadline"] == failed["deadline"] and result["invocations"] == failed["invocations"] + 2
    assert result["calls"][:len(original_calls)] == original_calls
    assert result["spec"]["limits"] == failed["spec"]["limits"]
    assert git(project, "show", f"{result['accepted_head']}:app.py") == "value = 1"
    assert git(project, "show", "HEAD:app.py") == "value = 0" and github.writes == []


@pytest.fixture
def typed_failure(batch, monkeypatch):
    project, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    original = engine.make_worktree

    def fail(project, directory, head):
        raise WorkspacePreparationError(directory, head, RuntimeError("Filename too long"))

    monkeypatch.setattr(engine, "make_worktree", fail)
    failed = issues.run(state, driver=RecordingHost(), github=github)
    monkeypatch.setattr(engine, "make_worktree", original)
    assert failed["reason"] == "workspace_preparation_failed"
    return project, state, github, failed, IssueStore(state), Store(failed["issues"][0]["child_state"])


@pytest.mark.parametrize("guard", ["stopped", "deadline", "missing-child", "worker-started", "identity-changed"])
def test_preparation_retry_requires_clean_evidence_and_live_authority(typed_failure, monkeypatch, guard):
    project, state, github, failed, store, child = typed_failure
    missing_database = None
    if guard == "stopped":
        issues.stop(state)
    elif guard == "deadline":
        monkeypatch.setattr(issues.time, "time", lambda: failed["deadline"] + 1)
    elif guard == "missing-child":
        missing_database = child.directory / "mission.sqlite3"
        missing_database.rename(missing_database.with_suffix(".retained"))
    elif guard == "worker-started":
        child.set_status("ready")
        call = child.reserve_call("implement")
        child.finish_call(call, {"cleanup_confirmed": False, "cost_usd": None}, unknown=True)
        child.set_status("blocked", "workspace_preparation_failed")
        store.set_status("ready")
        parent_call = store.reserve_call("implement", 1)
        store.finish_call(parent_call, {"cleanup_confirmed": False, "cost_usd": None}, unknown=True)
        store.set_status("blocked", "workspace_preparation_failed")
    else:
        git(Path(child.control()["spec"]["project"]), "checkout", "-b", "fixture/changed-identity")
    before = issues.status(state)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] in {"blocked", "stopped", "limited"}
    assert recovered["invocations"] == before["invocations"] and recovered["deadline"] == before["deadline"]
    host = RecordingHost()
    result = issues.run(state, driver=host, github=github)
    assert host.calls == [] and result["issues"][0]["state"] != "accepted_local"
    if guard == "stopped":
        assert recovered["stop_requested"]
    elif guard == "worker-started":
        assert recovered["calls"][-1]["state"] == "unknown"
        assert child.snapshot()["calls"][-1]["state"] == "unknown"
    elif guard == "missing-child":
        assert not missing_database.exists()
    assert git(project, "show", "HEAD:app.py") == "value = 0" and github.writes == []


def test_clean_preparation_recovery_does_not_authorize_a_stale_detail_plan(typed_failure):
    project, state, github, failed, store, _ = typed_failure
    stale_planning = copy.deepcopy(failed["planning"])
    stale_planning["expected_head"] = "0" * 40
    store.save_planning(stale_planning)

    recovered = issues.recover(state, acknowledge=True, github=github)
    row = recovered["issues"][0]
    assert recovered["status"] == "ready" and row["state"] == "queued"
    assert row["plan"] == failed["issues"][0]["plan"] and row["plan_base_sha"] is None
    assert recovered["calls"] == failed["calls"] and recovered["deadline"] == failed["deadline"]

    host = RecordingHost()
    result = issues.run(state, driver=host, github=github)
    assert host.calls == ["discover", "discover", "implement", "review"]
    assert host.requests[0]["data"]["planning_operation"] == "roadmap"
    assert host.requests[1]["data"]["issue"]["number"] == 1
    assert result["issues"][0]["state"] == "accepted_local"
    assert result["planning"]["revision"] == failed["planning"]["revision"] + 1
    assert result["invocations"] == failed["invocations"] + 4 and result["deadline"] == failed["deadline"]
    assert git(project, "show", "HEAD:app.py") == "value = 0" and github.writes == []


@pytest.mark.parametrize("purpose", ["plan", "fix"])
def test_direct_issue_child_preparation_retry_preserves_ready_roadmap_and_decision(batch, monkeypatch, purpose):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    store = IssueStore(state)
    store.update_issue(1, "needs_decision", reason="Choose the public behavior")
    decision = "Preserve the current API and implement the stated requirement"
    issues.resolve(state, 1, decision, github=github)
    planned = issues.run(state, driver=RecordingHost(), github=github, plan_only=True)
    original = issues.make_worktree
    attempted = []

    def fail(project, directory, head):
        attempted.append(str(directory))
        if len(attempted) == (1 if purpose == "plan" else 2):
            raise WorkspacePreparationError(directory, head, RuntimeError("Filename too long"))
        return original(project, directory, head)

    monkeypatch.setattr(issues, "make_worktree", fail)
    first_host = RecordingHost()
    with pytest.raises(WorkspacePreparationError):
        issues.run(state, driver=first_host, github=github)
    failed = issues.status(state)
    assert failed["reason"] == "workspace_preparation_failed"
    assert failed["preparation_failure"]["purpose"] == purpose
    assert failed["preparation_failure"]["dispatch_started"] is False
    assert failed["invocations"] == (1 if purpose == "plan" else 2)
    monkeypatch.setattr(issues, "make_worktree", original)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] == "ready" and recovered["deadline"] == planned["deadline"]
    assert recovered["invocations"] == failed["invocations"] and recovered["calls"] == failed["calls"]
    assert recovered["issues"][0]["decision_id"] == planned["issues"][0]["decision_id"]
    assert recovered["issues"][0]["decision"] == decision
    host = RecordingHost()
    result = issues.run(state, driver=host, github=github)
    assert result["issues"][0]["state"] == "accepted_local"
    assert host.calls == (["discover", "implement", "review"] if purpose == "plan" else ["implement", "review"])
    assert not any(request["data"].get("planning_operation") for request in host.requests)
    assert result["planning"]["revision"] == planned["planning"]["revision"]
    assert result["deadline"] == planned["deadline"] and result["invocations"] == 4
    assert result["issues"][0]["decision_id"] == planned["issues"][0]["decision_id"] and github.writes == []


def test_backlog_child_preparation_retry_preserves_partial_chunk_receipts(batch, monkeypatch):
    _, state, spec, github = batch
    github.issues = {number: workflow.source(number) for number in range(1, 7)}
    for source in github.issues.values():
        source["body"] += "x" * 5000
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 1}
    issues.initialize(spec, state, github=github)
    original = issues.make_worktree
    attempted = []

    def fail(project, directory, head):
        attempted.append(str(directory))
        if len(attempted) == 2:
            raise WorkspacePreparationError(directory, head, RuntimeError("Filename too long"))
        return original(project, directory, head)

    monkeypatch.setattr(issues, "make_worktree", fail)
    with pytest.raises(WorkspacePreparationError):
        issues.run(state, driver=RecordingHost(), github=github, plan_only=True)
    failed = issues.status(state)
    assert failed["reason"] == "workspace_preparation_failed" and failed["preparation_failure"]["purpose"] == "backlog"
    assert failed["invocations"] == 1 and failed["planning"]["roadmap"] is None
    covered = set(failed["planning"]["coverage"]["assessed"])
    assert covered and failed["planning"]["coverage"]["unassessed"]
    monkeypatch.setattr(issues, "make_worktree", original)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] == "ready" and recovered["calls"] == failed["calls"]
    host = RecordingHost()
    result = issues.run(state, driver=host, github=github, plan_only=True)
    redispatched = [entry["number"] for request in host.requests
                    if request["data"].get("planning_operation") == "assess" for entry in request["data"]["backlog"]]
    assert set(redispatched) == set(range(1, 7)) - covered
    assert result["planning"]["revision"] == failed["planning"]["revision"]
    assert result["planning"]["coverage"]["unassessed"] == [] and result["planning"]["status"] == "ready"
    assert result["deadline"] == failed["deadline"] and result["invocations"] == len(redispatched) + 2
    assert github.writes == []
