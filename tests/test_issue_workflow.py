"""Real Git/verification with simulated hosts and GitHub, never account operations."""

import copy
from contextlib import nullcontext
import json
import signal
import subprocess
import sys
import threading

import pytest

from dutyloop import engine, issues
from dutyloop.drivers import DriverResult
from dutyloop.github import GitHubError
from dutyloop.issue_store import IssueStore
from dutyloop.workspace import git


def source(number):
    return {"number": number, "title": f"Correct value {number}", "body": f"Implement behavior {number}",
            "html_url": f"https://github.com/example/project/issues/{number}", "state": "open",
            "updated_at": "2026-01-01T00:00:00Z", "labels": [], "comments": []}


class FakeGitHub:
    def __init__(self, project, remote):
        self.project, self.remote = project, remote
        self.issues = {n: source(n) for n in (1, 2, 3)}
        self.prs, self.writes = {}, []
        self.check_results = [{"name": "tests", "status": "completed", "conclusion": "success"}]
        self.protected = True
        self.fail_close_after_effect = False

    def repository(self):
        return {"full_name": "example/project", "default_branch": "main"}

    def list_open_issues(self):
        return copy.deepcopy(list(self.issues.values()))

    def get_issue(self, number):
        return copy.deepcopy(self.issues[number])

    def branch_head(self, branch):
        return git(self.remote, "rev-parse", f"refs/heads/{branch}")

    def find_pull_request(self, head, base):
        for row in self.prs.values():
            if row["head"]["ref"] == head and row["base"]["ref"] == base:
                return self.pull_request(row["number"])
        return None

    def create_pull_request(self, head, base, title, body):
        number = 100 + len(self.prs)
        self.writes.append(("create", number))
        self.prs[number] = {"number": number, "title": title, "body": body, "state": "open", "draft": False,
                            "merged": False, "merge_commit_sha": None, "html_url": f"https://github.com/example/project/pull/{number}",
                            "head": {"ref": head, "sha": self.branch_head(head), "repo": {"full_name": "example/project"}},
                            "base": {"ref": base, "sha": self.branch_head(base), "repo": {"full_name": "example/project"}}}
        return self.pull_request(number)

    def update_pull_request(self, number, title, body):
        self.writes.append(("update", number))
        self.prs[number].update(title=title, body=body)
        return self.pull_request(number)

    def pull_request(self, number):
        row = copy.deepcopy(self.prs[number])
        row["head"]["sha"] = self.branch_head(row["head"]["ref"])
        return row

    def checks(self, head):
        return copy.deepcopy(self.check_results)

    def protection(self, branch):
        return {"enforce_admins": {"enabled": self.protected},
                "required_status_checks": {"strict": True, "contexts": ["tests"]}}

    def merge_pull_request(self, number, head, method):
        assert self.pull_request(number)["head"]["sha"] == head
        self.writes.append(("merge", number))
        row = self.prs[number]
        git(self.remote, "update-ref", "refs/heads/main", head)
        row.update(merged=True, state="closed", merge_commit_sha=head)
        return {"merged": True, "sha": head}

    def close_issue(self, number):
        self.writes.append(("close", number))
        self.issues[number]["state"] = "closed"
        if self.fail_close_after_effect:
            self.fail_close_after_effect = False
            raise GitHubError("lost reply", outcome_unknown=True)
        return self.get_issue(number)

    def compare(self, base, head):
        messages = git(self.project, "log", "--reverse", "--format=%B%x00", f"{base}..{head}")
        ancestor = subprocess.run(["git", "-C", str(self.project), "merge-base", "--is-ancestor", base, head]).returncode == 0
        return {"status": "identical" if base == head else "ahead" if ancestor else "diverged",
                "commits": [{"commit": {"message": item.strip()}} for item in messages.split("\x00") if item.strip()]}


class Host:
    def __init__(self, *, decisions=(2,), dependencies=None, fail_gate=False):
        self.decisions, self.dependencies, self.fail_gate = decisions, dependencies or {}, fail_gate
        self.calls = []

    def run(self, request, should_stop):
        assert (request.cwd / "CLAUDE.md").read_text() == "Preserve the public API.\n"
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        self.calls.append(request.phase)
        if request.phase == "discover":
            number = data["issue"]["number"]
            if number in self.decisions and not data.get("operator_decision"):
                return DriverResult(payload={"status": "needs_decision", "summary": "Choose the expected behavior",
                                             "dependencies": [], "candidates": []})
            return DriverResult(payload={"status": "work", "summary": "Reproduce, fix, and test the declared value",
                                         "dependencies": self.dependencies.get(number, []), "candidates": [{
                                             "key": f"issue-{number}", "title": f"Correct value {number}", "kind": "bugfix",
                                             "why": "The documented value is incorrect", "priority": 3,
                                             "evidence": [{"source": "app.py", "observation": "The value needs correction"}],
                                             "acceptance": [f"Behavior for issue {number} works"]}]})
        if request.phase == "implement":
            number = int(data["task"]["key"].split("-")[-1])
            value = -1 if self.fail_gate else number
            text = f"value = {value}\n"
            if "Delivery feedback:" in data["task"]["why"]:
                text += "ci_repair = True\n"
            (request.cwd / "app.py").write_text(text, encoding="utf-8")
            return DriverResult(payload={"completed": True, "summary": "Implemented the correction"})
        return DriverResult(payload={"accepted": True, "summary": "Checked all criteria", "findings": []})


@pytest.fixture
def contained_batch(tmp_path, monkeypatch):
    project, remote = tmp_path / "project", tmp_path / "remote.git"
    project.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.name", "Fixture Maintainer"),
                 ("config", "user.email", "fixture@example.invalid")):
        git(project, *args)
    (project / "app.py").write_text("value = 0\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text("Preserve the public API.\n", encoding="utf-8")
    git(project, "add", "app.py", "CLAUDE.md")
    git(project, "commit", "-m", "Add fixture")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(project, "remote", "add", "origin", str(remote))
    git(project, "push", "origin", "main")
    monkeypatch.setattr(issues, "check_remote", lambda *args: None)  # Only fixture bare repositories, never github.com.
    spec = {"mission": "Implement the captured issue backlog without changing public interfaces", "project": str(project),
            "allowed_paths": ["app.py"],
            "verification": [[sys.executable, "-B", "-c", "import app; assert app.value > 0"]],
            "driver": {"name": "claude"}, "limits": {"max_invocations": 20, "max_wall_seconds": 300},
            "poll_seconds": .05, "max_task_attempts": 1,
            "github": {"repository": "example/project", "delivery": "local"}}
    return project, tmp_path / "state", spec, FakeGitHub(project, remote)


@pytest.fixture
def batch(contained_batch, monkeypatch):
    # Queue policy tests use real synchronous Git and real contained verification.
    # Exercise Git process containment once through the complete merge path;
    # process/engine suites separately test Git cancellation, hooks and deadlines.
    monkeypatch.setattr(issues, "controlled_git", lambda *args: nullcontext())
    monkeypatch.setattr(engine, "controlled_git", lambda *args: nullcontext())
    return contained_batch


def test_local_backlog_skips_decisions_continues_and_preserves_original(batch):
    project, state, spec, github = batch
    original = git(project, "rev-parse", "HEAD")
    issues.initialize(spec, state, github=github)
    host = Host()
    result = issues.run(state, driver=host, github=github)
    assert [row["state"] for row in result["issues"]] == ["accepted_local", "needs_decision", "accepted_local"]
    assert result["status"] == "completed"
    assert result["invocations"] == 7
    assert host.calls == ["discover"] * 3 + ["implement", "review"] * 2
    assert github.writes == []
    assert git(project, "rev-parse", "HEAD") == original
    assert git(project, "status", "--porcelain") == ""
    assert git(project, "show", f"{result['accepted_head']}:app.py") == "value = 3"
    assert all((state / "missions" / path.name).exists() for path in (state / "missions").iterdir())


def test_plan_only_preserves_work_and_global_budget_across_resume(batch):
    _, state, spec, github = batch
    issues.initialize(spec, state, github=github)
    planned = issues.run(state, driver=Host(), github=github, plan_only=True)
    assert planned["invocations"] == 3
    assert not github.writes
    assert {row["state"] for row in planned["issues"]} == {"planned", "needs_decision"}
    result = issues.run(state, driver=Host(), github=github)
    assert result["invocations"] == 7
    assert result["deadline"] == planned["deadline"]


def test_dependency_on_human_decision_does_not_block_unrelated_issue(batch):
    _, state, spec, github = batch
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(dependencies={1: [2]}), github=github)
    assert [row["state"] for row in result["issues"]] == ["waiting_dependency", "needs_decision", "accepted_local"]


def test_dependencies_execute_in_order_even_when_issue_numbers_are_reversed(batch):
    project, state, spec, github = batch
    spec["queue"] = {"numbers": [1, 3]}
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(dependencies={1: [3]}), github=github)
    assert [row["state"] for row in result["issues"]] == ["accepted_local", "accepted_local"]
    assert git(project, "show", f"{result['accepted_head']}:app.py") == "value = 1"


def test_failed_verification_never_publishes_or_closes(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(fail_gate=True), github=github)
    assert result["issues"][0]["state"] == "failed"
    assert github.writes == []


def test_merge_close_after_ci_and_exact_tree_readback(contained_batch):
    project, state, spec, github = contained_batch
    original = git(project, "rev-parse", "HEAD")
    spec["queue"] = {"numbers": [1, 3]}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert [row["state"] for row in result["issues"]] == ["closed", "closed"]
    assert [kind for kind, _ in github.writes] == ["create", "merge", "close", "create", "merge", "close"]
    assert result["issues"][1]["base_sha"] == result["issues"][0]["head"]
    assert git(project, "rev-parse", "HEAD") == original
    assert (project / "app.py").read_text() == "value = 0\n"


def test_pull_request_mode_never_merges_or_closes(batch):
    _, state, spec, github = batch
    spec["github"]["delivery"] = "pull_request"
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["state"] == "pull_request"
    assert github.writes == [("create", 100)]
    assert github.issues[1]["state"] == "open"
    assert result["status"] == "ready"
    assert result["reason"] == "pull_requests_pending"


def test_pr_mode_can_resume_after_maintainer_merges_dependency(batch):
    _, state, spec, github = batch
    spec["github"]["delivery"] = "pull_request"
    spec["queue"] = {"numbers": [1, 3]}
    issues.initialize(spec, state, github=github)
    first = issues.run(state, driver=Host(dependencies={3: [1]}), github=github)
    row = first["issues"][0]
    assert first["issues"][1]["state"] == "waiting_dependency"
    github.merge_pull_request(row["pr_number"], row["head"], "merge")
    second = issues.run(state, driver=Host(), github=github)
    assert second["issues"][0]["state"] == "delivered"
    assert second["issues"][1]["state"] == "pull_request"
    assert second["invocations"] == 6
    assert not any(kind == "close" for kind, _ in github.writes)


def test_missing_strict_protection_never_merges(batch):
    _, state, spec, github = batch
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    spec["queue"] = {"numbers": [1]}
    github.protected = False
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["reason"] == "strict_merge_protection_required"
    assert github.writes == [("create", 100)]


def test_unknown_closure_is_read_reconciled_without_duplicate_mutation(batch):
    _, state, spec, github = batch
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    spec["queue"] = {"numbers": [1]}
    github.fail_close_after_effect = True
    issues.initialize(spec, state, github=github)
    with pytest.raises(GitHubError):
        issues.run(state, driver=Host(), github=github)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["issues"][0]["state"] == "closed"
    assert [kind for kind, _ in github.writes].count("close") == 1


def test_changed_issue_is_retained_for_decision(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    github.issues[1]["body"] = "A new requirement"
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["reason"] == "issue_changed_since_snapshot"
    assert result["invocations"] == 0


def test_global_budget_does_not_reset_per_issue(batch):
    _, state, spec, github = batch
    spec["limits"]["max_invocations"] = 4
    issues.initialize(spec, state, github=github)
    host = Host()
    result = issues.run(state, driver=host, github=github)
    assert result["status"] == "limited"
    assert result["reason"] == "insufficient_task_budget"
    assert result["invocations"] == 3
    assert host.calls == ["discover"] * 3


@pytest.mark.parametrize("text", ["Closes #999", "CLOSES: #999", "Fixes example/other#22", "Implemented by Claude",
                                  "**Fixes** [#999](https://github.com/example/project/issues/999)", "Fixes:#999"])
def test_publication_rejects_automatic_closing_and_authorship(batch, text):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    issues.run(state, driver=Host(), github=github, plan_only=True)
    row = issues.status(state)["issues"][0]
    row["plan"]["candidates"][0]["acceptance"].append(text)
    IssueStore(state).update_issue(1, "planned", plan=row["plan"])
    result = issues.run(state, driver=Host(), github=github)
    runner = issues.IssueRunner(IssueStore(state), Host(), threading.Event(), github)
    with pytest.raises(ValueError, match="Publication metadata"):
        runner.metadata(result["issues"][0])
    assert not github.writes


def test_operator_decision_requeues_only_selected_issue(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [2]}
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    issues.resolve(state, 2, "Use the existing public behavior as the acceptance contract", github=github)
    resolved = issues.run(state, driver=Host(), github=github)
    assert resolved["issues"][0]["state"] == "accepted_local"
    assert resolved["deadline"] == result["deadline"]
    assert resolved["invocations"] == 4


def test_changed_requirements_can_be_explicitly_reapproved(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    issues.initialize(spec, state, github=github)
    github.issues[1]["body"] = "The confirmed updated requirement"
    assert issues.run(state, driver=Host(), github=github)["issues"][0]["state"] == "needs_decision"
    issues.resolve(state, 1, "Implement the updated issue requirements", github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["state"] == "accepted_local"
    assert result["issues"][0]["source"]["body"] == github.issues[1]["body"]


def test_planning_stop_returns_without_exception_or_implementation(batch):
    _, state, spec, github = batch
    entered = threading.Event()
    results, errors = [], []

    class WaitingHost:
        def run(self, request, should_stop):
            assert request.phase == "discover"
            entered.set()
            while not should_stop():
                entered.wait(.01)
            return DriverResult(cancelled=True)

    issues.initialize(spec, state, github=github)

    def work():
        try:
            results.append(issues.run(state, driver=WaitingHost(), github=github))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    assert entered.wait(10)
    issues.stop(state)
    thread.join(10)
    assert not thread.is_alive()
    assert not errors
    assert results[0]["status"] == "stopped"
    assert results[0]["invocations"] == 1
    assert not github.writes


def test_signal_during_child_implementation_stops_entire_batch(batch):
    _, state, spec, github = batch

    class SignalledHost(Host):
        def run(self, request, should_stop):
            result = super().run(request, should_stop)
            if request.phase == "implement":
                signal.raise_signal(signal.SIGINT)
            return result

    issues.initialize(spec, state, github=github)
    host = SignalledHost(decisions=())
    result = issues.run(state, driver=host, github=github)
    assert result["status"] == "stopped"
    assert result["stop_requested"]
    assert host.calls == ["discover"] * 3 + ["implement"]
    assert result["issues"][0]["state"] == "interrupted"
    assert not github.writes


def test_cleanup_unknown_during_planning_preserves_safety_block(batch):
    _, state, spec, github = batch

    class UnknownHost:
        def run(self, request, should_stop):
            return DriverResult(cleanup_confirmed=False)

    issues.initialize(spec, state, github=github)
    with pytest.raises(RuntimeError):
        issues.run(state, driver=UnknownHost(), github=github)
    result = issues.status(state)
    assert result["status"] == "blocked"
    assert result["reason"] == "worker_cleanup_unconfirmed"
    assert not result["stop_requested"]
    assert result["calls"][0]["state"] == "unknown"


def test_one_call_planning_limit_is_not_mislabeled_as_operator_stop(batch):
    _, state, spec, github = batch
    spec["limits"]["max_invocations"] = 1
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["status"] == "limited"
    assert not result["stop_requested"]
    assert result["invocations"] == 1


def test_task_retry_exhaustion_does_not_stop_unrelated_issues(batch):
    _, state, spec, github = batch
    spec["max_failures"] = 1
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(fail_gate=True), github=github)
    assert [row["state"] for row in result["issues"]] == ["failed", "needs_decision", "failed"]
    assert result["status"] == "completed"


def test_ci_failure_repairs_same_pr_and_updates_inspected_metadata(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    old_checks = github.checks
    first_head = []

    def checks(head):
        if not first_head:
            first_head.append(head)
        if head == first_head[0]:
            return [{"name": "tests", "status": "completed", "conclusion": "failure"}]
        return old_checks(head)

    github.checks = checks
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["state"] == "closed"
    assert result["issues"][0]["delivery_attempts"] == 2
    assert [kind for kind, _ in github.writes] == ["create", "update", "merge", "close"]


def test_ci_retry_limit_retains_issue_and_pr(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1], "max_delivery_attempts": 1}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    github.check_results[0]["conclusion"] = "failure"
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["reason"] == "ci_retry_limit"
    assert github.writes == [("create", 100)]


def test_conflicting_required_check_context_cannot_pass(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1], "max_delivery_attempts": 1}
    spec["github"].update(delivery="merge", close_issues=True, required_checks=["tests"])
    github.check_results.append({"name": "tests", "status": "completed", "conclusion": "failure"})
    issues.initialize(spec, state, github=github)
    result = issues.run(state, driver=Host(), github=github)
    assert result["issues"][0]["state"] == "failed"
    assert github.writes == [("create", 100)]


def test_batch_size_resume_keeps_budget_and_deadline(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1, 3], "batch_size": 1}
    issues.initialize(spec, state, github=github)
    first = issues.run(state, driver=Host(), github=github)
    assert first["status"] == "ready"
    assert first["reason"] == "batch_size_reached"
    assert first["invocations"] == 4
    second = issues.run(state, driver=Host(), github=github)
    assert second["deadline"] == first["deadline"]
    assert second["invocations"] == 6
    assert all(row["state"] == "accepted_local" for row in second["issues"])
