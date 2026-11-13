"""Planning reuses historical source investigations only across verified progress."""

import copy

import pytest

from dutyloop import backlog_runner, issues
from dutyloop.drivers import DriverResult
from dutyloop.workspace import git
import test_backlog_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


def test_chunked_stage_boundary_reuses_sources_but_reassesses_current_code_within_budget(batch):
    project, state, spec, github = batch
    workflow.populate(github, 2, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000}
    spec["limits"]["max_invocations"] = 10
    issues.initialize(spec, state, github=github)
    host = workflow.PortfolioHost(foundation=1)
    initial = issues.run(state, driver=host, github=github, plan_only=True)
    result = issues.run(state, driver=host, github=github)

    assert [row["state"] for row in result["issues"]] == ["accepted_local", "accepted_local"]
    assert len(host.planning("assess")) == 2
    roadmaps = host.planning("roadmap")
    assert len(roadmaps) == 2
    foundation = result["issues"][0]
    refreshed = roadmaps[-1]
    assert refreshed["head"] == foundation["head"]
    assert refreshed["code"] == "value = 1\n"
    assert refreshed["data"]["assessment_base_sha"] == initial["base_sha"]
    assert refreshed["data"]["assessment_reuse_mode"] == "verified_progress"
    assert len(refreshed["data"]["assessments"]) == 2
    assert result["planning"]["reused_issues"] == [1, 2]
    assert result["planning"]["coverage"]["unassessed"] == []
    assert result["invocations"] == 10 and result["deadline"] == initial["deadline"]
    assert host.calls.count("review") == 2
    assert git(project, "show", f"{result['accepted_head']}:app.py") == "value = 2"
    assert git(project, "show", "HEAD:app.py") == "value = 0"
    assert github.writes == []


def planning_snapshot():
    from test_issue_workflow import source

    snapshot = {"spec": {"github": {"repository": "example/project", "delivery": "local"},
                         "queue": {}, "mission": "Preserve the API"},
                "issues": [{"number": 1, "source": source(1), "state": "accepted_local",
                            "base_sha": "old", "head": "new", "verified_tree": "tree",
                            "review": {"accepted": True}, "verification": [{"returncode": 0}]}]}
    previous = {"source_fingerprint": backlog_runner.source_fingerprint(snapshot),
                "base_sha": "old", "assessment_base_sha": "old",
                "assessment_context_fingerprint": backlog_runner.digest(
                    {"base_sha": "old", **backlog_runner.authority(snapshot)})}
    return snapshot, previous


def test_verified_progress_proves_complete_chain_without_accepting_unreviewed_or_external_heads():
    snapshot, previous = planning_snapshot()
    assert backlog_runner.verified_progress(snapshot, previous, "new")
    for update in ({"state": "failed"}, {"review": {"accepted": False}},
                   {"verification": [{"returncode": 1}]}, {"base_sha": "unrelated"},
                   {"verified_tree": ""}):
        changed = copy.deepcopy(snapshot)
        changed["issues"][0].update(update)
        assert not backlog_runner.verified_progress(changed, previous, "new")
    assert not backlog_runner.verified_progress(snapshot, previous, "external")
    second = copy.deepcopy(snapshot["issues"][0])
    second.update(number=2, base_sha="new", head="latest")
    second["source"]["number"] = 2
    snapshot["issues"].append(second)
    previous["source_fingerprint"] = backlog_runner.source_fingerprint(snapshot)
    assert backlog_runner.verified_progress(snapshot, previous, "latest")
    snapshot["issues"][1]["base_sha"] = "external-intermediate"
    assert not backlog_runner.verified_progress(snapshot, previous, "latest")


def test_changed_authority_sources_or_decisions_cannot_reuse_old_base_investigations():
    snapshot, previous = planning_snapshot()
    changes = [lambda item: item["spec"].update(mission="Different responsibility"),
               lambda item: item["spec"]["queue"].update(planning_context="Different milestone"),
               lambda item: item["issues"][0]["source"].update(body="Changed acceptance"),
               lambda item: item["issues"][0].update(decision="Change behavior", decision_id="fresh")]
    for change in changes:
        changed = copy.deepcopy(snapshot)
        change(changed)
        assert not backlog_runner.verified_progress(changed, previous, "new")


def test_merged_progress_requires_post_merge_checks_and_exact_verified_merge():
    snapshot, previous = planning_snapshot()
    snapshot["spec"]["github"]["delivery"] = "merge"
    row = snapshot["issues"][0]
    row.update(state="closed", merge_sha="merged", post_merge_verified="merged",
               post_merge_checks=[{"returncode": 0}])
    assert backlog_runner.verified_progress(snapshot, previous, "merged")
    for update in ({"post_merge_verified": "different"}, {"post_merge_checks": []},
                   {"post_merge_checks": [{"returncode": 0, "cleanup_confirmed": False}]},
                   {"state": "pull_request"}):
        changed = copy.deepcopy(snapshot)
        changed["issues"][0].update(update)
        assert not backlog_runner.verified_progress(changed, previous, "merged")


@pytest.mark.parametrize("damage", ["missing_note", "missing_receipt"])
def test_partial_or_unbound_historical_notes_require_fresh_investigation(batch, damage):
    from dutyloop.issue_store import IssueStore

    _, state, spec, github = batch
    workflow.populate(github, 2, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "batch_size": 1}
    issues.initialize(spec, state, github=github)
    first_host = workflow.PortfolioHost(foundation=1)
    first = issues.run(state, driver=first_host, github=github)
    assert first["issues"][0]["state"] == "accepted_local"
    damaged = copy.deepcopy(first["planning"])
    if damage == "missing_note":
        damaged["assessments"] = damaged["assessments"][:1]
    else:
        damaged["chunks"] = [chunk for chunk in damaged["chunks"] if chunk["operation"] != "assess"]
    damaged.update(status="stale", expected_head=None)
    IssueStore(state).save_planning(damaged)
    host = workflow.PortfolioHost(foundation=1)
    second = issues.run(state, driver=host, github=github, plan_only=True)
    assert len(host.planning("assess")) == 2
    assert second["planning"]["reused_issues"] == []
    assert second["planning"]["assessment_base_sha"] == first["accepted_head"]
    assert second["planning"]["assessment_reuse_mode"] == "same_base"


@pytest.mark.parametrize("failure", ["stop", "unknown_cleanup"])
def test_historical_synthesis_cannot_activate_work_after_stop_or_unknown_cleanup(batch, failure):
    _, state, spec, github = batch
    workflow.populate(github, 2, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "batch_size": 1}
    issues.initialize(spec, state, github=github)
    first = issues.run(state, driver=workflow.PortfolioHost(foundation=1), github=github)

    class InterruptedHost(workflow.PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            assert self.requests[-1]["data"]["assessment_reuse_mode"] == "verified_progress"
            if failure == "stop":
                issues.stop(state)
                return result
            return DriverResult(payload=result.payload, cleanup_confirmed=False)

    host = InterruptedHost(foundation=1)
    if failure == "stop":
        second = issues.run(state, driver=host, github=github)
        assert second["status"] == "stopped"
    else:
        with pytest.raises(RuntimeError, match="cleanup"):
            issues.run(state, driver=host, github=github)
        second = issues.status(state)
        assert second["status"] == "blocked" and second["reason"] == "worker_cleanup_unconfirmed"
        assert any(call["state"] == "unknown" for call in second["calls"])
    assert second["planning"]["roadmap"] is None
    assert second["invocations"] == first["invocations"] + 1
    assert second["deadline"] == first["deadline"]
    assert host.detailed() == [] and host.calls == ["discover"] and github.writes == []


def test_successive_stage_refreshes_keep_historical_origin_and_check_each_new_base(batch):
    _, state, spec, github = batch
    workflow.populate(github, 3, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000}
    spec["limits"]["max_invocations"] = 15
    initial = issues.initialize(spec, state, github=github)

    class ThreeStagesHost(workflow.PortfolioHost):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if self.requests[-1]["data"].get("planning_operation") == "roadmap":
                result.payload["stages"] = []
                for item in result.payload["items"]:
                    if item["disposition"] == "work":
                        item["stage_id"] = f"stage-{item['number']}"
                        result.payload["stages"].append(workflow.stage(item["stage_id"], [item["number"]]))
            return result

    host = ThreeStagesHost(foundation=1)
    result = issues.run(state, driver=host, github=github)
    assert all(row["state"] == "accepted_local" for row in result["issues"])
    assert len(host.planning("assess")) == 3 and len(host.planning("roadmap")) == 3
    assert result["invocations"] == 15 and host.calls.count("review") == 3
    for index, request in enumerate(host.planning("roadmap")[1:]):
        assert request["head"] == result["issues"][index]["head"]
        assert request["data"]["assessment_base_sha"] == initial["base_sha"]
        assert request["data"]["assessment_reuse_mode"] == "verified_progress"
    assert result["planning"]["assessment_base_sha"] == initial["base_sha"]
