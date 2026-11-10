"""Delivery boundaries use local bare Git repositories and simulated GitHub."""

import threading
import time

import pytest
import test_issue_workflow as workflow

from dutyloop import issues
from dutyloop.issue_store import IssueStore
from dutyloop.process import ProcessResult
from dutyloop.workspace import git

batch = workflow.batch
contained_batch = workflow.contained_batch


def published(batch):
    """Create a verified PR, then stop before polling CI or merging."""
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    issues.initialize(spec, state, github=github)
    store = IssueStore(state)
    assert store.begin()
    runner = issues.IssueRunner(store, workflow.Host(), threading.Event(), github)
    runner.plan(runner.row(1))
    runner.implement(runner.row(1))
    runner.publish(runner.row(1))
    return store, runner, github


@pytest.mark.parametrize("checks", [
    [],
    [{"name": "another-check", "status": "completed", "conclusion": "success"}],
    [{"name": "tests", "status": "in_progress", "conclusion": None}],
    [{"name": "tests", "status": "completed", "conclusion": "skipped"}],
    [{"name": "tests", "status": "completed", "conclusion": "neutral"}],
])
def test_required_checks_need_explicit_success_before_merge(batch, checks):
    store, runner, github = published(batch)
    github.check_results = checks
    runner.delivery(runner.row(1))
    assert store.snapshot()["issues"][0]["state"] == "pull_request"
    assert github.writes == [("create", 100)]
    assert github.issues[1]["state"] == "open"


def test_remote_pr_head_change_refuses_merge_even_with_green_checks(batch):
    store, runner, github = published(batch)
    row = runner.row(1)
    git(github.remote, "update-ref", f"refs/heads/{row['branch']}", row["base_sha"])
    with pytest.raises(RuntimeError, match="PR source, destination, or head"):
        runner.delivery(row)
    assert store.snapshot()["issues"][0]["state"] == "pull_request"
    assert github.writes == [("create", 100)]
    assert github.issues[1]["state"] == "open"


@pytest.mark.parametrize("previous", ["merging", "closing"])
def test_recovery_does_not_treat_external_closure_as_verified_acceptance(batch, previous):
    store, runner, github = published(batch)
    row = runner.row(1)
    github.merge_pull_request(row["pr_number"], row["head"], "merge")
    github.issues[1]["state"] = "closed"  # An external actor closed it before post-merge verification.
    store.update_issue(1, previous)
    store.set_status("blocked", "github_outcome_unknown", runner_pid=None)
    recovered = issues.recover(store.directory, acknowledge=True, github=github)
    assert recovered["issues"][0]["state"] == "merged"
    assert recovered["issues"][0]["merge_sha"] == row["head"]
    assert not recovered["issues"][0].get("post_merge_verified")
    assert github.writes == [("create", 100), ("merge", 100)]


@pytest.mark.parametrize("reason", ["batch_interrupted", "github_outcome_unknown"])
@pytest.mark.parametrize("limit", ["deadline", "invocations"])
def test_recovery_cannot_reopen_exhausted_global_limits(batch, reason, limit):
    _, state, spec, github = batch
    issues.initialize(spec, state, github=github)
    store = IssueStore(state)
    store.begin()
    store.set_status("blocked", reason, runner_pid=None)
    with store.connection() as db:
        data = store._read(db)
        if limit == "deadline":
            data["deadline"] = time.time() - 1
        else:
            data["invocations"] = spec["limits"]["max_invocations"]
        store._save(db, data)
    before = store.control()
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["status"] in {"limited", "blocked"}
    assert recovered["deadline"] == before["deadline"]
    assert recovered["invocations"] == before["invocations"]
    assert not recovered["stop_requested"]
    assert github.writes == []


def test_post_merge_unknown_cleanup_blocks_before_git_identity_or_issue_close(batch, monkeypatch):
    store, runner, github = published(batch)
    row = runner.row(1)
    github.merge_pull_request(row["pr_number"], row["head"], "merge")
    store.update_issue(1, "merged", merge_sha=row["head"])
    verification_calls = []

    def unknown_verification(*args, **kwargs):
        verification_calls.append(args)
        return ProcessResult(cleanup_confirmed=False)

    monkeypatch.setattr(issues, "run_process", unknown_verification)
    monkeypatch.setattr(issues, "assert_identity", lambda *args: pytest.fail("No Git identity check after unknown cleanup"))
    with pytest.raises(RuntimeError, match="cleanup is unknown"):
        runner.finish_merge(runner.row(1))
    result = store.snapshot()
    assert len(verification_calls) == 1
    assert result["status"] == "blocked"
    assert result["reason"] == "verification_cleanup_unconfirmed"
    assert result["issues"][0]["state"] == "merged"
    assert github.writes == [("create", 100), ("merge", 100)]
    assert github.issues[1]["state"] == "open"


def test_post_merge_tree_mismatch_refuses_checks_and_closure(batch, monkeypatch):
    project, _, _, _ = batch
    store, runner, github = published(batch)
    row = runner.row(1)
    github.merge_pull_request(row["pr_number"], row["head"], "merge")
    # Simulate an unexpected merge result with the pre-fix tree and correct ancestry.
    unchecked_tree = git(project, "rev-parse", f"{row['base_sha']}^{{tree}}")
    unexpected = git(project, "commit-tree", unchecked_tree, "-p", row["head"], "-m", "Merge fixture with unexpected tree")
    git(project, "push", "origin", f"{unexpected}:refs/heads/main")
    github.prs[row["pr_number"]]["merge_commit_sha"] = unexpected
    store.update_issue(1, "merged", merge_sha=unexpected)
    monkeypatch.setattr(issues, "run_process", lambda *args, **kwargs: pytest.fail("Unverified merge tree must be refused before checks"))
    runner.finish_merge(runner.row(1))
    result = store.snapshot()
    assert result["issues"][0]["state"] == "needs_decision"
    assert result["issues"][0]["reason"] == "merged_tree_differs_from_verified_tree"
    assert github.writes == [("create", 100), ("merge", 100)]
    assert github.issues[1]["state"] == "open"
