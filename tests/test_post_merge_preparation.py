"""Recover a clean checkout failure after merge without repeating delivery effects."""

import copy
from pathlib import Path

import pytest

from dutyloop import issues
from dutyloop.issue_store import IssueStore
from dutyloop.workspace import WorkspacePreparationError, git
import test_issue_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


@pytest.fixture
def failed_post_merge(batch, monkeypatch):
    project, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    issues.initialize(spec, state, github=github)
    original = issues.make_worktree
    attempts = []

    def fail(project, directory, head):
        if Path(directory).parent.name == "post-merge":
            attempts.append({"path": str(directory), "head": head})
            raise WorkspacePreparationError(directory, head, OSError("No space left on device"))
        return original(project, directory, head)

    monkeypatch.setattr(issues, "make_worktree", fail)
    host = workflow.Host(decisions=())
    with pytest.raises(WorkspacePreparationError):
        issues.run(state, driver=host, github=github)
    failed = issues.status(state)
    row = failed["issues"][0]
    assert failed["status"] == "blocked" and failed["reason"] == "workspace_preparation_failed"
    assert row["state"] == "merged" and row.get("post_merge_verified") is None
    assert attempts and attempts[0]["head"] == row["merge_sha"]
    assert host.calls == ["discover", "discover", "implement", "review"]
    assert github.writes == [("create", 100), ("merge", 100)] and github.issues[1]["state"] == "open"
    monkeypatch.setattr(issues, "make_worktree", original)
    return project, state, github, failed


def test_post_merge_checkout_recovery_verifies_then_closes_without_replaying_models_or_merge(
        failed_post_merge, monkeypatch):
    project, state, github, failed = failed_post_merge
    original_calls = copy.deepcopy(failed["calls"])
    with pytest.raises(ValueError):
        issues.recover(state, github=github)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] == "ready" and recovered["issues"][0]["state"] == "merged"
    assert recovered["issues"][0].get("post_merge_verified") is None
    assert recovered["calls"] == original_calls and recovered["invocations"] == failed["invocations"]
    assert recovered["deadline"] == failed["deadline"] and github.writes == [("create", 100), ("merge", 100)]

    original = issues.run_process
    checks = []

    def verify(*args, **kwargs):
        if Path(kwargs["cwd"]).parent.name == "post-merge":
            checks.append(str(kwargs["cwd"]))
            assert github.issues[1]["state"] == "open"
        return original(*args, **kwargs)

    monkeypatch.setattr(issues, "run_process", verify)
    host = workflow.Host(decisions=())
    result = issues.run(state, driver=host, github=github)
    row = result["issues"][0]
    assert checks and host.calls == []
    assert row["state"] == "closed" and row["post_merge_verified"] == row["merge_sha"]
    assert all(check["cleanup_confirmed"] and check["returncode"] == 0 for check in row["post_merge_checks"])
    assert result["calls"] == original_calls and result["invocations"] == failed["invocations"]
    assert result["deadline"] == failed["deadline"]
    assert result["planning"]["revision"] == failed["planning"]["revision"]
    assert github.writes == [("create", 100), ("merge", 100), ("close", 1)]
    assert git(project, "show", "HEAD:app.py") == "value = 0"


@pytest.mark.parametrize("guard", ["stop", "deadline", "invocations"])
def test_post_merge_checkout_recovery_cannot_override_stop_or_limits(failed_post_merge, monkeypatch, guard):
    _, state, github, failed = failed_post_merge
    store = IssueStore(state)
    if guard == "stop":
        issues.stop(state)
    elif guard == "deadline":
        monkeypatch.setattr(issues.time, "time", lambda: failed["deadline"] + 1)
    else:
        with store.connection() as db:
            data = store._read(db)
            data["spec"]["limits"]["max_invocations"] = data["invocations"]
            store._save(db, data)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] in {"blocked", "limited", "stopped"}
    host = workflow.Host(decisions=())
    result = issues.run(state, driver=host, github=github)
    assert result["issues"][0]["state"] == "merged" and result["issues"][0].get("post_merge_verified") is None
    assert host.calls == [] and result["calls"] == failed["calls"]
    assert result["deadline"] == failed["deadline"] and result["invocations"] == failed["invocations"]
    assert github.writes == [("create", 100), ("merge", 100)] and github.issues[1]["state"] == "open"


def test_post_merge_preparation_recovery_rejects_receipts_without_exact_clean_ownership(failed_post_merge):
    _, state, github, failed = failed_post_merge
    store = IssueStore(state)
    receipt = failed["preparation_failure"]
    corrupted = {
        "head": "f" * 40,
        "worktree": str(state / "unowned" / Path(receipt["worktree"]).name),
        "dispatch_started": True,
        "cleanup_confirmed": False,
        "issue_number": 999,
    }
    for field, value in corrupted.items():
        store.set_status("blocked", "workspace_preparation_failed",
                         preparation_failure={**copy.deepcopy(receipt), field: value})
        recovered = issues.recover(state, acknowledge=True, github=github)
        assert recovered["status"] == "blocked", field
        assert recovered["issues"][0]["state"] == "merged", field
        assert recovered["issues"][0].get("post_merge_verified") is None, field
        host = workflow.Host(decisions=())
        result = issues.run(state, driver=host, github=github)
        assert host.calls == [] and result["calls"] == failed["calls"], field
        assert result["deadline"] == failed["deadline"] and result["invocations"] == failed["invocations"], field
        assert github.writes == [("create", 100), ("merge", 100)] and github.issues[1]["state"] == "open", field
