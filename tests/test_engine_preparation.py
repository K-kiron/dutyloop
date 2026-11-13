"""Clean checkout failures retain finite standalone mission recovery boundaries."""

from pathlib import Path

import pytest

from dutyloop import engine
from dutyloop.drivers import DriverResult
from dutyloop.store import Store
from dutyloop.workspace import WorkspacePreparationError
import test_selected_task as selected_tasks


selected = selected_tasks.selected


@pytest.mark.parametrize("workspace_phase", ["observations", "worktrees"])
def test_selected_preparation_failure_retries_same_candidate_without_spending_calls(
        selected, monkeypatch, workspace_phase):
    project, state, candidate = selected
    original = engine.make_worktree
    attempted = []

    def fail(project, directory, head):
        if Path(directory).parent.name == workspace_phase:
            attempted.append(str(directory))
            raise WorkspacePreparationError(directory, head, RuntimeError("Checkout could not create a file"))
        return original(project, directory, head)

    monkeypatch.setattr(engine, "make_worktree", fail)
    worker = selected_tasks.SelectedWorker()
    failed = engine.run_task(state, candidate, driver=worker)
    assert failed["status"] == "blocked" and failed["reason"] == "workspace_preparation_failed"
    assert failed["calls"] == failed["tasks"] == worker.phases == []
    assert failed["invocations"] == failed["failures"] == 0 and failed["runner_pid"] is None
    receipt = failed["preparation_failure"]
    assert receipt["operation"] == "worktree_add" and receipt["head"] == failed["accepted_head"]
    assert receipt["worktree"] == attempted[0]
    assert receipt["cleanup_confirmed"] is True and receipt["dispatch_started"] is False
    assert engine.run_task(state, candidate, driver=worker) == failed
    assert len(attempted) == 1

    with pytest.raises(ValueError, match="acknowledge"):
        engine.recover(state)
    recovered = engine.recover(state, acknowledge=True)
    assert recovered["status"] == "ready" and recovered["invocations"] == 0
    assert recovered["deadline"] == failed["deadline"] and recovered["spec"] == failed["spec"]
    assert recovered["preparation_failure"] == receipt

    monkeypatch.setattr(engine, "make_worktree", original)
    result = engine.run_task(state, candidate, driver=worker)
    assert result["status"] == "completed" and result["reason"] == "selected_task_accepted"
    assert worker.phases == ["implement", "review"] and result["invocations"] == 2
    assert result["deadline"] == failed["deadline"]
    assert len(result["tasks"]) == 1 and result["tasks"][0]["state"] == "accepted"
    assert (project / "app.py").read_text(encoding="utf-8") == "value = 0\n"


def test_autonomous_preparation_failure_blocks_once_without_failure_retry_loop(selected, monkeypatch):
    _, state, _ = selected
    attempts = []

    def fail(project, directory, head):
        attempts.append(str(directory))
        raise WorkspacePreparationError(directory, head, RuntimeError("Checkout could not create a file"))

    monkeypatch.setattr(engine, "make_worktree", fail)
    worker = selected_tasks.SelectedWorker()
    result = engine.run(state, driver=worker)
    assert result["status"] == "blocked" and result["reason"] == "workspace_preparation_failed"
    assert result["invocations"] == result["failures"] == 0 and result["runner_pid"] is None
    assert result["calls"] == result["tasks"] == worker.phases == []
    assert len(attempts) == 1
    assert engine.run(state, driver=worker) == result and len(attempts) == 1


def test_autonomous_preparation_recovery_retains_prior_accepted_work_and_budget(selected, monkeypatch):
    _, state, candidate = selected
    store = Store(state)
    spec = store.control()["spec"]
    store.set_status("ready", spec={**spec, "poll_seconds": .05})
    original = engine.make_worktree
    failed_paths = []

    def fail_next_cycle(project, directory, head):
        if Store(state).snapshot()["tasks"] and Path(directory).parent.name == "observations":
            failed_paths.append(str(directory))
            raise WorkspacePreparationError(directory, head, RuntimeError("Checkout could not create a file"))
        return original(project, directory, head)

    class AutonomousWorker(selected_tasks.SelectedWorker):
        def run(self, request, should_stop):
            if request.phase == "discover":
                self.phases.append(request.phase)
                return DriverResult(payload={"status": "work", "summary": "Inspect the selected behavior",
                                             "candidates": [candidate]})
            return super().run(request, should_stop)

    monkeypatch.setattr(engine, "make_worktree", fail_next_cycle)
    worker = AutonomousWorker()
    failed = engine.run(state, driver=worker)
    assert failed["reason"] == "workspace_preparation_failed" and len(failed_paths) == 1
    assert failed["invocations"] == 3 and failed["failures"] == 0
    assert worker.phases == ["discover", "implement", "review"]
    task, = failed["tasks"]
    assert task["state"] == "accepted" and task["commit_sha"] == failed["accepted_head"]

    recovered = engine.recover(state, acknowledge=True)
    assert recovered["status"] == "ready" and recovered["tasks"] == failed["tasks"]
    assert recovered["calls"] == failed["calls"] and recovered["invocations"] == 3
    assert recovered["deadline"] == failed["deadline"] and recovered["accepted_head"] == failed["accepted_head"]

    monkeypatch.setattr(engine, "make_worktree", original)
    next_worker = AutonomousWorker()
    result = engine.run(state, driver=next_worker)
    assert next_worker.phases == ["discover"]
    assert result["status"] == "limited" and result["reason"] == "invocation_limit"
    assert result["invocations"] == 4 and result["deadline"] == failed["deadline"]
    assert result["tasks"] == failed["tasks"] and result["accepted_head"] == failed["accepted_head"]
