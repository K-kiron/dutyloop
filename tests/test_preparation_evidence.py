"""Preparation recovery evidence is read-only, owned, and pre-dispatch only."""

import copy
import hashlib
import json
import threading

import pytest

from dutyloop import issues, preparation
from dutyloop.issue_store import IssueStore
from dutyloop.store import Store
from dutyloop.workspace import git
import test_issue_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


@pytest.fixture
def evidence(batch):
    project, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    initialized = issues.initialize(spec, state, github=github)
    store = IssueStore(state)
    head = initialized["accepted_head"]
    runner = issues.IssueRunner(store, None, threading.Event(), github)
    workspace, directory = runner.child(1, head, "fix")
    child = Store(directory)
    error = (f"Preparing worktree (detached HEAD {head[:7]})\n"
             "error: unable to create file tests/deep/fixture.py: Filename too long")
    child.event("selected_task_error", {"error": error})
    child.set_status("blocked", "selected_task_failed", runner_pid=None)
    candidate = {"key": "issue-1", "title": "Correct the value", "kind": "bugfix", "priority": 3,
                 "why": "The documented value needs correction",
                 "evidence": [{"source": "app.py", "observation": "The fixture value is zero"}],
                 "acceptance": ["The documented value is implemented"]}
    plan = {"status": "work", "summary": "Correct and verify the value", "dependencies": [], "candidates": [candidate]}
    store.update_issue(1, "interrupted", reason="selected_task_failed", plan=plan,
                       child_state=str(directory), plan_base_sha=head, roadmap_revision=1)
    source = initialized["issues"][0]["source"]
    scope = {key: value for key, value in source.items() if key != "updated_at"}
    source_hash = hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    store.save_planning({"revision": 1, "expected_head": head,
                         "roadmap": {"items": [{"number": 1, "source_sha256": source_hash}]}})
    row = store.snapshot()["issues"][0]
    return store, row, child, workspace, project


def test_legacy_evidence_is_owned_read_only_and_keeps_child_history(evidence):
    store, row, child, _, _ = evidence
    files = [store.path, child.path]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    receipt = preparation.inspect_child(store, row)
    assert receipt and receipt["legacy"] and receipt["plan_current"]
    assert receipt["child_state"] == row["child_state"] and receipt["issue_number"] == 1
    assert receipt["head"] == row["plan_base_sha"] and receipt["worktree"] is None
    assert receipt["cleanup_confirmed"] is True and receipt["dispatch_started"] is False
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files} == before
    assert child.control()["reason"] == "selected_task_failed"


def test_already_reconciled_row_and_historical_stop_without_stop_flag_are_supported(evidence):
    store, row, child, _, _ = evidence
    row.update(state="needs_decision", reason="interrupted_work_requires_inspection", previous_state=None)
    child.set_status("stopped", "operator_stop", runner_pid=None)
    receipt = preparation.inspect_child(store, row)
    assert receipt and receipt["legacy"] and receipt["plan_current"]
    assert child.control()["status"] == "stopped" and child.control()["stop_requested"] is False


@pytest.mark.parametrize("directory", ["observations", "worktrees"])
def test_typed_preparation_receipt_requires_owned_attempt_path(evidence, directory):
    store, row, child, _, _ = evidence
    worktree = child.directory / directory / "failed-attempt"
    child.set_status("blocked", "workspace_preparation_failed", preparation_failure={
        "operation": "worktree_add", "worktree": str(worktree), "head": row["plan_base_sha"],
        "error": "The clean checkout could not be prepared", "cleanup_confirmed": True, "dispatch_started": False,
    })
    row["reason"] = "workspace_preparation_failed"
    receipt = preparation.inspect_child(store, row)
    assert receipt and receipt["legacy"] is False and receipt["worktree"] == str(worktree)


@pytest.mark.parametrize("change", ["business-decision", "pending-scope", "no-plan", "wrong-key", "foreign-child",
                                    "wrong-base", "source", "revision", "current-base"])
def test_row_guards_and_stale_plan_bindings_do_not_authorize_old_plan(evidence, change):
    store, original, child, _, _ = evidence
    row = copy.deepcopy(original)
    if change == "business-decision":
        row.update(state="needs_decision", reason="A product choice is required")
    elif change == "pending-scope":
        row["pending_scope_sha256"] = "pending-change"
    elif change == "no-plan":
        row["plan"] = None
    elif change == "wrong-key":
        row["plan"]["candidates"][0]["key"] = "issue-2"
    elif change == "foreign-child":
        row["child_state"] = str(child.directory.parent.parent / "elsewhere" / child.directory.name)
    elif change == "wrong-base":
        row["plan_base_sha"] = "f" * len(row["plan_base_sha"])
    elif change == "source":
        row["source"]["body"] += "\nNew requirements"
    elif change == "revision":
        row["roadmap_revision"] = 2
    else:
        planning = store.control()["planning"]
        planning["expected_head"] = "f" * len(row["plan_base_sha"])
        store.save_planning(planning)
    receipt = preparation.inspect_child(store, row)
    if change in {"source", "revision", "current-base"}:
        assert receipt and receipt["plan_current"] is False
    else:
        assert receipt is None


@pytest.mark.parametrize("change", ["active-runner", "stop", "invocations", "call", "task", "wrong-branch",
                                    "dirty", "attached", "index-flags", "generic-error", "wrong-error-head",
                                    "not-checkout-error", "latest-error", "foreign-common-dir"])
def test_child_guards_reject_uncertain_or_unrelated_failures(evidence, change, tmp_path):
    store, row, child, workspace, project = evidence
    if change == "active-runner":
        child.set_status("blocked", "selected_task_failed", runner_pid=123)
    elif change == "stop":
        child.stop()
    elif change == "invocations":
        with child.connection() as db:
            data = child._read(db)
            data["invocations"] = 1
            child._save(db, data)
    elif change == "call":
        with child.connection() as db:
            db.execute("INSERT INTO calls(phase,state,data) VALUES('implement','finished','{}')")
    elif change == "task":
        child.add_task(row["plan"]["candidates"][0], workspace, row["plan_base_sha"])
    elif change == "wrong-branch":
        child.set_status("blocked", "selected_task_failed", branch="work/unrelated")
    elif change == "dirty":
        (workspace / "app.py").write_text("value = 99\n")
    elif change == "attached":
        git(workspace, "checkout", child.control()["branch"])
    elif change == "index-flags":
        git(workspace, "update-index", "--assume-unchanged", "app.py")
    elif change in {"generic-error", "wrong-error-head", "not-checkout-error", "latest-error"}:
        error = {"generic-error": "ValueError: Filename too long",
                 "wrong-error-head": "Preparing worktree (detached HEAD fffffff)\nerror: unable to create file x: Filename too long",
                 "not-checkout-error": f"Preparing worktree (detached HEAD {row['plan_base_sha'][:7]})\nValueError: Filename too long",
                 "latest-error": "A later unknown failure"}[change]
        child.event("selected_task_error", {"error": error})
    else:
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        git(foreign, "init")
        control = store.control()
        control["spec"]["project"] = str(foreign)
        with store.connection() as db:
            store._save(db, control)
    assert preparation.inspect_child(store, row) is None
    assert (project / "app.py").read_text() == "value = 0\n"


@pytest.mark.parametrize("field,value", [("cleanup_confirmed", False), ("dispatch_started", True),
                                         ("head", "f" * 40), ("operation", "other"), ("worktree", "outside")])
def test_typed_receipt_rejects_missing_cleanup_or_scope_proof(evidence, field, value):
    store, row, child, _, _ = evidence
    receipt = {"operation": "worktree_add", "worktree": str(child.directory / "observations" / "attempt"),
               "head": row["plan_base_sha"], "error": "Checkout failed", "cleanup_confirmed": True,
               "dispatch_started": False}
    receipt[field] = value
    child.set_status("blocked", "workspace_preparation_failed", preparation_failure=receipt)
    assert preparation.inspect_child(store, row) is None


def test_missing_child_database_is_not_created(evidence):
    store, row, child, _, _ = evidence
    child.path.unlink()
    assert preparation.inspect_child(store, row) is None
    assert not child.path.exists()
