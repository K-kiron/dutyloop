import os
import subprocess
import shlex
import sys
import threading
import time
from pathlib import Path

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
    assert Path(result["tasks"][0]["worktree"]).is_dir()
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
    marker = project / ".git" / "deadline-hook-entered"
    hook.write_text(f"#!/bin/sh\nprintf entered > {shlex.quote(marker.as_posix())}\nsleep 8\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["limits"]["max_wall_seconds"] = 2
        store._save(db, data)
    started = time.monotonic()
    result = run(state, driver=Worker())
    assert time.monotonic() - started < 6
    assert marker.is_file(), "The hook must actually start for the deadline check to be meaningful"
    assert result["status"] == "limited"
    assert result["reason"] == "wall_time_limit"
    assert result["invocations"] == 0


def test_task_does_not_start_without_budget_for_independent_review(mission):
    _, state = mission
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["limits"]["max_invocations"] = 2
        store._save(db, data)
    worker = Worker()
    result = run(state, driver=worker)
    assert worker.phases == ["discover"]
    assert result["tasks"] == []
    assert result["invocations"] == 1
    assert result["reason"] == "insufficient_task_budget"
    assert not (state / "worktrees").exists()


def test_parallel_discovery_leaves_room_for_implementation_and_review(mission):
    _, state = mission
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["team"]["discovery_workers"] = 3
        store._save(db, data)
    worker = Worker()
    result = run(state, driver=worker)
    assert worker.phases == ["discover", "implement", "review"]
    assert result["tasks"][0]["state"] == "accepted"
    task = result["tasks"][0]
    committed_tree = subprocess.check_output(
        ["git", "-C", str(task["worktree"]), "rev-parse", f"{task['commit_sha']}^{{tree}}"], text=True,
    ).strip()
    assert task["verified_tree"] == committed_tree
    checks = [event["data"] for event in store.events() if event["kind"] == "verification"]
    assert checks[-1]["passed"] is True
    assert checks[-1]["tree"] == committed_tree
    assert list((state / "observations").iterdir()) == []
    assert list((state / "git-operations").iterdir()) == []


def test_wait_cycles_release_unchanged_observations_and_git_logs(mission):
    _, state = mission
    run(state, driver=Worker(discovery="wait"))
    assert list((state / "observations").iterdir()) == []
    assert list((state / "git-operations").iterdir()) == []


def test_modified_observation_and_rejected_task_are_retained(mission):
    _, state = mission

    class DirtyObservation(Worker):
        def run(self, request, should_stop):
            if request.phase == "discover":
                (request.cwd / "app.py").write_text("value = 99\n", encoding="utf-8")
            return super().run(request, should_stop)

    result = run(state, driver=DirtyObservation())
    assert result["status"] == "blocked"
    assert len(list((state / "observations").iterdir())) == 1
    assert next((state / "observations").iterdir()).joinpath("app.py").read_text() == "value = 99\n"


def test_terminal_host_error_does_not_retry_or_consume_remaining_budget(mission):
    _, state = mission

    class AuthenticationFailure:
        def run(self, request, should_stop):
            result = DriverResult(returncode=1, error="Host requires login")
            result.error_kind = "auth"
            return result

    result = run(state, driver=AuthenticationFailure())
    assert result["status"] == "blocked"
    assert result["reason"] == "driver_auth"
    assert result["invocations"] == 1
    assert result["calls"][0]["error_kind"] == "auth"


def test_unexpected_driver_exception_is_unknown_charged_and_requires_cleanup(mission):
    _, state = mission

    class BrokenDriver:
        def run(self, request, should_stop):
            raise OSError("Worker transport disappeared")

    result = run(state, driver=BrokenDriver())
    assert result["status"] == "blocked"
    assert result["reason"] == "worker_cleanup_unconfirmed"
    assert result["invocations"] == 1
    assert result["calls"][0]["state"] == "unknown"
    assert result["calls"][0]["cleanup_confirmed"] is False


def test_commit_hook_cannot_add_unreviewed_history_with_same_final_tree(mission):
    project, state = mission
    original = status(state)["accepted_head"]
    hook = project / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\ngit -c core.hooksPath=/dev/null commit --allow-empty -m 'Unreviewed extra commit'\n", encoding="utf-8")
    hook.chmod(0o755)
    result = run(state, driver=Worker())
    assert result["accepted_head"] == original
    assert result["tasks"][0]["state"] == "rejected"
    assert "ancestry" in result["tasks"][0]["feedback"][-1]


def test_unconfirmed_git_cleanup_blocks_without_dispatching_worker(mission, monkeypatch):
    import dutyloop.workspace as workspace
    from dutyloop.process import ProcessResult

    _, state = mission
    monkeypatch.setattr(workspace, "run_process", lambda *args, **kwargs: ProcessResult(cleanup_confirmed=False))
    worker = Worker()
    result = run(state, driver=worker)
    assert result["status"] == "blocked"
    assert result["reason"] == "git_cleanup_unconfirmed"
    assert worker.phases == []
    assert result["invocations"] == 0


@pytest.mark.parametrize("mutation", [
    "from pathlib import Path; Path('app.py').write_text('value = 2\\n')",
    "import subprocess; subprocess.run(['git', 'read-tree', 'HEAD'], check=True)",
    pytest.param(
        "from pathlib import Path; p = Path('app.py'); p.chmod(p.stat().st_mode ^ 0o111)",
        marks=pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX executable mode"),
    ),
])
def test_successful_verification_cannot_change_the_verified_snapshot(mission, mutation):
    project, state = mission
    original = status(state)["accepted_head"]
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["verification"].append([sys.executable, "-B", "-c", mutation])
        store._save(db, data)
    worker = Worker()
    result = run(state, driver=worker)
    assert result["status"] == "blocked"
    assert result["reason"] == "verification_modified_workspace"
    assert worker.phases == ["discover", "implement"]
    assert result["accepted_head"] == original
    assert result["tasks"][0]["commit_sha"] is None
    assert Path(result["tasks"][0]["worktree"]).is_dir()
    assert (project / "app.py").read_text() == "value = 0\n"


def test_commit_refuses_index_changes_after_review(mission, monkeypatch):
    import dutyloop.engine as engine

    _, state = mission
    original_head = status(state)["accepted_head"]
    original_commit = engine.prepare_commit

    def change_before_commit(workspace, *args, **kwargs):
        (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(workspace), "add", "app.py"], check=True, capture_output=True)
        return original_commit(workspace, *args, **kwargs)

    monkeypatch.setattr(engine, "prepare_commit", change_before_commit)
    result = run(state, driver=Worker())
    assert result["accepted_head"] == original_head
    assert result["tasks"][0]["state"] == "rejected"
    assert result["tasks"][0]["commit_sha"] is None
    assert "reviewed tree" in result["tasks"][0]["feedback"][-1]


def test_commit_refuses_base_changes_after_review(mission, monkeypatch):
    import dutyloop.engine as engine

    _, state = mission
    original_head = status(state)["accepted_head"]
    original_commit = engine.prepare_commit

    def change_before_commit(workspace, *args, **kwargs):
        argv = ["git", "-C", str(workspace)]
        base_tree = subprocess.check_output([*argv, "rev-parse", "HEAD^{tree}"], text=True).strip()
        extra = subprocess.check_output(
            [*argv, "commit-tree", base_tree, "-p", original_head, "-m", "Extra unchecked history"], text=True,
        ).strip()
        subprocess.run([*argv, "update-ref", "--no-deref", "HEAD", extra], check=True)
        return original_commit(workspace, *args, **kwargs)

    monkeypatch.setattr(engine, "prepare_commit", change_before_commit)
    result = run(state, driver=Worker())
    assert result["accepted_head"] == original_head
    assert result["tasks"][0]["state"] == "rejected"
    assert result["tasks"][0]["commit_sha"] is None
    assert "reviewed base" in result["tasks"][0]["feedback"][-1]


def test_verification_reads_the_materialized_staged_snapshot(mission, tmp_path):
    project, state = mission
    original_head = status(state)["accepted_head"]
    marker = tmp_path / "filter-ran"
    transform = tmp_path / "clean.py"
    transform.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "sys.stdout.write(sys.stdin.read().replace('value = 1', 'value = 2'))\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(transform.as_posix())}"
    subprocess.run(["git", "-C", str(project), "config", "filter.swap.clean", command], check=True)
    (project / ".git" / "info" / "attributes").write_text("app.py filter=swap\n", encoding="utf-8")
    worker = Worker()
    result = run(state, driver=worker)
    assert marker.is_file(), "The clean filter must run for this regression to be meaningful"
    assert result["accepted_head"] == original_head
    assert result["tasks"][0]["state"] == "rejected"
    assert "Operator verification failed" in result["tasks"][0]["feedback"][-1]
    assert "review" not in worker.phases


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_worker_cannot_hide_a_verification_dependency_with_index_flags(mission, flag):
    project, state = mission
    (project / "outside.py").write_text("value = 0\n", encoding="utf-8")
    for args in (["add", "outside.py"], ["commit", "-m", "Add dependency"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    # Include the committed dependency in this fixture's mission base.
    head = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
    store = Store(state)
    with store.connection() as db:
        data = store._read(db)
        data["accepted_head"] = head
        store._save(db, data)
    subprocess.run(["git", "-C", str(project), "update-ref", f"refs/heads/{status(state)['branch']}", head], check=True)

    class HiddenDependency(Worker):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if request.phase == "implement":
                subprocess.run(["git", "-C", str(request.cwd), "update-index", flag, "outside.py"], check=True)
                (request.cwd / "outside.py").write_text("value = 1\n", encoding="utf-8")
                (request.cwd / "app.py").write_text("from outside import value\n", encoding="utf-8")
            return result

    worker = HiddenDependency()
    result = run(state, driver=worker)
    assert result["accepted_head"] == head
    assert result["tasks"][0]["state"] == "rejected"
    assert "index flags" in result["tasks"][0]["feedback"][-1]
    assert "review" not in worker.phases
    retained = Path(result["tasks"][0]["worktree"])
    assert (retained / "outside.py").read_text() == "value = 1\n"
