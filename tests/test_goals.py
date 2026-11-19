import copy
import json
import subprocess
import sys
import threading

import pytest

from dutyloop.drivers import DriverResult
from dutyloop.engine import initialize, recover, request_stop, run
from dutyloop.store import Store


def candidate(key="correct-value", value=1):
    return {"key": key, "title": f"Correct value to {value}", "kind": "bugfix",
            "why": "The requested value is missing", "priority": 5,
            "evidence": [{"source": "app.py", "observation": "Inspect the current value"}],
            "acceptance": [f"Value equals {value}"]}


@pytest.fixture
def goal(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 0\n", encoding="utf-8")
    for args in (["init", "-b", "main"], ["config", "user.name", "Test Maintainer"],
                 ["config", "user.email", "maintainer@example.invalid"], ["add", "."],
                 ["commit", "-m", "Add fixture"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    spec = {"mode": "goal", "mission": "Deliver the requested value",
            "goal_acceptance": ["The requested value is present"], "project": str(project),
            "allowed_paths": ["app.py"],
            "verification": [[sys.executable, "-B", "-c", "import app; assert app.value > 0"]],
            "limits": {"max_invocations": 12, "max_wall_seconds": 120}, "max_task_attempts": 1}
    return project, tmp_path / "state", spec


class GoalWorker:
    def __init__(self, tasks=None, target=1):
        self.plan = {"status": "work", "summary": "Deliver only the requested behavior", "investigations": [],
                     "tasks": tasks if tasks is not None else [
                         {"candidate": candidate(), "depends_on": [], "blocked_reason": ""}]}
        self.target = target
        self.phases = []
        self.implemented = []

    def run(self, request, should_stop):
        self.phases.append(request.phase)
        if request.phase == "discover":
            return DriverResult(payload=copy.deepcopy(self.plan))
        if request.phase == "implement":
            data = json.loads(request.prompt.split("\n")[-1])
            task = data["task"]
            self.implemented.append(task["key"])
            value = int(task["title"].split()[-1])
            (request.cwd / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            return DriverResult(payload={"completed": True, "summary": "Implemented"})
        met = (request.cwd / "app.py").read_text().strip() == f"value = {self.target}"
        return DriverResult(payload={"accepted": True, "summary": "Inspected current files", "findings": [],
                                     "goal_completed": met, "criteria": [{"criterion": 1, "met": met,
                                     "evidence": [{"source": "app.py", "observation": "Checked the value"}]}]})


def test_goal_stops_on_verified_acceptance_without_more_discovery(goal):
    project, state, spec = goal
    initialize(spec, state)
    worker = GoalWorker()
    result = run(state, driver=worker)
    assert result["status"] == "completed"
    assert result["reason"] == "goal_accepted"
    assert worker.phases == ["discover", "implement", "review"]
    assert result["goal"]["completion"]["head"] == result["accepted_head"]
    assert result["goal"]["completion"]["checks"][0]["returncode"] == 0
    assert run(state, driver=worker)["invocations"] == 3
    assert worker.phases == ["discover", "implement", "review"]
    assert (project / "app.py").read_text() == "value = 0\n"


@pytest.mark.parametrize("repair_value", [1, 2, 0])
def test_repair_handoff_keeps_current_tree_checks_and_independent_review(goal, repair_value):
    project, state, spec = goal
    spec["max_task_attempts"] = 2
    initialize(spec, state)
    requests = []
    review_trees = []

    class Repair(GoalWorker):
        def run(self, request, should_stop):
            requests.append((request.phase, request.cwd, json.loads(request.prompt.split("\n")[-1])))
            result = super().run(request, should_stop)
            if request.phase == "implement" and self.phases.count("implement") == 2:
                (request.cwd / "app.py").write_text(f"value = {repair_value}\n# Checked repair\n", encoding="utf-8")
            if request.phase == "review":
                review_trees.append(subprocess.check_output(
                    ["git", "-C", str(request.cwd), "write-tree"], text=True).strip())
                if self.phases.count("review") == 1 or repair_value != 1:
                    result.payload.update(accepted=False, goal_completed=False,
                                          findings=["The required regression case is missing"])
                else:
                    result.payload["goal_completed"] = True
                    result.payload["criteria"][0]["met"] = True
            return result

    result = run(state, driver=Repair())
    expected = ["discover", "implement", "review", "implement"] + (["review"] if repair_value else [])
    assert [row[0] for row in requests] == expected
    assert requests[1][1] == requests[3][1]
    repair = requests[3][2]["previous_review"]
    assert repair["tree"] == review_trees[0]
    assert repair["scope"]["accepted_head"] == subprocess.check_output(
        ["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
    assert repair["scope"]["goal_revision"] == 1
    assert repair["review"]["accepted"] is False
    checks = [event for event in Store(state).events() if event["kind"] == "verification"]
    assert len(checks) == 2
    if repair_value == 0:
        assert checks[-1]["data"]["passed"] is False
        assert result["tasks"][0]["commit_sha"] is None
        assert not result["goal"]["completion"]
        return
    review = requests[4][2]
    assert review["previous_review"] == repair
    assert review["checked_tree"] == review_trees[1] != review_trees[0]
    assert "Checked repair" in review["repair_diff"]
    assert "-value = 0" in review["staged_diff"]
    assert review["operator_checks"][0]["returncode"] == 0
    assert review["authority"]["goal_acceptance"] == [{"criterion": 1, "requirement": spec["goal_acceptance"][0]}]
    assert not review["authority"]["past_tasks"]
    assert "candidate" not in review["authority"]["goal_plan"]["tasks"][0]
    assert review["task"]["acceptance"] == ["Value equals 1"]
    task = result["tasks"][0]
    if repair_value == 1:
        assert result["status"] == "completed"
        assert task["verified_tree"] == review_trees[-1]
    else:
        assert task["commit_sha"] is None
        assert not result["goal"]["completion"]


def test_historical_review_is_not_reused_after_operator_decision_changes(goal):
    _, state, spec = goal
    spec["max_task_attempts"] = 2
    initialize(spec, state)
    requests = []

    class NewDecision(GoalWorker):
        def run(self, request, should_stop):
            requests.append(json.loads(request.prompt.split("\n")[-1]))
            result = super().run(request, should_stop)
            if request.phase == "review" and self.phases.count("review") == 1:
                result.payload.update(accepted=False, goal_completed=False, findings=["Clarify the expected value"])
                # Simulate an authority update after this review was dispatched.
                store = Store(state)
                with store.connection() as db:
                    data = store._read(db)
                    data["goal"]["decisions_at"] = 1
                    data["goal"]["operator_decision"] = "Keep the requested value at one"
                    store._save(db, data)
            return result

    worker = NewDecision()
    result = run(state, driver=worker)
    assert result["status"] == "completed"
    assert worker.phases == ["discover", "implement", "review", "implement", "review"]
    for prompt in requests[3:]:
        assert prompt["previous_review"] is None
        assert "Clarify the expected value" not in json.dumps(prompt)
        assert prompt["authority"]["operator_decisions"]["goal"] == "Keep the requested value at one"
    assert requests[-1]["repair_diff"] is None
    assert "-value = 0" in requests[-1]["staged_diff"]
    assert "Clarify the expected value" in result["tasks"][0]["feedback"][0]


def test_scoped_context_preserves_other_tasks_investigations_and_all_authority(goal):
    from dutyloop.goals import GoalSupervisor

    _, state, spec = goal
    spec["goal_acceptance"].append("Existing behavior remains intact")
    initialize(spec, state)
    store = Store(state)
    store.begin()
    selected = {"candidate": candidate(), "depends_on": ["foundation"], "blocked_reason": "Choose policy",
                "decision": "Keep the documented policy"}
    other = {"candidate": candidate("foundation", 2), "depends_on": [], "blocked_reason": ""}
    investigation = {"key": "compatibility", "state": "finished", "result": {
        "payload": {"findings": ["Existing consumers require the documented behavior"]}}}
    plan = {"revision": 1, "tasks": [selected, other], "investigations": [investigation],
            "operator_decision": "Preserve compatibility", "decisions_at": 1}
    store.save_goal(plan, expected_revision=0)
    task_id = store.add_task(selected["candidate"], state / "unused-worktree", store.control()["accepted_head"])
    store.update_task(task_id, "running", feedback=["Current task feedback"])
    supervisor = GoalSupervisor(store, None, threading.Event())
    prompt = json.loads(supervisor.prompt("Review", task=selected["candidate"], feedback=["Current task feedback"]).split("\n")[-1])
    authority = prompt["authority"]
    assert prompt["task"] == selected["candidate"]
    assert authority["goal_plan"]["tasks"][0] == {"task_key": "correct-value", **{k: v for k, v in selected.items() if k != "candidate"}}
    assert authority["goal_plan"]["tasks"][1] == other
    assert authority["goal_plan"]["investigations"] == [investigation]
    assert [row["requirement"] for row in authority["goal_acceptance"]] == spec["goal_acceptance"]
    assert authority["operator_decisions"] == {"goal": "Preserve compatibility", "tasks": {"correct-value": selected["decision"]}}
    assert authority["operator_verification"] == spec["verification"]
    assert authority["allowed_paths"] == spec["allowed_paths"]
    assert not authority["past_tasks"]
    assert store.control()["goal"] == plan
    assert supervisor.context()["goal_plan"]["tasks"] == [selected, other]


def test_goal_persists_dependency_queue_without_replanning_each_task(goal):
    _, state, spec = goal
    tasks = [{"candidate": candidate("second-step", 2), "depends_on": ["first-step"], "blocked_reason": ""},
             {"candidate": candidate("first-step", 1), "depends_on": [], "blocked_reason": ""}]
    initialize(spec, state)
    worker = GoalWorker(tasks, target=2)
    result = run(state, driver=worker)
    assert result["status"] == "completed"
    assert worker.implemented == ["first-step", "second-step"]
    assert worker.phases.count("discover") == 1
    assert len(result["goal"]["tasks"]) == 2


def test_goal_dependency_cycle_blocks_before_implementation(goal):
    _, state, spec = goal
    tasks = [{"candidate": candidate("first-step"), "depends_on": ["second-step"], "blocked_reason": ""},
             {"candidate": candidate("second-step"), "depends_on": ["first-step"], "blocked_reason": ""}]
    initialize(spec, state)
    worker = GoalWorker(tasks)
    result = run(state, driver=worker)
    assert result["status"] == "blocked"
    assert worker.phases == ["discover", "discover"]
    assert not result["tasks"]


def test_goal_reserves_review_before_starting_a_task(goal):
    _, state, spec = goal
    spec["limits"]["max_invocations"] = 2
    initialize(spec, state)
    worker = GoalWorker()
    result = run(state, driver=worker)
    assert result["status"] == "limited"
    assert worker.phases == ["discover"]
    assert result["goal"]["tasks"]


def test_whole_plan_block_prevents_dispatch_after_control_restart(goal):
    _, state, spec = goal
    initialize(spec, state)
    worker = GoalWorker()
    worker.plan["status"] = "blocked"
    result = run(state, driver=worker)
    assert result["reason"] == "goal_requires_operator_decision"
    Store(state).set_status("ready", "inspected")
    result = run(state, driver=worker)
    assert result["reason"] == "goal_requires_operator_decision"
    assert worker.phases == ["discover"]
    assert not result["tasks"]


def test_invalid_plan_evidence_gets_one_charged_correction(goal):
    _, state, spec = goal
    initialize(spec, state)

    class Corrected(GoalWorker):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if request.phase == "discover" and self.phases.count("discover") == 1:
                result.payload["tasks"][0]["candidate"]["evidence"][0]["source"] = "app.py and tests.py"
            elif request.phase == "discover":
                assert "Evidence source must be an existing project file" in request.prompt
                result.payload["tasks"][0]["candidate"]["evidence"][0]["source"] = "app.py"
            return result

    result = run(state, driver=Corrected())
    assert result["status"] == "completed"
    assert result["invocations"] == 4
    assert result["goal"]["revision"] == 1
    assert len(result["tasks"]) == 1


def test_invalid_plan_correction_keeps_exhausted_budget(goal):
    _, state, spec = goal
    spec["limits"]["max_invocations"] = 2
    initialize(spec, state)
    worker = GoalWorker()
    worker.plan["tasks"][0]["candidate"]["evidence"][0]["source"] = "missing.py"
    result = run(state, driver=worker)
    assert result["status"] == "limited"
    assert worker.phases == ["discover"]
    assert not result.get("goal")
    assert not result["tasks"]


def test_goal_stop_during_review_cannot_mark_completed(goal):
    _, state, spec = goal
    initialize(spec, state)

    class Stopping(GoalWorker):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if request.phase == "review":
                request_stop(state)
            return result

    result = run(state, driver=Stopping())
    assert result["status"] == "stopped"
    assert result["tasks"][0]["state"] == "unknown"
    assert not result.get("goal", {}).get("completion")


@pytest.mark.parametrize("scope", ["app.py", "."])
def test_goal_investigators_are_distinct_parallel_read_only_assignments(goal, scope):
    _, state, spec = goal
    initialize(spec, state)
    rendezvous = threading.Barrier(2)
    worktrees = set()

    class Team(GoalWorker):
        def __init__(self):
            super().__init__()
            self.plan["investigations"] = [
                {"key": key, "role": role, "question": "Check current behavior", "paths": [scope]}
                for key, role in [("behavior", "behavior investigator"), ("regression", "regression investigator")]]

        def run(self, request, should_stop):
            if "recommendations" in request.schema["properties"]:
                worktrees.add(str(request.cwd))
                rendezvous.wait(timeout=10)
                return DriverResult(payload={"summary": "Inspected assigned concern",
                    "evidence": [{"source": "app.py", "observation": "Current value is zero"}],
                    "recommendations": ["Preserve the public name"]})
            return super().run(request, should_stop)

    result = run(state, driver=Team())
    assert result["status"] == "completed"
    assert len(worktrees) == 2
    assert all(item["state"] == "finished" for item in result["goal"]["investigations"])
    assert result["invocations"] == 5


@pytest.mark.parametrize("scope", ["../outside", "missing.py"])
def test_invalid_investigation_scope_blocks_before_dispatch(goal, scope):
    _, state, spec = goal
    initialize(spec, state)
    worker = GoalWorker()
    worker.plan["investigations"] = [
        {"key": "behavior", "role": "Investigator", "question": "Check behavior", "paths": [scope]}]
    result = run(state, driver=worker)
    assert result["status"] == "blocked"
    assert result["reason"] == "goal_plan_invalid"
    assert worker.phases == ["discover", "discover"]
    assert not result.get("goal")
    assert not result["tasks"]


def test_already_satisfied_goal_requires_checks_and_independent_review(goal):
    project, state, spec = goal
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "commit", "-am", "Set requested value"], check=True, capture_output=True)
    initialize(spec, state)
    worker = GoalWorker(tasks=[])
    result = run(state, driver=worker)
    assert result["status"] == "completed"
    assert worker.phases == ["discover", "review"]
    assert not result["tasks"]
    recovered = recover(state, acknowledge=True)
    assert recovered["status"] == "completed"
    assert not Store(state).begin()
    assert Store(state).reserve_call("discover") is None


def test_empty_plan_is_not_success_when_operator_checks_fail(goal):
    _, state, spec = goal
    initialize(spec, state)
    worker = GoalWorker(tasks=[])
    result = run(state, driver=worker)
    assert result["status"] == "blocked"
    assert result["reason"] == "goal_no_supported_remediation"
    assert worker.phases == ["discover", "discover"]
    assert not result["goal"]["completion"]


def test_missing_whole_goal_criteria_prevents_integration(goal):
    _, state, spec = goal
    initialize(spec, state)

    class Missing(GoalWorker):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if request.phase == "review":
                result.payload["criteria"] = []
            return result

    result = run(state, driver=Missing())
    assert result["status"] == "blocked"
    assert result["tasks"][0]["commit_sha"] is None
    assert not result["goal"]["completion"]


def test_failed_operator_gate_cannot_be_overruled_by_goal_claim(goal):
    _, state, spec = goal
    spec["verification"] = [[sys.executable, "-B", "-c", "raise SystemExit(1)"]]
    initialize(spec, state)
    worker = GoalWorker()
    result = run(state, driver=worker)
    assert result["status"] == "blocked"
    assert "review" not in worker.phases
    assert result["tasks"][0]["state"] == "rejected"


def test_independent_task_runs_while_human_decision_stays_blocked(goal):
    _, state, spec = goal
    tasks = [{"candidate": candidate("decision-needed", 2), "depends_on": [], "blocked_reason": "Choose business policy"},
             {"candidate": candidate("safe-fix"), "depends_on": [], "blocked_reason": ""}]
    initialize(spec, state)
    worker = GoalWorker(tasks)
    result = run(state, driver=worker)
    assert worker.implemented == ["safe-fix"]
    assert result["tasks"][0]["state"] == "accepted"
    assert result["reason"] == "goal_requires_operator_decision"
    assert not result["goal"]["completion"]


def test_completion_rechecks_deadline_after_ref_validation(goal, monkeypatch):
    _, state, spec = goal
    initialize(spec, state)
    store = Store(state)
    store.begin()
    store.save_goal({"revision": 1, "completion": None}, expected_revision=0)
    now = store.control()["deadline"] - 1
    monkeypatch.setattr("dutyloop.store.time.time", lambda: now)

    def slow_check():
        nonlocal now
        now += 2

    assert not store.complete_goal(store.control()["accepted_head"], {"checked": True}, slow_check)
    assert not store.control()["goal"]["completion"]


def test_investigator_cleanup_failure_keeps_recovery_reason(goal):
    _, state, spec = goal
    initialize(spec, state)

    class Unclean(GoalWorker):
        def __init__(self):
            super().__init__()
            self.plan["investigations"] = [{"key": "behavior", "role": "Investigator", "question": "Check behavior", "paths": ["app.py"]}]

        def run(self, request, should_stop):
            if "recommendations" in request.schema["properties"]:
                return DriverResult(cleanup_confirmed=False, error="Worker ownership lost")
            return super().run(request, should_stop)

    result = run(state, driver=Unclean())
    assert result["status"] == "blocked"
    assert result["reason"] == "worker_cleanup_unconfirmed"
    assert result["goal"]["investigations"][0]["state"] == "unknown"
    assert not result["tasks"]
