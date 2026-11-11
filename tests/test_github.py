"""Offline GitHub protocol tests. No credentials or network are used."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from dutyloop import github
from dutyloop.github import GitHub, GitHubError
from dutyloop.process import ProcessResult

SHA = "a" * 40


def issue(number=1, **fields):
    return {"id": number, "number": number, "title": f"Issue {number}", "body": "Acceptance criteria",
            "html_url": f"https://github.com/acme/project/issues/{number}", "state": "open",
            "updated_at": "2026-11-10T00:00:00Z", "labels": [{"name": "bug"}], "comments": 0, **fields}


@pytest.fixture
def client(tmp_path):
    return GitHub("acme/project", cwd=tmp_path, output_dir=tmp_path / "capture", timeout_seconds=2)


def install_runner(monkeypatch, handler):
    calls = []

    def run(argv, **options):
        calls.append((argv, options))
        result = handler(argv, options)
        if not isinstance(result, ProcessResult):
            result = ProcessResult(returncode=0, stdout=json.dumps(result))
        directory = options["output_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stdout.log").write_text(result.stdout, encoding="utf-8")
        (directory / "stderr.log").write_text(result.stderr, encoding="utf-8")
        return result

    monkeypatch.setattr(github, "run_process", run)
    return calls


def test_repository_scopes_host_and_keeps_credentials_out_of_arguments(client, monkeypatch):
    calls = install_runner(monkeypatch, lambda argv, options: {"full_name": "acme/project", "default_branch": "main"})
    assert client.repository()["default_branch"] == "main"
    argv, options = calls[0]
    assert argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
    assert argv[-1] == "repos/acme/project"
    assert options["timeout_seconds"] == 2
    assert options["max_output_bytes"] == 1024 * 1024
    assert options["input_text"] == ""
    assert options["env"]["GH_DEBUG"] == ""
    assert not any("token" in str(value).lower() for value in [argv, options])
    assert not list(client.output_dir.iterdir())


def test_all_issue_and_comment_pages_are_loaded_and_pulls_excluded(client, monkeypatch):
    def response(argv, options):
        url = urlsplit(argv[-1])
        page = int(parse_qs(url.query)["page"][0])
        if url.path.endswith("/issues"):
            assert parse_qs(url.query)["state"] == ["open"]
            if page == 1:
                return [issue(1, comments=101)] + [issue(n, pull_request={}) for n in range(2, 101)]
            return [issue(101, body=None, labels=["documentation"])]
        if url.path.endswith("/1/comments"):
            if page == 1:
                return [{"id": n, "body": f"Comment {n}"} for n in range(1, 101)]
            return [{"id": 101, "body": "Final acceptance detail"}]
        assert url.path.endswith("/101/comments")
        return []

    calls = install_runner(monkeypatch, response)
    issues = client.list_open_issues()
    assert [entry["number"] for entry in issues] == [1, 101]
    assert len(issues[0]["comments"]) == 101
    assert issues[0]["comments"][-1]["body"] == "Final acceptance detail"
    assert issues[0]["labels"] == ["bug"]
    assert issues[1]["body"] == "" and issues[1]["labels"] == ["documentation"]
    assert len(calls) == 5


def test_issue_comments_are_read_even_if_count_is_stale(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: [{"id": 1, "body": "New decision"}]
                   if "/comments?" in argv[-1] else issue())
    assert client.get_issue(1)["comments"][0]["body"] == "New decision"


def test_missing_comments_refuses_partial_issue(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: [] if "/comments?" in argv[-1] else issue(comments=1))
    with pytest.raises(GitHubError, match="comments were incomplete"):
        client.get_issue(1)


def test_issue_endpoint_refuses_pull_requests(client, monkeypatch):
    calls = install_runner(monkeypatch, lambda argv, options: issue(pull_request={}))
    with pytest.raises(GitHubError, match="pull request"):
        client.get_issue(1)
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["", "https://github.com/acme/project", "acme/project/other", "../project",
                                  "acme/..", "acme/project?state=open", "acme/project\n", "-owner/project"])
def test_repository_validation(value, tmp_path):
    with pytest.raises(ValueError):
        GitHub(value, cwd=tmp_path, output_dir=tmp_path / "output")


@pytest.mark.parametrize("branch", ["-main", "../main", "main?x=y", "feat:thing", "feature//a", "@",
                                   "refs/heads/.hidden", "main.lock", "main\n", "foo@{bar"])
def test_branch_validation_refuses_dispatch(client, monkeypatch, branch):
    monkeypatch.setattr(github, "run_process", lambda *args, **kwargs: pytest.fail("Invalid branch dispatched"))
    with pytest.raises(ValueError):
        client.branch_head(branch)


@pytest.mark.parametrize("number", [0, -1, True, "1", 1.5])
def test_issue_number_validation_refuses_dispatch(client, monkeypatch, number):
    monkeypatch.setattr(github, "run_process", lambda *args, **kwargs: pytest.fail("Invalid number dispatched"))
    with pytest.raises(ValueError):
        client.get_issue(number)


def test_checks_combine_runs_with_latest_commit_statuses_and_paginate(client, monkeypatch):
    def response(argv, options):
        url = urlsplit(argv[-1])
        page = int(parse_qs(url.query)["page"][0])
        if url.path.endswith("/check-runs"):
            assert parse_qs(url.query)["filter"] == ["latest"]
            runs = [{"id": n, "name": f"Job {n}", "status": "completed", "conclusion": "success", "head_sha": SHA}
                    for n in range(1, 101)] if page == 1 else [
                        {"id": 101, "name": "Windows", "status": "queued", "conclusion": None}]
            return {"total_count": 101, "check_runs": runs}
        assert url.path.endswith("/statuses")
        return [{"id": 6, "context": "external", "state": "pending"},
                {"id": 5, "context": "external", "state": "failure"},
                {"id": 4, "context": "lint", "state": "success"},
                {"id": 3, "context": "security", "state": "error"}]

    calls = install_runner(monkeypatch, response)
    checks = client.checks(SHA)
    assert len(checks) == 104
    assert checks[-3:] == [{"name": "external", "status": "in_progress", "conclusion": None},
                           {"name": "lint", "status": "completed", "conclusion": "success"},
                           {"name": "security", "status": "completed", "conclusion": "failure"}]
    assert len(calls) == 3


def test_empty_checks_are_not_claimed_successful(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: {"total_count": 0, "check_runs": []}
                   if "/check-runs?" in argv[-1] else [])
    assert client.checks(SHA) == []


def test_reported_check_count_refuses_missing_page(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: {"total_count": 2, "check_runs": [
        {"id": 1, "name": "test", "status": "completed", "conclusion": "success"}]})
    with pytest.raises(GitHubError, match="incomplete"):
        client.checks(SHA)


def test_pagination_limit_refuses_partial_results(client, monkeypatch):
    monkeypatch.setattr(github, "_MAX_PAGES", 2)
    install_runner(monkeypatch, lambda argv, options: [issue(n) for n in range(
        int(parse_qs(urlsplit(argv[-1]).query)["page"][0]) * 100,
        int(parse_qs(urlsplit(argv[-1]).query)["page"][0]) * 100 + 100)])
    with pytest.raises(GitHubError, match="pagination limit"):
        client.list_open_issues()


def test_repeated_pages_are_not_silently_accepted(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: [issue(n) for n in range(1, 101)])
    with pytest.raises(GitHubError, match="repeated"):
        client.list_open_issues()


def test_find_pull_requires_exact_owned_head_and_base(client, monkeypatch):
    def pull(number, *, head="fix/issue-1", base="main", repo="acme/project"):
        return {"id": number, "number": number, "head": {"ref": head, "repo": {"full_name": repo}},
                "base": {"ref": base, "repo": {"full_name": "acme/project"}}}

    calls = install_runner(monkeypatch, lambda argv, options: [pull(1, repo="fork/project"),
                                                             pull(2, base="release"), pull(3)])
    assert client.find_pull_request("fix/issue-1", "main")["number"] == 3
    query = parse_qs(urlsplit(calls[0][0][-1]).query)
    assert query["head"] == ["acme:fix/issue-1"] and query["state"] == ["all"]


def test_mutation_payloads_use_stdin_and_bind_merge_sha(client, monkeypatch):
    calls = install_runner(monkeypatch, lambda argv, options: {"number": 7, "merged": True})
    client.create_pull_request("fix/issue-1", "main", "Fix import", "Details\n\nRefs #1")
    client.merge_pull_request(7, SHA, "squash")
    client.close_issue(1)
    assert [argv[5] for argv, options in calls] == ["POST", "PUT", "PATCH"]
    assert json.loads(calls[0][1]["input_text"]) == {
        "head": "fix/issue-1", "base": "main", "title": "Fix import", "body": "Details\n\nRefs #1"}
    assert json.loads(calls[1][1]["input_text"]) == {"sha": SHA, "merge_method": "squash"}
    assert json.loads(calls[2][1]["input_text"]) == {"state": "closed", "state_reason": "completed"}
    assert all("--input" in argv and "Details" not in " ".join(argv) for argv, options in calls)


def test_invalid_merge_sha_never_dispatches(client, monkeypatch):
    monkeypatch.setattr(github, "run_process", lambda *args, **kwargs: pytest.fail("Invalid merge dispatched"))
    with pytest.raises(ValueError):
        client.merge_pull_request(1, "main")


def test_branch_and_compare_encode_refs_without_changing_repo(client, monkeypatch):
    calls = install_runner(monkeypatch, lambda argv, options: {"object": {"type": "commit", "sha": SHA}}
                           if "/git/ref/" in argv[-1] else {"status": "ahead"})
    assert client.branch_head("feature/issue-1") == SHA
    assert client.compare("main", "feature/issue-1")["status"] == "ahead"
    assert calls[0][0][-1] == "repos/acme/project/git/ref/heads/feature/issue-1"
    assert calls[1][0][-1] == "repos/acme/project/compare/main...feature%2Fissue-1"


@pytest.mark.parametrize("result, reason", [
    (ProcessResult(returncode=0, cancelled=True), "cancelled"),
    (ProcessResult(returncode=0, timed_out=True), "timed out"),
    (ProcessResult(returncode=0, cleanup_confirmed=False), "cleanup"),
    (ProcessResult(returncode=0, output_truncated=True), "capture limit"),
    (ProcessResult(returncode=1, stderr="secret=ghp_private credential"), "request failed"),
    (ProcessResult(returncode=0, stdout="private malformed token"), "malformed JSON"),
    (ProcessResult(returncode=0, stdout="[]"), "mutation response"),
])
def test_failed_mutations_are_unknown_and_errors_do_not_echo_private_output(client, monkeypatch, result, reason):
    install_runner(monkeypatch, lambda argv, options: result)
    with pytest.raises(GitHubError, match=reason) as caught:
        client.close_issue(1)
    assert caught.value.outcome_unknown
    assert caught.value.cleanup_confirmed == result.cleanup_confirmed
    assert "private" not in str(caught.value) and "ghp_" not in str(caught.value)
    logs = list(client.output_dir.rglob("*.log"))
    assert bool(logs) is not result.cleanup_confirmed


def test_cancellation_before_dispatch_has_no_remote_uncertainty(client, monkeypatch):
    client.should_stop = lambda: True
    monkeypatch.setattr(github, "run_process", lambda *args, **kwargs: pytest.fail("Stopped operation dispatched"))
    with pytest.raises(GitHubError) as caught:
        client.close_issue(1)
    assert caught.value.cancelled and not caught.value.outcome_unknown


def test_stop_callback_is_forwarded_and_process_exceptions_are_sanitized(client, monkeypatch):
    def run(argv, **options):
        assert options["should_stop"] is client.should_stop
        raise OSError("private credential ghp_secret")

    monkeypatch.setattr(github, "run_process", run)
    with pytest.raises(GitHubError) as caught:
        client.create_pull_request("fix/1", "main", "Fix", "Description")
    assert caught.value.outcome_unknown and not caught.value.cleanup_confirmed
    assert "private" not in str(caught.value) and caught.value.__cause__ is None


def test_repository_identity_mismatch_is_refused(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: {"full_name": "another/repo", "default_branch": "main"})
    with pytest.raises(GitHubError, match="identity"):
        client.repository()


def test_protection_is_repo_scoped_get_and_preserves_server_rules(client, monkeypatch):
    rules = {"enforce_admins": {"enabled": True}, "required_status_checks": {
        "strict": True, "contexts": ["test"], "checks": [{"context": "test", "app_id": 42}]}}
    calls = install_runner(monkeypatch, lambda argv, options: rules)
    assert client.protection("release/stable") == rules
    argv, options = calls[0]
    assert argv[5] == "GET"
    assert argv[-1] == "repos/acme/project/branches/release%2Fstable/protection"
    assert "--input" not in argv and options["input_text"] == ""


def test_missing_or_inaccessible_protection_fails_closed(client, monkeypatch):
    install_runner(monkeypatch, lambda argv, options: ProcessResult(returncode=1, stderr="HTTP 404 private data"))
    with pytest.raises(GitHubError) as caught:
        client.protection("main")
    assert not caught.value.outcome_unknown
    assert "private" not in str(caught.value)


def test_protection_ref_validation_prevents_dispatch(client, monkeypatch):
    monkeypatch.setattr(github, "run_process", lambda *args, **kwargs: pytest.fail("Invalid ref dispatched"))
    with pytest.raises(ValueError):
        client.protection("main?query=other")


def test_update_pull_request_patches_only_explicit_metadata_via_stdin(client, monkeypatch):
    fields = {"title": "fix: Repair import", "body": "Checks passed\nVerified tree: abc"}
    calls = install_runner(monkeypatch, lambda argv, options: {"number": 12, **fields})
    assert client.update_pull_request(12, fields["title"], fields["body"])["body"] == fields["body"]
    argv, options = calls[0]
    assert argv[5] == "PATCH" and argv[-1] == "repos/acme/project/pulls/12"
    assert json.loads(options["input_text"]) == fields
    assert "--input" in argv and fields["body"] not in argv


@pytest.mark.parametrize("number,title,body", [(True, "Title", "Body"), (1, " ", "Body"),
                                              (1, None, "Body"), (1, "Title", None)])
def test_update_pull_request_rejects_invalid_metadata_before_dispatch(client, monkeypatch, number, title, body):
    monkeypatch.setattr(github, "run_process", lambda *args, **kwargs: pytest.fail("Invalid PR update dispatched"))
    with pytest.raises(ValueError):
        client.update_pull_request(number, title, body)


def test_pull_feedback_loads_review_inline_and_conversation_pages_read_only(client, monkeypatch):
    def response(argv, options):
        path = urlsplit(argv[-1]).path
        page = int(parse_qs(urlsplit(argv[-1]).query)["page"][0])
        author = {"user": {"login": "maintainer"}, "author_association": "MEMBER"}
        if path.endswith("/reviews"):
            if page == 1:
                return [{"id": n, "body": f"Review {n}", "state": "COMMENTED", "commit_id": SHA,
                         "submitted_at": "2026-11-10T01:00:00Z", **author} for n in range(1, 101)]
            return [{"id": 101, "body": "Please handle the edge case", "state": "CHANGES_REQUESTED",
                     "commit_id": SHA, "submitted_at": "2026-11-10T02:00:00Z", **author}]
        if "/pulls/" in path:
            return [{"id": 9, "body": "Inline detail", "commit_id": SHA, "path": "app.py", "line": 7,
                     "updated_at": "2026-11-10T02:00:00Z", **author}]
        return [{"id": 7, "body": "Conversation detail", "updated_at": "2026-11-10T03:00:00Z", **author}]

    calls = install_runner(monkeypatch, response)
    feedback = client.pull_request_feedback(12)
    assert len(feedback) == 103
    assert feedback[100]["kind"] == "review" and feedback[100]["state"] == "CHANGES_REQUESTED"
    assert feedback[101]["kind"] == "inline" and feedback[101]["path"] == "app.py"
    assert feedback[101]["line"] == 7
    assert feedback[-1]["kind"] == "conversation" and feedback[-1]["commit_id"] is None
    assert feedback[-1]["author"] == "maintainer"
    assert all(argv[5] == "GET" and options["input_text"] == "" for argv, options in calls)
    assert [urlsplit(argv[-1]).path for argv, _ in calls] == [
        "repos/acme/project/pulls/12/reviews", "repos/acme/project/pulls/12/reviews",
        "repos/acme/project/pulls/12/comments", "repos/acme/project/issues/12/comments"]


@pytest.mark.parametrize("field,value", [("id", True), ("id", None), ("body", 1), ("user", None),
                                        ("commit_id", "main"), ("state", "UNKNOWN")])
def test_malformed_review_feedback_is_refused_before_partial_data(client, monkeypatch, field, value):
    row = {"id": 1, "body": "Review", "state": "COMMENTED", "commit_id": SHA,
           "submitted_at": "2026-11-10T01:00:00Z", "user": {"login": "maintainer"},
           "author_association": "MEMBER", field: value}
    install_runner(monkeypatch, lambda argv, options: [row])
    with pytest.raises(GitHubError, match="feedback|pagination"):
        client.pull_request_feedback(12)
