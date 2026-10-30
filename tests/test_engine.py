import subprocess
import sys
import threading
import time

import pytest

from dutyloop.drivers import DriverResult
from dutyloop.engine import initialize, request_stop, run, status
from dutyloop.store import Store, runner_lock


@pytest.fixture
def mission(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 0\n", encoding="utf-8")
    for args in (["init", "-b", "main"], ["config", "user.name", "Test Maintainer"],
                 ["config", "user.email", "maintainer@example.invalid"], ["add", "app.py"],
                 ["commit", "-m", "Add fixture"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    spec = {"mission": "Correct the app value while preserving all other behavior", "project": str(project),
            "allowed_paths": ["app.py"], "verification": [[sys.executable, "-B", "-c", "import app; assert app.value == 1"]],
            "limits": {"max_invocations": 3, "max_wall_seconds": 30}, "poll_seconds": .05,
            "max_task_attempts": 1}
    state = tmp_path / "state"
    initialize(spec, state)
    return project, state


class Worker:
    def __init__(self, *, value=1, completed=True, accepted=True, discovery="work", mutate_index=False):
        self.value, self.completed, self.accepted = value, completed, accepted
        self.discovery, self.mutate_index = discovery, mutate_index
        self.phases = []

    def run(self, request, should_stop):
        self.phases.append(request.phase)
        if request.phase == "discover":
            candidates = [{"key": "correct-value", "title": "Correct the app value", "kind": "bugfix",
                           "why": "The value violates the declared behavior", "priority": 5,
                           "evidence": [{"source": "app.py", "observation": "value is zero"}], "acceptance": ["value equals one"]}]
            return DriverResult(payload={"status": self.discovery, "summary": "Inspect current behavior", "candidates": candidates if self.discovery == "work" else []})
        if request.phase == "implement":
            (request.cwd / "app.py").write_text(f"value = {self.value}\n", encoding="utf-8")
            return DriverResult(payload={"summary": "Updated the app", "completed": self.completed})
        if self.mutate_index:
            subprocess.run(["git", "-C", str(request.cwd), "read-tree", "HEAD"], check=True)
        return DriverResult(payload={"accepted": self.accepted, "summary": "Reviewed", "findings": []})


def test_successful_worker_cannot_override_failed_operator_gate(mission):
    project, state = mission
    worker = Worker(value=2)
    result = run(state, driver=worker)
    assert worker.phases == ["discover", "implement", "discover"]
    assert result["tasks"][0]["state"] == "rejected"
    assert result["tasks"][0]["commit_sha"] is None
    assert (project / "app.py").read_text() == "value = 0\n"


def test_independent_rejection_prevents_integration(mission):
    _, state = mission
    result = run(state, driver=Worker(accepted=False))
    assert result["tasks"][0]["state"] == "rejected"
    assert result["tasks"][0]["commit_sha"] is None
    assert result["reported_cost_usd"] is None
    assert result["calls_without_cost"] == 3


def test_declared_incomplete_work_is_not_accepted_even_if_gate_passes(mission):
    _, state = mission
    worker = Worker(completed=False)
    result = run(state, driver=worker)
    assert result["tasks"][0]["state"] == "rejected"
    assert "review" not in worker.phases


def test_reviewer_cannot_swap_the_staged_tree(mission):
    _, state = mission
    result = run(state, driver=Worker(mutate_index=True))
    assert result["status"] == "blocked"
    assert result["tasks"][0]["commit_sha"] is None


def test_no_work_keeps_responsibility_alive_until_limit(mission):
    _, state = mission
    worker = Worker(discovery="wait")
    result = run(state, driver=worker)
    assert worker.phases == ["discover"] * 3
    assert result["status"] == "limited"
    assert result["reason"] == "invocation_limit"
    assert result["tasks"] == []


def test_real_blocker_waits_for_operator_instead_of_burning_budget(mission):
    _, state = mission
    result = run(state, driver=Worker(discovery="blocked"))
    assert result["status"] == "blocked"
    assert result["invocations"] == 1
    assert result["reason"] == "discovery_requires_operator_decision"


def test_stop_interrupts_current_call_and_never_dispatches_next(mission):
    _, state = mission
    entered = threading.Event()

    class Blocking:
        def run(self, request, should_stop):
            entered.set()
            while not should_stop():
                time.sleep(.01)
            return DriverResult(cancelled=True)

    thread = threading.Thread(target=run, args=(state,), kwargs={"driver": Blocking()})
    thread.start()
    assert entered.wait(5)
    request_stop(state)
    thread.join(5)
    assert not thread.is_alive()
    result = status(state)
    assert result["status"] == "stopped"
    assert result["invocations"] == 1
    assert run(state, driver=Worker())["invocations"] == 1


def test_second_runner_cannot_take_an_active_os_lock(mission):
    _, state = mission
    with runner_lock(state):
        with pytest.raises(RuntimeError, match="Another supervisor"):
            run(state, driver=Worker())


def test_worker_cannot_hide_out_of_scope_files_in_its_own_commit(mission):
    _, state = mission

    class CommittingWorker(Worker):
        def run(self, request, should_stop):
            if request.phase == "implement":
                (request.cwd / "outside.txt").write_text("Not authorized", encoding="utf-8")
                for args in (["add", "outside.txt"], ["commit", "-m", "Hide unauthorized file"]):
                    subprocess.run(["git", "-C", str(request.cwd), *args], check=True, capture_output=True)
            return super().run(request, should_stop)

    result = run(state, driver=CommittingWorker())
    assert result["status"] == "blocked"
    assert result["tasks"][0]["state"] != "accepted"


def test_stop_wins_before_integration_even_after_last_worker_check(mission, monkeypatch):
    import dutyloop.engine as engine

    _, state = mission
    original = engine.prepare_commit

    def stop_at_commit(*args, **kwargs):
        head = original(*args, **kwargs)
        request_stop(state)
        return head

    monkeypatch.setattr(engine, "prepare_commit", stop_at_commit)
    result = run(state, driver=Worker())
    assert result["status"] == "stopped"
    assert result["tasks"][0]["state"] != "accepted"


def test_verification_crash_requires_recovery_even_after_model_call_finished(mission):
    project, state = mission
    store = Store(state)
    task = store.add_task({"key": "old-task", "title": "Old task"}, state / "old-worktree", store.snapshot()["accepted_head"])
    store.update_task(task, "verifying")
    store.set_status("running", runner_pid=999999)
    worker = Worker()
    with pytest.raises(RuntimeError, match="Unfinished work"):
        run(state, driver=worker)
    assert worker.phases == []
    assert status(state)["invocations"] == 0


def test_runtime_git_hook_obeys_wall_deadline(mission):
    project, state = mission
    hook = project / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    hook.chmod(0o755)
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["limits"]["max_wall_seconds"] = .2
        store._save(db, data)
    started = time.monotonic()
    result = run(state, driver=Worker())
    assert time.monotonic() - started < 4
    assert result["status"] == "limited"
    assert result["reason"] == "wall_time_limit"
    assert result["invocations"] == 0
