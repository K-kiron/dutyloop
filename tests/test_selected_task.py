import copy
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dutyloop.drivers import DriverResult
from dutyloop.engine import initialize, recover, run, run_task, status
from dutyloop.store import Store, runner_lock


@pytest.fixture
def selected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 0\n", encoding="utf-8")
    for args in (["init", "-b", "main"], ["config", "user.name", "Test Maintainer"],
                 ["config", "user.email", "maintainer@example.invalid"], ["add", "app.py"],
                 ["commit", "-m", "Add fixture"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    state = tmp_path / "state"
    initialize({"mission": "Resolve the selected app issue", "project": str(project),
                "allowed_paths": ["app.py"],
                "verification": [[sys.executable, "-B", "-c", "import app; assert app.value == 1"]],
                "limits": {"max_invocations": 4, "max_wall_seconds": 120},
                "max_task_attempts": 1}, state)
    candidate = {"key": "issue-12", "title": "Correct the app value", "kind": "bugfix",
                 "why": "The declared behavior requires one", "priority": 5,
                 "evidence": [{"source": "app.py", "observation": "Value is zero"}],
                 "acceptance": ["Value equals one"]}
    return project, state, candidate


class SelectedWorker:
    def __init__(self, *, value=1, accepted=True):
        self.value = value
        self.accepted = accepted
        self.phases = []

    def run(self, request, should_stop):
        self.phases.append(request.phase)
        assert request.phase != "discover", "A selected task must never discover other work"
        if request.phase == "implement":
            (request.cwd / "app.py").write_text(f"value = {self.value}\n", encoding="utf-8")
            return DriverResult(payload={"completed": True, "summary": "Corrected the value"})
        return DriverResult(payload={"accepted": self.accepted, "summary": "Checked the change", "findings": []})


def test_selected_task_requires_real_verification_and_review(selected):
    project, state, candidate = selected
    original = status(state)["accepted_head"]
    worker = SelectedWorker()
    result = run_task(state, candidate, driver=worker)
    assert worker.phases == ["implement", "review"]
    assert result["status"] == "completed"
    assert result["reason"] == "selected_task_accepted"
    assert result["runner_pid"] is None
    assert result["invocations"] == 2
    task, = result["tasks"]
    assert task["state"] == "accepted"
    assert task["checks"][0]["returncode"] == 0
    assert task["review"]["accepted"] is True
    assert task["commit_sha"] == result["accepted_head"] != original
    assert task["candidate"]["source_receipts"][0]["sha256"]
    assert subprocess.check_output(["git", "-C", str(project), "show", f"{result['branch']}:app.py"], text=True) == "value = 1\n"
    assert (project / "app.py").read_text() == "value = 0\n"
    assert list((state / "observations").iterdir()) == []


@pytest.mark.parametrize("worker,phases", [(SelectedWorker(value=2), ["implement"]),
                                            (SelectedWorker(accepted=False), ["implement", "review"])])
def test_rejected_selected_task_finishes_without_discovery(selected, worker, phases):
    _, state, candidate = selected
    original = status(state)["accepted_head"]
    result = run_task(state, candidate, driver=worker)
    assert worker.phases == phases
    assert result["status"] == "completed"
    assert result["reason"] == "selected_task_rejected"
    assert result["tasks"][0]["state"] == "rejected"
    assert result["accepted_head"] == original
    assert Path(result["tasks"][0]["worktree"]).is_dir()


@pytest.mark.parametrize("accepted", [True, False])
def test_completed_selected_task_is_not_replayed_by_either_runner(selected, accepted):
    _, state, candidate = selected
    result = run_task(state, candidate, driver=SelectedWorker(accepted=accepted))
    worker = SelectedWorker()
    assert run_task(state, candidate, driver=worker) == result
    assert run(state, driver=worker) == result
    assert worker.phases == []


def test_selected_mission_cannot_be_reused_for_a_different_task(selected):
    _, state, candidate = selected
    run_task(state, candidate, driver=SelectedWorker())
    changed = {**candidate, "key": "issue-13"}
    with pytest.raises(ValueError, match="different|selected"):
        run_task(state, changed, driver=SelectedWorker())


def test_selected_mission_cannot_silently_change_acceptance(selected):
    _, state, candidate = selected
    run_task(state, candidate, driver=SelectedWorker())
    changed = {**candidate, "acceptance": ["Value equals two"]}
    with pytest.raises(ValueError, match="different|selected"):
        run_task(state, changed, driver=SelectedWorker())


def test_selected_task_validates_external_evidence_before_dispatch(selected):
    _, state, candidate = selected
    candidate["evidence"] = [{"source": "https://github.com/example/repo/issues/12", "observation": "Issue requires one"}]
    worker = SelectedWorker()
    result = run_task(state, candidate, driver=worker)
    assert result["status"] == "blocked"
    assert result["reason"] == "invalid_selected_task"
    assert worker.phases == []
    assert result["tasks"] == []


def test_selected_task_external_stop_cancels_active_call(selected):
    _, state, candidate = selected
    entered = threading.Event()
    stop = threading.Event()

    class Blocking:
        def run(self, request, should_stop):
            entered.set()
            while not should_stop():
                time.sleep(.01)
            return DriverResult(cancelled=True)

    thread = threading.Thread(target=run_task, args=(state, candidate),
                              kwargs={"driver": Blocking(), "should_stop": stop.is_set})
    thread.start()
    assert entered.wait(5)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    result = status(state)
    assert result["status"] == "stopped"
    assert result["stop_requested"] is True
    assert result["tasks"][0]["state"] == "unknown"
    assert result["accepted_head"] == result["tasks"][0]["base_sha"]
    worker = SelectedWorker()
    run_task(state, candidate, driver=worker)
    assert worker.phases == []


def test_selected_task_external_stop_before_start_reserves_no_calls(selected):
    _, state, candidate = selected
    worker = SelectedWorker()
    result = run_task(state, candidate, driver=worker, should_stop=lambda: True)
    assert result["status"] == "stopped"
    assert result["invocations"] == 0
    assert result["tasks"] == []
    assert worker.phases == []


def test_selected_task_requires_budget_for_implementation_and_review(selected):
    _, state, candidate = selected
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["limits"]["max_invocations"] = 1
        store._save(db, data)
    worker = SelectedWorker()
    result = run_task(state, candidate, driver=worker)
    assert result["status"] == "limited"
    assert result["reason"] == "insufficient_task_budget"
    assert result["tasks"] == []
    assert worker.phases == []


def test_selected_task_recovered_unknown_is_not_replayed(selected):
    _, state, candidate = selected
    store = Store(state)
    task = store.add_task(copy.deepcopy(candidate), state / "old-worktree", store.snapshot()["accepted_head"])
    store.update_task(task, "unknown", feedback=["Interrupted previously"])
    store.set_status("blocked", "supervisor_interrupted")
    recover(state, acknowledge=True)
    worker = SelectedWorker()
    result = run_task(state, candidate, driver=worker)
    assert result["status"] == "blocked"
    assert result["reason"] == "selected_task_unknown"
    assert len(result["tasks"]) == 1
    assert worker.phases == []


def test_selected_task_unclean_running_work_requires_recovery(selected):
    _, state, candidate = selected
    store = Store(state)
    store.add_task(candidate, state / "old-worktree", store.snapshot()["accepted_head"])
    worker = SelectedWorker()
    with pytest.raises(RuntimeError, match="Unfinished work"):
        run_task(state, candidate, driver=worker)
    assert worker.phases == []
    assert status(state)["reason"] == "unclean_interruption"


def test_selected_task_changed_integration_ref_is_blocked(selected):
    project, state, candidate = selected
    (project / "app.py").write_text("value = 2\n", encoding="utf-8")
    for args in (["add", "app.py"], ["commit", "-m", "External change"],
                 ["update-ref", f"refs/heads/{status(state)['branch']}", "HEAD"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    worker = SelectedWorker()
    with pytest.raises(RuntimeError, match="changed outside"):
        run_task(state, candidate, driver=worker)
    assert status(state)["reason"] == "integration_ref_changed"
    assert worker.phases == []


def test_selected_task_uses_existing_runner_lock(selected):
    _, state, candidate = selected
    with runner_lock(state), pytest.raises(RuntimeError, match="Another supervisor"):
        run_task(state, candidate, driver=SelectedWorker())


def test_selected_task_schema_rejects_extra_authority(selected):
    _, state, candidate = selected
    with pytest.raises(ValueError, match="unexpected properties"):
        run_task(state, {**candidate, "verification": []}, driver=SelectedWorker())
