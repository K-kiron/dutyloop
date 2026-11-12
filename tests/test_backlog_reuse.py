"""Runtime task outcomes must not invalidate unchanged source investigations."""

import copy
import json

import pytest

from dutyloop import backlog_runner, issues
from dutyloop.drivers import DriverResult
from dutyloop.workspace import git
import test_backlog_workflow as workflow
import test_issue_workflow as issue_workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


class LocalDecisionHost(issue_workflow.Host):
    def __init__(self, *, separate_stages=False):
        super().__init__(decisions=())
        self.requests = []
        self.separate_stages = separate_stages

    def run(self, request, should_stop):
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        self.requests.append({"phase": request.phase, "data": copy.deepcopy(data)})
        result = super().run(request, should_stop)
        if data.get("planning_operation") == "roadmap" and self.separate_stages:
            pending = {item["number"] for item in result.payload["items"] if item["disposition"] == "work"}
            result.payload["stages"] = []
            for number, identifier in ((1, "foundation"), (2, "human-expansion")):
                if number in pending:
                    next_stage = workflow.stage(identifier, [number])
                    next_stage["requires_human_acceptance"] = number == 2
                    result.payload["stages"].append(next_stage)
                    next(item for item in result.payload["items"] if item["number"] == number)["stage_id"] = identifier
        elif request.phase == "discover" and not data.get("planning_operation") and data["issue"]["number"] == 1:
            return DriverResult(payload={"status": "needs_decision", "summary": "Choose the first issue's public behavior",
                                         "dependencies": [], "candidates": []})
        return result

    def planning(self, operation):
        return [request for request in self.requests if request["data"].get("planning_operation") == operation]


def test_local_question_keeps_source_investigation_and_runs_independent_same_stage_issue_within_budget(batch):
    project, state, spec, github = batch
    workflow.populate(github, 2, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    spec["limits"]["max_invocations"] = 7
    issues.initialize(spec, state, github=github)
    host = LocalDecisionHost()
    result = issues.run(state, driver=host, github=github)
    rows = {row["number"]: row for row in result["issues"]}
    assert rows[1]["state"] == "needs_decision"
    assert rows[2]["state"] == "accepted_local"
    assert len(host.planning("assess")) == 2 and len(host.planning("roadmap")) == 1
    assert result["planning"]["revision"] == 1 and result["planning"]["coverage"]["unassessed"] == []
    assert result["invocations"] == 7
    assert [request["data"]["task"]["key"] for request in host.requests if request["phase"] == "implement"] == ["issue-2"]
    assert git(project, "show", f"{result['accepted_head']}:app.py") == "value = 2"
    assert git(project, "show", "HEAD:app.py") == "value = 0" and github.writes == []


def test_cached_roadmap_still_blocks_later_human_stage_after_local_question(batch):
    _, state, spec, github = batch
    workflow.populate(github, 2, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    issues.initialize(spec, state, github=github)
    host = LocalDecisionHost(separate_stages=True)
    result = issues.run(state, driver=host, github=github)
    assert len(host.planning("assess")) == 2 and len(host.planning("roadmap")) == 1
    assert result["planning"]["revision"] == 1 and result["planning"]["active_stage_id"] == "foundation"
    current, work, gate = backlog_runner.select_work(result)
    assert current["id"] == "foundation" and work == [] and gate == "stage_has_no_runnable_work"
    assert result["planning"]["roadmap"]["stages"][1]["requires_human_acceptance"]
    assert not any(request["phase"] == "implement" for request in host.requests)
    assert result["invocations"] == 4 and github.writes == []


def test_runtime_state_changes_are_not_source_identity_but_scope_and_operator_intent_are():
    source = issue_workflow.source(1)
    snapshot = {"spec": {"github": {"repository": "example/project", "delivery": "local"},
                         "queue": {"planning_context": ""}, "mission": "Preserve the public API"},
                "issues": [{"number": 1, "state": "queued", "source": source, "reason": None}]}
    initial = backlog_runner.source_fingerprint(snapshot)
    for state in ("planning", "planned", "needs_decision", "waiting_dependency", "failed", "interrupted"):
        changed = copy.deepcopy(snapshot)
        changed["issues"][0].update(state=state, reason="A local execution observation")
        assert backlog_runner.source_fingerprint(changed) == initial
    changes = [
        lambda item: item["issues"][0]["source"].update(body="A new acceptance requirement"),
        lambda item: item["issues"][0]["source"].update(comments=[{"body": "A new comment"}]),
        lambda item: item["issues"][0]["source"].update(labels=["needs-human"]),
        lambda item: item["issues"][0]["source"].update(state="closed"),
        lambda item: item["issues"][0].update(decision="An explicit business decision", decision_id="fresh"),
        lambda item: item["spec"]["queue"].update(planning_context="A different milestone"),
        lambda item: item["spec"].update(mission="A different product responsibility"),
        lambda item: item.update(sources=[{"number": 1, "source": source, "admitted": False}]),
    ]
    for change in changes:
        changed = copy.deepcopy(snapshot)
        change(changed)
        assert backlog_runner.source_fingerprint(changed) != initial


@pytest.mark.parametrize("change", ["operator-decision", "source"])
def test_genuine_input_change_reassesses_only_affected_chunk_before_fresh_global_synthesis(batch, change):
    from dutyloop import repository

    _, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    repository.initialize(spec, state, github=github)
    first_host = LocalDecisionHost()
    first_host.decisions = (1,)
    first = issues.run(state, driver=first_host, github=github, plan_only=True)
    notes = first["planning"]["assessments"]
    assert len(notes) >= 3
    affected = next(note for note in notes if any(item["number"] == 1 for item in note["items"]))
    affected_numbers = {item["number"] for item in affected["items"]}
    unaffected = set(range(1, 7)) - affected_numbers
    retained_children = {chunk["child_state"] for chunk in first["planning"]["chunks"]
                         if chunk["operation"] == "assess" and set(chunk["issue_numbers"]) <= unaffected}
    if change == "operator-decision":
        repository.resolve(state, 1, "Preserve the current API and implement the documented requirement", github=github)
    else:
        github.issues[1]["body"] += "\nA changed source acceptance requirement"
        repository.reconcile(state, github=github)
    changed = repository.status(state)
    assert changed["planning"]["status"] == "stale"

    second_host = LocalDecisionHost()
    second_host.decisions = (1,)
    second = issues.run(state, driver=second_host, github=github, plan_only=True)
    investigated = [entry["number"] for request in second_host.planning("assess")
                    for entry in request["data"]["backlog"]]
    assert sorted(investigated) == sorted(affected_numbers)
    assert len(second_host.planning("assess")) == 1 and len(second_host.planning("roadmap")) == 1
    assert second["invocations"] == first["invocations"] + 2
    assert second["planning"]["revision"] > first["planning"]["revision"]
    assert second["planning"]["reused_assessments"] == len(notes) - 1
    assert second["planning"]["reused_issues"] == sorted(unaffected)
    assert retained_children <= set(second["planning"]["child_states"])
    assert second["planning"]["status"] == "ready" and second["planning"]["coverage"]["unassessed"] == []
    assert second["deadline"] == first["deadline"] and github.writes == []


def test_changed_execution_base_reinvestigates_all_chunks_before_fresh_roadmap(batch):
    project, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    spec["github"]["delivery"] = "pull_request"
    issues.initialize(spec, state, github=github)
    first = issues.run(state, driver=LocalDecisionHost(), github=github, plan_only=True)
    (project / "app.py").write_text("value = 42\n")
    git(project, "add", "app.py")
    git(project, "commit", "-m", "Update fixture execution base")
    git(project, "push", "origin", "main")  # Only the fixture's temporary local bare repository.
    head = git(project, "rev-parse", "HEAD")
    host = LocalDecisionHost()
    second = issues.run(state, driver=host, github=github, plan_only=True)
    investigated = [entry["number"] for request in host.planning("assess") for entry in request["data"]["backlog"]]
    assert sorted(investigated) == [1, 2, 3, 4, 5, 6]
    assert second["planning"]["base_sha"] == head != first["planning"]["base_sha"]
    assert second["planning"]["reused_assessments"] == 0 and second["planning"]["reused_issues"] == []
    assert second["invocations"] == first["invocations"] + len(host.planning("assess")) + 1
    assert all(request["data"]["repository_base_sha"] == head for request in host.requests)
    assert second["deadline"] == first["deadline"] and github.writes == []
