"""Whole-backlog planning before scoped execution, using real local Git only."""

import copy
import json

import pytest

from dutyloop import issues, repository
from dutyloop.drivers import DriverResult
from dutyloop.issue_store import IssueStore
from dutyloop.workspace import git
import test_issue_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


def stage(identifier, numbers):
    return {"id": identifier, "title": identifier, "goal": "Establish the documented capability",
            "rationale": "Foundation must precede expansion", "entry_conditions": ["Preserve the public API"],
            "exit_criteria": ["All assigned requirements pass operator verification and review"],
            "requires_human_acceptance": False, "issue_numbers": numbers}


class PortfolioHost(workflow.Host):
    def __init__(self, foundation=93):
        super().__init__(decisions=())
        self.foundation = foundation
        self.requests = []

    def run(self, request, should_stop):
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        self.requests.append({"phase": request.phase, "data": copy.deepcopy(data),
                              "schema": request.schema, "head": git(request.cwd, "rev-parse", "HEAD"),
                              "code": (request.cwd / "app.py").read_text()})
        result = super().run(request, should_stop)
        if data.get("planning_operation") == "assess":
            for item, entry in zip(result.payload["items"], data["backlog"]):
                item["assessment"] += " " + entry["source"]["body"][-80:]
                item["assessment"] += " " + " ".join(comment["body"][-80:]
                                                       for comment in entry["source"]["comments"])
        if data.get("planning_operation") == "roadmap":
            pending = {item["number"] for item in result.payload["items"] if item["disposition"] == "work"}
            foundation = [self.foundation] if self.foundation in pending else []
            expansion = sorted(pending - set(foundation))
            result.payload["stages"] = ([stage("foundation", foundation)] if foundation else [])
            if expansion:
                result.payload["stages"].append(stage("expansion", expansion))
            for item in result.payload["items"]:
                if item["disposition"] == "work":
                    item["stage_id"] = "foundation" if item["number"] == self.foundation else "expansion"
        elif request.phase == "discover" and result.payload.get("candidates"):
            # A future issue may have higher task priority; stage membership wins.
            result.payload["candidates"][0]["priority"] = 1 if data["issue"]["number"] == self.foundation else 5
        return result

    def planning(self, operation=None):
        return [request for request in self.requests
                if request["data"].get("planning_operation")
                and (operation is None or request["data"]["planning_operation"] == operation)]

    def detailed(self):
        return [request for request in self.requests
                if request["phase"] == "discover" and not request["data"].get("planning_operation")]


def populate(github, count, *, body_size=0):
    github.issues = {number: workflow.source(number) for number in range(1, count + 1)}
    for number, source in github.issues.items():
        source["body"] += "\n" + "x" * body_size + f"\nREQUIRED-BODY-TAIL-{number}"
        source["comments"] = [{"body": f"REQUIRED-COMMENT-TAIL-{number}"}]


def test_complete_93_issue_roadmap_executes_late_foundation_within_four_calls(batch):
    project, state, spec, github = batch
    populate(github, 93)
    spec["queue"] = {"batch_size": 1}
    issues.initialize(spec, state, github=github)
    host = PortfolioHost()
    result = issues.run(state, driver=host, github=github)
    implemented = [request["data"]["task"]["key"] for request in host.requests
                   if request["phase"] == "implement"]
    assert implemented == ["issue-93"]
    assert result["invocations"] == 4
    assert [request["data"]["issue"]["number"] for request in host.detailed()] == [93]
    assert len(host.planning()) == 1
    captured = host.planning()[0]["data"]["backlog"]
    assert len(captured) == 93
    assert all(entry["source"]["body"] == github.issues[entry["number"]]["body"]
               and entry["source"]["comments"] == github.issues[entry["number"]]["comments"] for entry in captured)
    assert "REQUIRED-COMMENT-TAIL-93" in json.dumps(captured)
    assert result["planning"]["coverage"] == {"total": 93, "assessed": list(range(1, 94)), "unassessed": []}
    assert git(project, "show", f"{result['accepted_head']}:app.py") == "value = 93"
    assert git(project, "show", "HEAD:app.py") == "value = 0"
    assert all(row["plan"] is None for row in result["issues"] if row["number"] != 93)
    assert github.writes == []


def test_plan_only_covers_93_without_detailed_plans_and_unchanged_resume_is_free(batch):
    from dutyloop import backlog

    _, state, spec, github = batch
    populate(github, 93)
    issues.initialize(spec, state, github=github)
    host = PortfolioHost()
    planned = issues.run(state, driver=host, github=github, plan_only=True)
    assert planned["invocations"] == 1
    assert all(row["plan"] is None for row in planned["issues"])
    assert host.detailed() == [] and host.calls == ["discover"]
    current, work, gate = backlog.next_work(planned["planning"]["roadmap"], planned["issues"], "local")
    assert current["id"] == "foundation" and [item["number"] for item in work] == [93] and gate is None
    repeated = issues.run(state, driver=host, github=github, plan_only=True)
    assert repeated["invocations"] == 1 and len(host.requests) == 1
    assert repeated["planning"]["revision"] == planned["planning"]["revision"]
    assert repeated["deadline"] == planned["deadline"] and github.writes == []


def test_complete_source_chunks_retain_acceptance_tails_through_synthesis(batch):
    from dutyloop import backlog

    _, state, spec, github = batch
    populate(github, 6, body_size=3500)
    spec["queue"] = {"planning_max_bytes": 20000}
    issues.initialize(spec, state, github=github)
    host = PortfolioHost(foundation=6)
    result = issues.run(state, driver=host, github=github, plan_only=True)
    chunks = host.planning("assess")
    assert len(chunks) >= 2 and all(request["schema"] == backlog.ASSESSMENT_SCHEMA for request in chunks)
    captured = [entry for request in chunks for entry in request["data"]["backlog"]]
    assert sorted(entry["number"] for entry in captured) == list(range(1, 7))
    assert all(entry["source"]["body"] == github.issues[entry["number"]]["body"] for entry in captured)
    synthesis = host.planning("roadmap")
    assert len(synthesis) == 1
    notes = json.dumps(synthesis[0]["data"]["assessments"])
    assert all(f"REQUIRED-BODY-TAIL-{number}" in notes and f"REQUIRED-COMMENT-TAIL-{number}" in notes
               for number in range(1, 7))
    assert result["planning"]["status"] == "ready" and result["planning"]["coverage"]["unassessed"] == []
    assert result["invocations"] == len(chunks) + 1
    assert host.detailed() == [] and github.writes == []


def test_first_chunk_budget_exhaustion_retains_explicit_incomplete_coverage(batch):
    _, state, spec, github = batch
    populate(github, 6, body_size=3500)
    spec["queue"] = {"planning_max_bytes": 20000}
    spec["limits"]["max_invocations"] = 1
    issues.initialize(spec, state, github=github)
    host = PortfolioHost(foundation=6)
    result = issues.run(state, driver=host, github=github)
    planning = result["planning"]
    assert result["status"] == "limited" and result["invocations"] == 1
    assert planning["status"] == "incomplete" and planning["roadmap"] is None
    assert 0 < len(planning["coverage"]["assessed"]) < 6
    assert set(planning["coverage"]["assessed"]) | set(planning["coverage"]["unassessed"]) == set(range(1, 7))
    assert set(planning["coverage"]["assessed"]).isdisjoint(planning["coverage"]["unassessed"])
    assert len(host.planning("assess")) == 1 and host.planning("roadmap") == [] and host.detailed() == []
    assert all(row["plan"] is None for row in result["issues"])
    assert all(call["state"] != "unknown" for call in result["calls"])
    repeated = issues.run(state, driver=host, github=github)
    assert repeated["invocations"] == 1 and repeated["deadline"] == result["deadline"]
    assert len(host.requests) == 1 and github.writes == []


def test_oversized_single_source_requests_decision_without_truncation_or_dispatch(batch):
    _, state, spec, github = batch
    populate(github, 1, body_size=30000)
    spec["queue"] = {"planning_max_bytes": 20000}
    issues.initialize(spec, state, github=github)
    host = PortfolioHost(foundation=1)
    result = issues.run(state, driver=host, github=github)
    assert result["planning"]["status"] == "needs_decision"
    assert result["planning"]["reason"] == "issue_exceeds_planning_input_limit"
    assert result["planning"]["coverage"]["unassessed"] == [1] and result["planning"]["roadmap"] is None
    assert result["issues"][0]["source"]["body"] == github.issues[1]["body"]
    assert result["invocations"] == 0 and host.requests == [] and github.writes == []


def test_repository_source_decision_and_admission_changes_invalidate_cached_roadmap(batch):
    _, state, spec, github = batch
    populate(github, 1)
    repository.initialize(spec, state, github=github)
    host = PortfolioHost(foundation=1)
    initial = issues.run(state, driver=host, github=github, plan_only=True)
    github.issues[1]["body"] += "\nNew acceptance requirement"
    repository.reconcile(state, github=github)
    changed = issues.run(state, driver=host, github=github, plan_only=True)
    assert changed["planning"]["revision"] > initial["planning"]["revision"]
    assert host.planning()[-1]["data"]["backlog"][0]["source"]["body"].endswith("New acceptance requirement")
    assert changed["issues"][0]["state"] == "needs_decision"
    repository.resolve(state, 1, "Accept the new requirement while preserving the public API", github=github)
    decided = issues.run(state, driver=host, github=github, plan_only=True)
    assert decided["planning"]["revision"] > changed["planning"]["revision"]
    assert host.planning()[-1]["data"]["backlog"][0]["decision"].startswith("Accept the new requirement")
    github.issues[4] = workflow.source(4)
    repository.reconcile(state, github=github)
    observed = issues.run(state, driver=host, github=github, plan_only=True)
    new_source = next(entry for entry in host.planning()[-1]["data"]["backlog"] if entry["number"] == 4)
    assert new_source["admitted"] is False
    assert next(item for item in observed["planning"]["roadmap"]["items"] if item["number"] == 4)["disposition"] == "defer"
    repository.resolve(state, 4, "Admit the documented requirement for planning", github=github)
    admitted = issues.run(state, driver=host, github=github, plan_only=True)
    assert admitted["planning"]["revision"] > observed["planning"]["revision"]
    assert next(entry for entry in host.planning()[-1]["data"]["backlog"] if entry["number"] == 4)["admitted"] is True
    count = len(host.requests)
    cached = issues.run(state, driver=host, github=github, plan_only=True)
    assert len(host.requests) == count and cached["invocations"] == admitted["invocations"] == 5
    assert cached["deadline"] == initial["deadline"] and host.detailed() == [] and github.writes == []


def test_stage_boundary_reassesses_latest_accepted_code_and_replans_stale_detail(batch):
    project, state, spec, github = batch
    populate(github, 2)
    issues.initialize(spec, state, github=github)
    host = PortfolioHost(foundation=1)
    initial = issues.run(state, driver=host, github=github, plan_only=True)
    stale = {"status": "work", "summary": "Stale detail from before the foundation", "dependencies": [],
             "candidates": [{"key": "issue-2", "title": "Correct value 2", "kind": "bugfix", "priority": 5,
                             "why": "Old source assumption", "evidence": [{"source": "app.py", "observation": "Old value"}],
                             "acceptance": ["Behavior for issue 2 works"]}]}
    IssueStore(state).update_issue(2, "planned", plan=stale, plan_base_sha=initial["base_sha"],
                                   roadmap_revision=initial["planning"]["revision"])
    result = issues.run(state, driver=host, github=github)
    foundation = next(row for row in result["issues"] if row["number"] == 1)
    expansion = next(row for row in result["issues"] if row["number"] == 2)
    assert len(host.planning()) == 2
    assert host.planning()[1]["data"]["repository_base_sha"] == foundation["head"]
    assert host.planning()[1]["code"] == "value = 1\n"
    later = next(request for request in host.detailed() if request["data"]["issue"]["number"] == 2)
    assert later["head"] == foundation["head"] and later["code"] == "value = 1\n"
    assert later["data"]["roadmap"]["revision"] > initial["planning"]["revision"]
    assert expansion["plan_base_sha"] == foundation["head"] and expansion["plan"]["summary"] != stale["summary"]
    assert [request["data"]["task"]["key"] for request in host.requests if request["phase"] == "implement"] == ["issue-1", "issue-2"]
    assert result["invocations"] == 8 and git(project, "show", f"{result['accepted_head']}:app.py") == "value = 2"
    assert github.writes == []


@pytest.mark.parametrize("failure", ["mutation", "stop", "unknown_cleanup"])
def test_global_planning_failure_never_activates_roadmap_or_implementation(batch, failure):
    project, state, spec, github = batch
    populate(github, 1)
    issues.initialize(spec, state, github=github)

    class FailingHost(PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if failure == "mutation":
                (request.cwd / "app.py").write_text("value = 999\n")
            elif failure == "stop":
                issues.stop(state)
            else:
                return DriverResult(payload=result.payload, cleanup_confirmed=False)
            return result

    host = FailingHost(foundation=1)
    if failure == "stop":
        result = issues.run(state, driver=host, github=github)
        assert result["status"] == "stopped" and result["stop_requested"]
    else:
        with pytest.raises(RuntimeError):
            issues.run(state, driver=host, github=github)
        result = issues.status(state)
        assert result["status"] == "blocked"
    assert result["planning"]["roadmap"] is None and result["planning"]["status"] == "incomplete"
    assert result["invocations"] == 1 and host.detailed() == [] and github.writes == []
    assert git(project, "show", "HEAD:app.py") == "value = 0"
    if failure == "unknown_cleanup":
        assert result["reason"] == "worker_cleanup_unconfirmed" and result["calls"][0]["state"] == "unknown"


def test_unresolved_foundation_survives_replanning_and_new_intake_until_explicit_decision(batch):
    from dutyloop import backlog_runner

    project, state, spec, github = batch
    populate(github, 2)
    spec.update(intake={"mode": "auto"}, queue={"batch_size": 1})
    repository.initialize(spec, state, github=github)

    class DecisionHost(PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            data = self.requests[-1]["data"]
            if data.get("planning_operation") == "roadmap":
                foundation = next(entry for entry in data["backlog"] if entry["number"] == 1)
                if foundation.get("decision_id"):
                    item = next(item for item in result.payload["items"] if item["number"] == 1)
                    item.update(disposition="defer", stage_id="", question="",
                                reason="The operator explicitly deferred this foundation requirement")
                    result.payload["stages"] = [item for item in result.payload["stages"]
                                                if item["id"] != "foundation"]
            elif request.phase == "discover" and data["issue"]["number"] == 1:
                return DriverResult(payload={"status": "needs_decision", "summary": "Choose the foundational API behavior",
                                             "dependencies": [], "candidates": []})
            return result

    host = DecisionHost(foundation=1)
    blocked = issues.run(state, driver=host, github=github)
    assert host.detailed()[0]["data"]["issue"]["number"] == 1
    assert not any(request["phase"] == "implement" for request in host.requests)
    assert blocked["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    assert [item["id"] for item in blocked["planning"]["roadmap"]["stages"]] == ["expansion"]
    current, work, gate = backlog_runner.select_work(blocked)
    assert current["id"] == "foundation" and work == [] and gate == "stage_has_no_runnable_work"

    github.issues[3] = workflow.source(3)
    reconciled = repository.reconcile(state, github=github)
    assert next(source for source in reconciled["sources"] if source["number"] == 3)["admitted"]
    refreshed = issues.run(state, driver=host, github=github)
    assert refreshed["planning"]["revision"] > blocked["planning"]["revision"]
    assert refreshed["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    assert backlog_runner.select_work(refreshed)[0]["id"] == "foundation"
    assert not any(request["phase"] == "implement" for request in host.requests)

    calls = len(host.requests)
    decided = repository.resolve(state, 1, "Defer issue 1 and proceed with the independent expansion work", github=github)
    foundation = next(row for row in decided["issues"] if row["number"] == 1)
    assert foundation["decision_id"]
    assert foundation["decision_id"] != refreshed["planning"]["stage_barrier"]["decision_ids"].get("1")
    assert decided["planning"]["status"] == "stale" and len(host.requests) == calls
    allowed = issues.run(state, driver=host, github=github)
    assert [request["data"]["task"]["key"] for request in host.requests
            if request["phase"] == "implement"] == ["issue-2"]
    assert next(item for item in allowed["planning"]["roadmap"]["items"]
                if item["number"] == 1)["disposition"] == "defer"
    assert git(project, "show", f"{allowed['accepted_head']}:app.py") == "value = 2"
    assert github.writes == []


def test_observed_human_stage_attention_is_visible_and_queued_member_can_be_resolved(batch):
    project, state, spec, github = batch
    populate(github, 1)
    repository.initialize(spec, state, github=github)

    class HumanGateHost(PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if self.requests[-1]["data"].get("planning_operation") == "roadmap":
                result.payload["stages"][0]["requires_human_acceptance"] = True
            return result

    host = HumanGateHost(foundation=1)
    planned = issues.run(state, driver=host, github=github, plan_only=True)
    assert planned["issues"][0]["state"] == "queued" and planned["invocations"] == 1
    repository.set_mode(state, "auto")
    visible = repository.status(state)
    attention = next(item for item in visible["attention"] if item["reason"] == "stage_requires_human_acceptance")
    assert attention["issue_numbers"] == [1] and attention["type"] == "planning"
    assert len(host.requests) == 1

    decision = "The required human review is complete; preserve the current API"
    resolved = repository.resolve(state, 1, decision, github=github)
    assert resolved["issues"][0]["state"] == "queued" and resolved["issues"][0]["decision_id"]
    assert resolved["planning"]["status"] == "stale" and resolved["invocations"] == 1
    assert len(host.requests) == 1 and host.detailed() == [] and github.writes == []
    assert git(project, "show", "HEAD:app.py") == "value = 0"
    assert all(row["head"] is None for row in resolved["issues"])
    repeated = repository.resolve(state, 1, decision, github=github)
    assert repeated["issues"][0]["decision_id"] != resolved["issues"][0]["decision_id"]
    assert repeated["issues"][0]["decision"] == resolved["issues"][0]["decision"]
    assert repeated["invocations"] == 1 and len(host.requests) == 1 and github.writes == []


def test_failed_foundation_cannot_be_removed_by_an_automatic_replacement_roadmap(batch):
    from dutyloop import backlog_runner

    project, state, spec, github = batch
    populate(github, 2)
    issues.initialize(spec, state, github=github)
    host = PortfolioHost(foundation=1)
    host.fail_gate = True
    result = issues.run(state, driver=host, github=github)
    assert [request["data"]["task"]["key"] for request in host.requests
            if request["phase"] == "implement"] == ["issue-1"]
    assert next(row for row in result["issues"] if row["number"] == 1)["state"] == "failed"
    assert [item["id"] for item in result["planning"]["roadmap"]["stages"]] == ["expansion"]
    assert result["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    current, work, gate = backlog_runner.select_work(result)
    assert current["id"] == "foundation" and work == [] and gate == "stage_has_no_runnable_work"
    assert result["accepted_head"] == result["base_sha"]
    assert git(project, "show", "HEAD:app.py") == "value = 0" and github.writes == []


@pytest.mark.parametrize("human", [False, True])
def test_freshly_resolved_work_remains_executable_inside_the_active_stage_barrier(human):
    from dutyloop import backlog_runner

    numbers = [1] if human else [1, 2]
    original = stage("foundation", numbers)
    original["requires_human_acceptance"] = human
    items = [{"number": number, "disposition": "work", "dependencies": [] if number == 1 else [1],
              "stage_id": "foundation"} for number in numbers]
    snapshot = {"spec": {"github": {"delivery": "local"}},
                "issues": [{"number": number, "state": "queued", "decision_id": "new" if number == 1 else None}
                           for number in numbers],
                "planning": {"roadmap": {"stages": [stage("foundation", numbers)], "items": items},
                             "stage_barrier": {"stage": original,
                                               "decision_ids": {str(number): "old" if number == 1 else None
                                                                for number in numbers}}}}
    current, work, gate = backlog_runner.select_work(snapshot)
    assert current["id"] == "foundation" and current["issue_numbers"] == numbers
    assert [item["number"] for item in work] == [1] and gate is None


def test_replacement_human_gate_is_preserved_when_filtering_active_stage_members():
    from dutyloop import backlog_runner

    guarded = stage("foundation", [1, 2])
    replacement = stage("human-review", [2])
    replacement["requires_human_acceptance"] = True
    snapshot = {"spec": {"github": {"delivery": "local"}},
                "issues": [{"number": 1, "state": "needs_decision"}, {"number": 2, "state": "queued"}],
                "planning": {"roadmap": {"stages": [replacement],
                                         "items": [{"number": 1, "disposition": "needs_decision", "dependencies": [],
                                                    "stage_id": ""},
                                                   {"number": 2, "disposition": "work", "dependencies": [],
                                                    "stage_id": "human-review"}]},
                             "stage_barrier": {"stage": guarded, "decision_ids": {"1": None, "2": None}}}}
    current, work, gate = backlog_runner.select_work(snapshot)
    assert current["id"] == "human-review" and current["requires_human_acceptance"]
    assert work == [] and gate == "stage_requires_human_acceptance"


def test_replacement_stage_order_is_preserved_inside_an_unfinished_active_stage():
    from dutyloop import backlog_runner

    snapshot = {"spec": {"github": {"delivery": "local"}},
                "issues": [{"number": 1, "state": "queued"}, {"number": 2, "state": "queued"}],
                "planning": {"roadmap": {"stages": [stage("first", [2]), stage("second", [1])],
                                         "items": [{"number": 1, "disposition": "work", "dependencies": [],
                                                    "stage_id": "second"},
                                                   {"number": 2, "disposition": "work", "dependencies": [],
                                                    "stage_id": "first"}]},
                             "stage_barrier": {"stage": stage("foundation", [1, 2]),
                                               "decision_ids": {"1": None, "2": None}}}}
    current, work, gate = backlog_runner.select_work(snapshot)
    assert current["id"] == "first" and current["issue_numbers"] == [2]
    assert [item["number"] for item in work] == [2] and gate is None


@pytest.mark.parametrize("closure_known", [True, False], ids=["already-closed", "closure-discovered-during-resolve"])
def test_explicit_closed_stage_decision_releases_independent_work_without_verifying_closure(batch, closure_known):
    from dutyloop import backlog, backlog_runner

    project, state, spec, github = batch
    populate(github, 3)
    spec["intake"] = {"mode": "auto"}
    repository.initialize(spec, state, github=github)

    class FoundationDecisionHost(PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            data = self.requests[-1]["data"]
            if request.phase == "discover" and not data.get("planning_operation") and data["issue"]["number"] == 1:
                return DriverResult(payload={"status": "needs_decision", "summary": "Choose the foundational API behavior",
                                             "dependencies": [], "candidates": []})
            return result

    host = FoundationDecisionHost(foundation=1)
    host.dependencies = {3: [1]}
    blocked = issues.run(state, driver=host, github=github)
    assert blocked["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    assert backlog_runner.select_work(blocked)[1] == []
    github.issues[1]["state"] = "closed"
    if closure_known:
        repository.reconcile(state, github=github)
        blocked = issues.run(state, driver=host, github=github)
        assert next(row for row in blocked["issues"] if row["number"] == 1)["state"] == "external_closed"
        assert backlog_runner.select_work(blocked)[1] == []

    before = repository.status(state)
    calls = len(host.requests)
    resolved = repository.resolve(state, 1, "Acknowledge external closure; defer issue 1 and proceed with independent work",
                                  github=github)
    closed = next(row for row in resolved["issues"] if row["number"] == 1)
    assert closed["decision_id"] and closed["decision_id"] != before["planning"]["stage_barrier"]["decision_ids"].get("1")
    assert closed["state"] == "external_closed" and closed["source"]["state"] == "closed"
    assert backlog.verified(closed, "local") is False
    assert resolved["invocations"] == before["invocations"] and resolved["deadline"] == before["deadline"]
    assert len(host.requests) == calls and github.writes == []

    repository.reconcile(state, github=github)
    planned = issues.run(state, driver=host, github=github, plan_only=True)
    item = next(item for item in planned["planning"]["roadmap"]["items"] if item["number"] == 1)
    assert item["disposition"] == "defer"
    current, work, gate = backlog_runner.select_work(planned)
    assert current["id"] == "expansion" and [item["number"] for item in work] == [2] and gate is None
    assert next(item for item in planned["planning"]["roadmap"]["items"] if item["number"] == 3)["dependencies"] == [1]
    assert planned["invocations"] == before["invocations"] + 1 and planned["deadline"] == before["deadline"]
    assert not any(request["phase"] == "implement" for request in host.requests)
    assert not any(event["kind"] == "backlog_stage_completed"
                   for event in issues.events(state, limit=1000)["events"])
    assert planned["accepted_head"] == planned["base_sha"]
    assert git(project, "show", "HEAD:app.py") == "value = 0" and github.writes == []


@pytest.mark.parametrize("first_stage_outcome", ["verified", "explicitly-deferred"])
def test_released_first_barrier_does_not_erase_the_next_unfinished_active_stage(batch, first_stage_outcome):
    from dutyloop import backlog_runner

    _, state, spec, github = batch
    populate(github, 3)
    issues.initialize(spec, state, github=github)

    class ThreeStageHost(PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            data = self.requests[-1]["data"]
            if data.get("planning_operation") == "roadmap":
                first = next(entry for entry in data["backlog"] if entry["number"] == 1)
                if first.get("decision_id") and first_stage_outcome == "explicitly-deferred":
                    item = next(item for item in result.payload["items"] if item["number"] == 1)
                    item.update(disposition="defer", reason="The operator explicitly deferred the first stage",
                                question="", stage_id="")
                result.payload["stages"] = []
                for number, identifier in ((1, "foundation"), (2, "second"), (3, "third")):
                    item = next(item for item in result.payload["items"] if item["number"] == number)
                    if item["disposition"] == "work":
                        item["stage_id"] = identifier
                        result.payload["stages"].append(stage(identifier, [number]))
            elif request.phase == "discover":
                number = data["issue"]["number"]
                if number == 2 or number == 1 and not data.get("operator_decision"):
                    return DriverResult(payload={"status": "needs_decision", "summary": f"Choose behavior for stage {number}",
                                                 "dependencies": [], "candidates": []})
            return result

    host = ThreeStageHost(foundation=1)
    first = issues.run(state, driver=host, github=github)
    assert first["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    assert not any(request["phase"] == "implement" for request in host.requests)
    decision = ("Preserve the API and implement the first foundation requirement" if first_stage_outcome == "verified"
                else "Explicitly defer the first foundation requirement and proceed to the second stage")
    resolved = issues.resolve(state, 1, decision, github=github)
    assert resolved["issues"][0]["decision_id"]
    second = issues.run(state, driver=host, github=github)
    implemented = [request["data"]["task"]["key"] for request in host.requests if request["phase"] == "implement"]
    assert "issue-3" not in implemented
    assert implemented == (["issue-1"] if first_stage_outcome == "verified" else [])
    assert next(row for row in second["issues"] if row["number"] == 2)["state"] == "needs_decision"
    assert second["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [2]
    current, work, gate = backlog_runner.select_work(second)
    assert current["id"] == "second" and work == [] and gate == "stage_has_no_runnable_work"
    completed = [event["data"]["stage_id"] for event in issues.events(state, limit=1000)["events"]
                 if event["kind"] == "backlog_stage_completed"]
    assert "second" not in completed
    if first_stage_outcome == "explicitly-deferred":
        assert "foundation" not in completed
    assert github.writes == []


def test_fresh_decision_cannot_retire_obligation_before_replacement_keeps_it_as_work(batch):
    from dutyloop import backlog_runner

    _, state, spec, github = batch
    github.issues = {number: workflow.source(number) for number in (1, 3)}
    spec["queue"] = {"batch_size": 1}
    issues.initialize(spec, state, github=github)

    class ReorderingHost(PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            data = self.requests[-1]["data"]
            if data.get("planning_operation") == "roadmap":
                first = next(entry for entry in data["backlog"] if entry["number"] == 1)
                if first.get("decision_id"):
                    # The fresh decision still requires work on #1; it is not a
                    # waiver merely because the previous roadmap asked a question.
                    assert next(item for item in result.payload["items"] if item["number"] == 1)["disposition"] == "work"
                    result.payload["stages"] = [stage("expansion", [3]), stage("foundation", [1])]
            elif request.phase == "discover" and data["issue"]["number"] == 1 and not data.get("operator_decision"):
                return DriverResult(payload={"status": "needs_decision", "summary": "Choose the foundational behavior",
                                             "dependencies": [], "candidates": []})
            return result

    host = ReorderingHost(foundation=1)
    blocked = issues.run(state, driver=host, github=github)
    assert blocked["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    assert next(item for item in blocked["planning"]["roadmap"]["items"] if item["number"] == 1)["disposition"] == "needs_decision"
    issues.resolve(state, 1, "Implement the first foundation requirement before independent expansion", github=github)
    result = issues.run(state, driver=host, github=github)
    implemented = [request["data"]["task"]["key"] for request in host.requests if request["phase"] == "implement"]
    assert "issue-3" not in implemented
    assert result["planning"]["stage_barrier"]["stage"]["issue_numbers"] == [1]
    current, work, gate = backlog_runner.select_work(result)
    assert current["id"] == "foundation" and work == [] and gate == "stage_has_no_runnable_work"
    assert github.writes == []
