"""PR-only delivery keeps one owned PR current without granting merge authority."""

import json
import threading

import pytest
import test_issue_workflow as workflow

from dutyloop import issues, repository
from dutyloop.drivers import DriverResult
from dutyloop.issue_store import IssueStore
from dutyloop.repository_store import RepositoryStore
from dutyloop.store import Store
from dutyloop.workspace import git

batch = workflow.batch
contained_batch = workflow.contained_batch


def published(batch, **github_options):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    spec["github"].update(delivery="pull_request", **github_options)
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=workflow.Host(), github=github)
    return state, github, result["issues"][0]


def feedback(kind="review", *, number=4, body="Cover the existing behavior's edge case", author="maintainer",
             association="MEMBER", head=None):
    return {"kind": kind, "id": number, "body": body, "author": author,
            "author_association": association, "updated_at": "2026-11-10T01:00:00Z",
            "commit_id": head, "state": "CHANGES_REQUESTED" if kind == "review" else None}


class FeedbackHost(workflow.Host):
    def __init__(self, verdict="work", **kwargs):
        super().__init__(**kwargs)
        self.verdict, self.assessments = verdict, []

    def run(self, request, should_stop):
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        if request.phase == "discover" and "pull_request_feedback" in data:
            self.calls.append(request.phase)
            self.assessments.append(data)
            return DriverResult(payload={"status": self.verdict, "summary": "Review assessment"})
        return super().run(request, should_stop)


def test_ci_failure_is_repaired_in_pr_only_mode_without_merging(batch):
    state, github, first = published(batch)
    old_checks = github.checks
    github.checks = lambda head: [{"name": "tests", "status": "completed", "conclusion": "failure"}] \
        if head == first["head"] else old_checks(head)
    result = issues.run(state, driver=workflow.Host(), github=github)
    row = result["issues"][0]
    assert row["state"] == "pull_request"
    assert row["head"] != first["head"]
    assert row["branch"] == first["branch"] and row["pr_number"] == first["pr_number"]
    assert row["delivery_attempts"] == 2
    assert github.writes == [("create", 100), ("update", 100)]
    assert github.issues[1]["state"] == "open"


@pytest.mark.parametrize("kind", ["review", "inline", "conversation"])
def test_trusted_review_feedback_is_reassessed_and_repaired_once_on_owned_pr(batch, kind):
    state, github, first = published(batch)
    github.feedback = [feedback(kind, head=first["head"] if kind != "conversation" else None)]
    host = FeedbackHost()
    repaired = issues.run(state, driver=host, github=github)
    row = repaired["issues"][0]
    assert row["state"] == "pull_request" and row["head"] != first["head"]
    assert row["pr_number"] == first["pr_number"] and row["branch"] == first["branch"]
    assert len(host.assessments) == 1
    assert host.assessments[0]["pull_request_feedback"] == github.feedback
    assert "ci_repair = True" in git(batch[0], "show", f"{row['head']}:app.py")
    resumed = issues.run(state, driver=host, github=github)
    assert resumed["invocations"] == repaired["invocations"]
    assert len(host.assessments) == 1
    assert github.writes == [("create", 100), ("update", 100)]


@pytest.mark.parametrize("verdict,expected", [("needs_decision", "needs_decision"), ("no_work", "pull_request")])
def test_ambiguous_or_nonactionable_feedback_never_enters_implementation(batch, verdict, expected):
    state, github, first = published(batch)
    github.feedback = [feedback(body="Choose a new public behavior", head=first["head"])]
    host = FeedbackHost(verdict)
    result = issues.run(state, driver=host, github=github)
    assert result["issues"][0]["state"] == expected
    assert result["issues"][0]["head"] == first["head"]
    assert host.calls == ["discover"]
    assert github.writes == [("create", 100)]
    if verdict == "no_work":
        resumed = issues.run(state, driver=host, github=github)
        assert resumed["invocations"] == result["invocations"]


@pytest.mark.parametrize("author", ["stranger", "dependabot[bot]"])
def test_untrusted_feedback_is_retained_without_model_or_branch_mutation(batch, author):
    state, github, first = published(batch)
    github.feedback = [feedback(association="NONE", author=author, head=first["head"])]
    host = FeedbackHost()
    result = issues.run(state, driver=host, github=github)
    assert result["issues"][0]["head"] == first["head"]
    assert host.calls == []
    assert github.writes == [("create", 100)]
    assert result["issues"][0]["ignored_feedback"]


def test_explicit_review_author_is_eligible_for_reassessment(batch):
    state, github, first = published(batch, review_authors=["outside-reviewer"])
    github.feedback = [feedback(association="NONE", author="OUTSIDE-REVIEWER", head=first["head"])]
    host = FeedbackHost("needs_decision")
    result = issues.run(state, driver=host, github=github)
    assert len(host.assessments) == 1
    assert result["issues"][0]["state"] == "needs_decision"


def test_explicit_bot_author_allowlist_permits_bounded_reassessment(batch):
    actor = "chatgpt-codex-connector[bot]"
    state, github, first = published(batch, review_authors=[actor])
    before = issues.status(state)
    github.feedback = [feedback(association="NONE", author=actor, head=first["head"])]
    host = FeedbackHost("needs_decision")
    result = issues.run(state, driver=host, github=github)
    assert host.calls == ["discover"] and len(host.assessments) == 1
    assert result["invocations"] == before["invocations"] + 1
    assert result["issues"][0]["state"] == "needs_decision"
    assert result["issues"][0]["head"] == first["head"]
    assert github.writes == [("create", 100)]


def test_maintenance_opt_out_leaves_ci_and_feedback_untouched(batch):
    state, github, first = published(batch, maintain_pull_requests=False)
    github.check_results[0]["conclusion"] = "failure"
    github.feedback = [feedback(head=first["head"])]
    host = FeedbackHost()
    result = issues.run(state, driver=host, github=github)
    assert result["issues"][0]["state"] == "pull_request"
    assert result["issues"][0]["head"] == first["head"]
    assert host.calls == [] and github.writes == [("create", 100)]


def test_review_retry_limit_stops_repeated_new_feedback_without_merge(batch):
    state, github, first = published(batch)
    store = IssueStore(state)
    store.update_issue(1, "pull_request", delivery_attempts=2)
    github.feedback = [feedback(head=first["head"])]
    host = FeedbackHost()
    result = issues.run(state, driver=host, github=github)
    assert result["issues"][0]["reason"] == "pr_retry_limit"
    assert not host.calls and github.writes == [("create", 100)]


def test_resolve_owned_pr_preserves_identity_and_replans_on_its_head(batch):
    state, github, first = published(batch)
    IssueStore(state).update_issue(1, "needs_decision", reason="review_scope_ambiguous")
    github.issues[1]["body"] = "Explicitly confirmed updated requirement"
    resolved = issues.resolve(state, 1, "Keep the existing API and add edge-case coverage", github=github)
    row = resolved["issues"][0]
    assert row["state"] == "queued" and row["head"] == first["head"]
    assert row["branch"] == first["branch"] and row["pr_number"] == first["pr_number"]
    assert row["source"]["body"] == github.issues[1]["body"]
    planned = issues.run(state, driver=workflow.Host(), github=github, plan_only=True)
    assert planned["issues"][0]["state"] == "planned"
    assert planned["issues"][0]["head"] == first["head"]
    assert planned["deadline"] == resolved["deadline"]


def test_resolve_owned_pr_rejects_remote_metadata_drift(batch):
    state, github, _ = published(batch)
    IssueStore(state).update_issue(1, "needs_decision")
    github.prs[100]["body"] += "\nA maintainer changed this"
    with pytest.raises(RuntimeError, match="metadata differs"):
        issues.resolve(state, 1, "Continue within original scope", github=github)
    assert issues.status(state)["issues"][0]["state"] == "needs_decision"


def test_resolved_review_repair_updates_the_original_pr(batch):
    state, github, first = published(batch)
    github.feedback = [feedback(head=first["head"])]
    decision = issues.run(state, driver=FeedbackHost("needs_decision"), github=github)
    issues.resolve(state, 1, "Add coverage for the existing documented edge case", github=github)

    class ResolvedHost(workflow.Host):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if request.phase == "implement":
                with (request.cwd / "app.py").open("a", encoding="utf-8") as output:
                    output.write("confirmed_edge_case = True\n")
            return result

    result = issues.run(state, driver=ResolvedHost(), github=github)
    row = result["issues"][0]
    assert row["state"] == "pull_request" and row["head"] != first["head"]
    assert row["pr_number"] == first["pr_number"] and row["branch"] == first["branch"]
    assert row["delivery_attempts"] == 2
    assert result["deadline"] == decision["deadline"]
    assert github.writes == [("create", 100), ("update", 100)]


def test_review_reassessment_obeys_global_invocation_budget(batch):
    state, github, first = published(batch)
    store = IssueStore(state)
    with store.connection() as db:
        data = store._read(db)
        data["invocations"] = data["spec"]["limits"]["max_invocations"] - 1
        store._save(db, data)
    github.feedback = [feedback(head=first["head"])]
    host = FeedbackHost()
    result = issues.run(state, driver=host, github=github)
    assert result["status"] == "limited"
    assert result["invocations"] == result["spec"]["limits"]["max_invocations"]
    assert host.calls == ["discover"]
    assert result["issues"][0]["head"] == first["head"]
    assert github.writes == [("create", 100)]


def test_review_reassessment_stop_never_dispatches_implementation(batch):
    state, github, first = published(batch)
    github.feedback = [feedback(head=first["head"])]

    class StoppingHost:
        def run(self, request, should_stop):
            issues.stop(state)
            return DriverResult(cancelled=True)

    result = issues.run(state, driver=StoppingHost(), github=github)
    assert result["status"] == "stopped" and result["stop_requested"]
    assert result["issues"][0]["head"] == first["head"]
    assert github.writes == [("create", 100)]


def test_review_planner_unknown_cleanup_retains_global_block(batch):
    state, github, first = published(batch)
    github.feedback = [feedback(head=first["head"])]

    class UnknownHost:
        def run(self, request, should_stop):
            return DriverResult(cleanup_confirmed=False)

    with pytest.raises(RuntimeError):
        issues.run(state, driver=UnknownHost(), github=github)
    result = issues.status(state)
    assert result["status"] == "blocked" and result["reason"] == "worker_cleanup_unconfirmed"
    assert result["calls"][-1]["state"] == "unknown"
    child = Store(result["issues"][0]["child_state"]).snapshot()
    assert child["status"] == "blocked" and child["reason"] == "worker_cleanup_unconfirmed"
    assert child["runner_pid"] is None
    assert github.writes == [("create", 100)]


def test_feedback_for_old_head_is_not_reapplied_to_current_branch(batch):
    state, github, first = published(batch)
    github.feedback = [feedback(head=first["base_sha"])]
    host = FeedbackHost()
    result = issues.run(state, driver=host, github=github)
    assert not host.calls and result["issues"][0]["head"] == first["head"]


def test_crash_after_known_review_assessment_retains_pending_repair(batch, monkeypatch):
    state, github, first = published(batch)
    github.feedback = [feedback("conversation")]
    original = issues.IssueRunner.assess_feedback
    host = FeedbackHost()

    def crash_after_assessment(self, *args):
        result = original(self, *args)
        assert self.row(1)["state"] == "planned"
        assert result["status"] == "work"
        raise RuntimeError("simulated crash after settled assessment")

    monkeypatch.setattr(issues.IssueRunner, "assess_feedback", crash_after_assessment)
    with pytest.raises(RuntimeError, match="simulated crash"):
        issues.run(state, driver=host, github=github)
    assert issues.status(state)["issues"][0]["state"] == "planned"
    assert issues.status(state)["issues"][0]["feedback_receipts"]
    issues.recover(state, acknowledge=True, github=github)
    monkeypatch.setattr(issues.IssueRunner, "assess_feedback", original)
    repaired = issues.run(state, driver=host, github=github)
    row = repaired["issues"][0]
    assert row["state"] == "pull_request" and row["head"] != first["head"]
    assert row["branch"] == first["branch"] and row["pr_number"] == first["pr_number"]
    assert host.calls == ["discover", "implement", "review"]
    assert len(host.assessments) == 1
    resumed = issues.run(state, driver=host, github=github)
    assert resumed["invocations"] == repaired["invocations"]
    assert github.writes == [("create", 100), ("update", 100)]


def test_direct_issue_executor_obeys_repository_observe_policy(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    spec["github"]["delivery"] = "pull_request"
    issues.initialize(spec, state, github=github)
    RepositoryStore(state).create({"mode": "observe", "poll_seconds": .05})
    host = workflow.Host()
    result = issues.run(state, driver=host, github=github, plan_only=False)
    assert result["issues"][0]["state"] == "planned"
    assert host.calls == ["discover"] and not github.writes
    # A caller holding an old auto-mode snapshot still cannot dispatch a task.
    again = issues.run(state, driver=host, github=github, plan_only=False)
    assert again["invocations"] == result["invocations"] and not github.writes


def test_issue_executor_refuses_incomplete_repository_initialization(batch):
    _, state, spec, github = batch
    issues.initialize(spec, state, github=github)
    (state / "repository.initializing").touch()
    host = workflow.Host()
    with pytest.raises(ValueError, match="initialization is incomplete"):
        issues.run(state, driver=host, github=github)
    assert host.calls == [] and not github.writes


def pending_merge(batch, *, repository_agent):
    _, state, spec, github = batch
    github.issues = {1: workflow.source(1)}
    spec["github"].update(delivery="merge", required_checks=["tests"])
    github.check_results = [{"name": "tests", "status": "in_progress", "conclusion": None}]
    if repository_agent:
        repository.initialize({**spec, "intake": {"mode": "auto", "poll_seconds": .05}}, state, github=github)
    else:
        issues.initialize(spec, state, github=github)
    store = IssueStore(state)
    assert store.begin()
    host = workflow.Host()
    runner = issues.IssueRunner(store, host, threading.Event(), github)
    runner.plan(runner.row(1))
    runner.implement(runner.row(1))
    runner.publish(runner.row(1))
    store.set_status("ready", runner_pid=None)
    return state, github, host


def test_repository_pending_merge_yields_for_new_intake_and_planning(batch, monkeypatch):
    state, github, host = pending_merge(batch, repository_agent=True)
    original_init = issues.IssueRunner.__init__

    def init_with_wait_guard(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        def unexpected_inner_wait(timeout):
            pytest.fail("Repository delivery must yield pending checks to the intake coordinator")

        self.interrupted.wait = unexpected_inner_wait

    monkeypatch.setattr(issues.IssueRunner, "__init__", init_with_wait_guard)
    ticks = []

    def coordinator_wait(store, interrupted, until):
        ticks.append(store.snapshot())
        if len(ticks) == 1:
            github.issues[3] = workflow.source(3)
            return False
        issues.stop(state)
        return True

    monkeypatch.setattr(repository, "_wait", coordinator_wait)
    result = repository.run(state, driver=host, github=github)
    assert len(ticks) == 2
    assert [row["state"] for row in result["issues"]] == ["pull_request", "planned"]
    assert ticks[0]["status"] == "ready" and ticks[0]["reason"] == "pull_requests_pending"
    assert host.calls == ["discover", "implement", "review", "discover"]
    assert github.writes == [("create", 100)]
    assert github.issues[1]["state"] == "open" and github.issues[3]["state"] == "open"


def test_finite_issue_merge_delivery_keeps_waiting_for_pending_checks(batch, monkeypatch):
    state, github, host = pending_merge(batch, repository_agent=False)
    store = IssueStore(state)
    assert store.begin()
    interrupted = threading.Event()
    waits = []

    def stop_after_inner_wait(timeout):
        waits.append(timeout)
        interrupted.set()

    monkeypatch.setattr(interrupted, "wait", stop_after_inner_wait)
    runner = issues.IssueRunner(store, host, interrupted, github)
    runner.loop()
    assert waits and store.snapshot()["issues"][0]["state"] == "pull_request"
    assert github.writes == [("create", 100)]
