"""Operator decisions resume finite goals without changing their obligations."""

import json
import threading
import time

import pytest
import test_goals as workflow

from dutyloop import cli, goal_decisions, mcp_server
from dutyloop.engine import initialize, run
from dutyloop.store import Store, runner_lock
from dutyloop.workspace import git

goal = workflow.goal
candidate = workflow.candidate
GoalWorker = workflow.GoalWorker


@pytest.fixture
def blocked_goal(goal):
    project, state, spec = goal
    initialize(spec, state)
    store = Store(state)
    plan = {"revision": 1, "base_sha": store.control()["accepted_head"], "summary": "Need operator choice",
            "tasks": [{"candidate": candidate("first-step", 1), "depends_on": [], "blocked_reason": ""},
                      {"candidate": candidate("second-step", 2), "depends_on": ["first-step"],
                       "blocked_reason": "Choose the intended final value"}],
            "investigations": [], "history": [], "completion": None}
    store.save_goal(plan, expected_revision=0)
    with store.connection() as db:
        data = store._read(db)
        data.update(status="blocked", reason="goal_requires_operator_decision", invocations=3,
                    opened_at=time.time() - 10, deadline=time.time() + 100)
        store._save(db, data)
        accepted = {"candidate": plan["tasks"][0]["candidate"], "commit_sha": data["accepted_head"]}
        db.execute("INSERT INTO tasks(task_key,state,data) VALUES(?,?,?)", ("first-step", "accepted", json.dumps(accepted)))
    return project, state, store


def test_operator_decision_preserves_plan_accepted_history_and_budget(blocked_goal):
    _, state, store = blocked_goal
    before = store.snapshot()
    result = goal_decisions.resolve(state, "second-step", "Use the intended value 2")
    assert result["status"] == "ready"
    assert result["reason"] == "goal_operator_decision_recorded"
    assert result["goal"]["revision"] == 1
    assert result["goal"]["tasks"][1]["decision"] == "Use the intended value 2"
    assert result["goal"]["tasks"][1]["blocked_reason"] == "Choose the intended final value"
    assert result["goal"]["decisions_at"] >= before["opened_at"]
    for field in ("tasks", "invocations", "deadline", "opened_at", "accepted_head", "branch", "calls", "spec"):
        assert result[field] == before[field]
    event = store.events()[-1]
    assert event["kind"] == "goal_decision_recorded"
    assert event["data"]["key"] == "second-step"


@pytest.mark.parametrize("status,reason", [("stopped", "operator_stop"), ("limited", "invocation_limit"),
    ("completed", "goal_accepted"), ("blocked", "review_modified_workspace"), ("ready", None)])
def test_operator_decision_cannot_reset_other_control_states(blocked_goal, status, reason):
    _, state, store = blocked_goal
    store.set_status(status, reason)
    before = store.snapshot()
    with pytest.raises((ValueError, RuntimeError)):
        goal_decisions.resolve(state, "second-step", "Use value 2")
    assert store.snapshot() == before


@pytest.mark.parametrize("table,state_name", [("calls", "running"), ("calls", "reserved"), ("calls", "unknown"),
                                           ("tasks", "running"), ("tasks", "verifying"), ("tasks", "unknown"), ("tasks", "rejected")])
def test_operator_decision_cannot_hide_unsettled_work(blocked_goal, table, state_name):
    _, state, store = blocked_goal
    with store.connection() as db:
        if table == "calls":
            db.execute("INSERT INTO calls(phase,state,data) VALUES('discover',?, '{}')", (state_name,))
        else:
            db.execute("UPDATE tasks SET state=?", (state_name,))
    before = store.snapshot()
    with pytest.raises(RuntimeError, match="work"):
        goal_decisions.resolve(state, "second-step", "Use value 2")
    assert store.snapshot() == before


def test_operator_decision_checks_runner_and_integration_ref(blocked_goal):
    project, state, store = blocked_goal
    store.set_status("blocked", "goal_requires_operator_decision", runner_pid=123)
    with pytest.raises(RuntimeError, match="runner"):
        goal_decisions.resolve(state, "second-step", "Use value 2")
    store.set_status("blocked", "goal_requires_operator_decision", runner_pid=None)
    git(project, "update-ref", f"refs/heads/{store.control()['branch']}", "0" * 40)
    with pytest.raises(RuntimeError, match="Integration"):
        goal_decisions.resolve(state, "second-step", "Use value 2")
    with runner_lock(state):
        with pytest.raises(RuntimeError, match="supervisor"):
            goal_decisions.resolve(state, "second-step", "Use value 2")


@pytest.mark.parametrize("exhausted", ["invocations", "deadline"])
def test_operator_decision_cannot_extend_exhausted_limits(blocked_goal, exhausted):
    _, state, store = blocked_goal
    with store.connection() as db:
        data = store._read(db)
        data[exhausted] = data["spec"]["limits"]["max_invocations"] if exhausted == "invocations" else time.time() - 1
        store._save(db, data)
    before = store.snapshot()
    result = goal_decisions.resolve(state, "second-step", "Use value 2")
    assert result["status"] == "limited"
    assert result["invocations"] == before["invocations"]
    assert result["deadline"] == before["deadline"]


def test_operator_decision_validates_key_text_and_mode(blocked_goal):
    _, state, store = blocked_goal
    for key, decision in (("unknown-key", "Proceed"), ("first-step", "Proceed"), ("second-step", " "),
                          ("second-step", "bad\x00text"), ("second-step", True)):
        with pytest.raises(ValueError):
            goal_decisions.resolve(state, key, decision)
    with store.connection() as db:
        data = store._read(db)
        data["spec"]["mode"] = "standing"
        store._save(db, data)
    with pytest.raises(ValueError, match="goal mode"):
        goal_decisions.resolve(state, "second-step", "Use value 2")


def test_cli_and_mcp_resolve_preserve_operator_text(blocked_goal):
    project, state, _ = blocked_goal
    args = cli._parser().parse_args(["resolve", "--state", str(state), "--key", "second-step",
                                    "--decision", "Use value 2"])
    assert cli._dispatch(args)["goal"]["tasks"][1]["decision"] == "Use value 2"
    controller = mcp_server.ControlPlane(state.parent, [project])
    with pytest.raises(ValueError, match="operator decision"):
        controller.mission_resolve(state.name, "second-step", "Use value 3")


def test_whole_goal_decision_requests_bounded_replan(blocked_goal):
    _, state, store = blocked_goal
    with store.connection() as db:
        data = store._read(db)
        data["goal"]["plan_status"] = "blocked"
        data["goal"]["tasks"][1]["candidate"]["key"] = "goal"
        store._save(db, data)
    before = store.snapshot()
    result = goal_decisions.resolve(state, "@goal", "Keep the existing behavior")
    assert result["goal"]["operator_decision"] == "Keep the existing behavior"
    assert result["goal"]["replan_requested"] is True
    assert result["goal"]["tasks"] == before["goal"]["tasks"]
    assert result["goal"]["revision"] == before["goal"]["revision"]
    assert result["spec"]["max_plan_revisions"] == before["spec"]["max_plan_revisions"]


def test_plain_goal_is_an_ordinary_task_key(blocked_goal):
    _, state, store = blocked_goal
    with store.connection() as db:
        data = store._read(db)
        data["goal"]["tasks"][1]["candidate"]["key"] = "goal"
        data["goal"]["plan_status"] = "work"
        store._save(db, data)
    result = goal_decisions.resolve(state, "goal", "Use value 2")
    assert result["goal"]["tasks"][1]["decision"] == "Use value 2"
    assert "operator_decision" not in result["goal"]
    assert "replan_requested" not in result["goal"]


def test_concurrent_stop_cannot_be_overwritten_by_operator_decision(blocked_goal, monkeypatch):
    _, state, store = blocked_goal
    checked = threading.Event()
    stopping = threading.Event()
    original = goal_decisions.git

    def checking(*args):
        checked.set()
        assert stopping.wait(5)
        return original(*args)

    def stop():
        assert checked.wait(5)
        stopping.set()
        store.stop()

    monkeypatch.setattr(goal_decisions, "git", checking)
    thread = threading.Thread(target=stop)
    thread.start()
    try:
        goal_decisions.resolve(state, "second-step", "Use value 2")
    finally:
        thread.join(10)
    assert not thread.is_alive()
    result = store.snapshot()
    assert result["status"] == "stopped"
    assert result["stop_requested"] is True


def test_recorded_node_decision_resumes_same_plan_without_replaying_accepted_work(goal):
    _, state, spec = goal
    tasks = [{"candidate": candidate("first-step", 1), "depends_on": [], "blocked_reason": ""},
             {"candidate": candidate("second-step", 2), "depends_on": ["first-step"], "blocked_reason": "Choose value"}]
    worker = GoalWorker(tasks, target=2)
    initialize(spec, state)
    blocked = run(state, driver=worker)
    assert blocked["reason"] == "goal_requires_operator_decision"
    assert worker.implemented == ["first-step"]
    goal_decisions.resolve(state, "second-step", "Use value 2")
    completed = run(state, driver=worker)
    assert completed["status"] == "completed"
    assert worker.implemented == ["first-step", "second-step"]
    assert worker.phases.count("discover") == 1
    assert completed["goal"]["revision"] == blocked["goal"]["revision"]
    assert completed["deadline"] == blocked["deadline"]
    assert completed["invocations"] == blocked["invocations"] + 2


def test_configurator_and_human_status_expose_goal_controls(goal, tmp_path):
    project, state, _ = goal
    output = tmp_path / "finite-goal.json"
    args = cli._parser().parse_args(["configure", "--project", str(project), "--output", str(output),
        "--mission", "Set value", "--allow-path", "app.py", "--verify-json", '["python","-m","unittest"]',
        "--mode", "goal", "--acceptance", "The value is correct", "--acceptance", "Regression passes",
        "--max-workers", "2", "--max-plan-revisions", "3"])
    assert cli._dispatch(args)["started"] is False
    spec = json.loads(output.read_text(encoding="utf-8"))
    assert spec["mode"] == "goal"
    assert spec["goal_acceptance"] == ["The value is correct", "Regression passes"]
    assert spec["team"]["max_workers"] == 2
    assert spec["max_plan_revisions"] == 3
    assert not state.exists()


def test_human_status_reports_pending_blocked_and_completed_goal(blocked_goal):
    from dutyloop.onboarding import human_status
    _, _, store = blocked_goal
    snapshot = store.snapshot()
    text = human_status(snapshot)
    assert "Mode: finite goal" in text
    assert "revision 1; 1 pending; 1 need decisions" in text
    assert "second-step" in text
    assert "Goal completion: pending" in text
    snapshot["goal"]["completion"] = {"head": "accepted-sha"}
    assert "accepted at accepted-sha" in human_status(snapshot)


def test_whole_plan_blocker_rejects_node_decision_without_changing_state(blocked_goal):
    _, state, store = blocked_goal
    with store.connection() as db:
        data = store._read(db)
        data["goal"]["plan_status"] = "blocked"
        data["goal"]["tasks"][1]["candidate"]["key"] = "goal"
        store._save(db, data)
    before = store.snapshot()
    before_events = store.events()
    with pytest.raises(ValueError, match="@goal"):
        goal_decisions.resolve(state, "goal", "Use value 2")
    assert store.snapshot() == before
    assert store.events() == before_events
