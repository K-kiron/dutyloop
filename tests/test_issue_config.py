import copy
import subprocess
import sys

import pytest

from dutyloop.issue_config import check_remote, validate_issue_spec
from dutyloop.workspace import git


@pytest.fixture
def issue_spec(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 0\n", encoding="utf-8")
    for args in (["init", "-b", "main"], ["add", "app.py"],
                 ["-c", "user.name=Fixture Maintainer", "-c", "user.email=maintainer@example.invalid",
                  "commit", "-m", "Add fixture"],
                 ["remote", "add", "origin", "https://github.com/example/app.git"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    return {"mission": "Resolve the captured issues", "project": str(project),
            "allowed_paths": ["app.py"], "verification": [[sys.executable, "-B", "-c", "import app"]],
            "limits": {"max_invocations": 20, "max_wall_seconds": 120},
            "github": {"repository": "example/app"}}, tmp_path / "state"


def test_issue_config_defaults_grant_no_publication_authority(issue_spec):
    spec, state = issue_spec
    normalized = validate_issue_spec(spec, state)
    assert normalized["github"]["delivery"] == "local"
    assert normalized["github"]["close_issues"] is False
    assert normalized["github"]["required_checks"] == []
    assert normalized["github"]["maintain_pull_requests"] is True
    assert normalized["github"]["review_authors"] == []
    assert normalized["queue"]["numbers"] == []
    assert normalized["queue"]["exclude_labels"] == ["needs-decision", "needs-human"]
    assert not state.exists()


@pytest.mark.parametrize("value", ["true", 1, None])
def test_pr_maintenance_requires_boolean_authority(issue_spec, value):
    spec, state = issue_spec
    spec["github"]["maintain_pull_requests"] = value
    with pytest.raises(ValueError, match="maintain_pull_requests"):
        validate_issue_spec(spec, state)


@pytest.mark.parametrize("value", ["owner", ["-user"], ["user name"], [True]])
def test_review_authors_are_explicit_github_logins(issue_spec, value):
    spec, state = issue_spec
    spec["github"]["review_authors"] = value
    with pytest.raises(ValueError, match="review_authors"):
        validate_issue_spec(spec, state)


def test_review_author_allowlist_is_case_insensitive_and_deduplicated(issue_spec):
    spec, state = issue_spec
    spec["github"].update(maintain_pull_requests=False, review_authors=["Maintainer", "maintainer", "outside-user"])
    normalized = validate_issue_spec(spec, state)
    assert normalized["github"]["maintain_pull_requests"] is False
    assert normalized["github"]["review_authors"] == ["maintainer", "outside-user"]


@pytest.mark.parametrize("section,field", [(None, "auto_publish"), ("github", "token"), ("queue", "auto_close")])
def test_issue_config_unknown_fields_fail_closed(issue_spec, section, field):
    spec, state = issue_spec
    if section is None:
        spec[field] = True
    else:
        spec.setdefault(section, {})[field] = True
    with pytest.raises(ValueError, match="Unknown"):
        validate_issue_spec(spec, state)


@pytest.mark.parametrize("delivery", ["local", "pull_request"])
def test_issue_closure_requires_explicit_merge_delivery(issue_spec, delivery):
    spec, state = issue_spec
    spec["github"].update(delivery=delivery, close_issues=True)
    with pytest.raises(ValueError, match="close_issues requires merge"):
        validate_issue_spec(spec, state)


def test_issue_merge_requires_explicit_named_ci_checks(issue_spec):
    spec, state = issue_spec
    spec["github"].update(delivery="merge", close_issues=True)
    with pytest.raises(ValueError, match="requires explicit required_checks"):
        validate_issue_spec(spec, state)
    spec["github"]["required_checks"] = ["unit", "integration", "unit"]
    normalized = validate_issue_spec(spec, state)
    assert normalized["github"]["required_checks"] == ["unit", "integration"]
    assert normalized["github"]["close_issues"] is True


@pytest.mark.parametrize("branch", ["../main", "main:other", "--help", "feature space", "Claude/issues"])
def test_issue_base_branch_rejects_unsafe_or_branded_names(issue_spec, branch):
    spec, state = issue_spec
    spec["github"]["base_branch"] = branch
    with pytest.raises(ValueError, match="functional Git branch"):
        validate_issue_spec(spec, state)


def test_issue_config_rejects_user_chosen_shared_branch(issue_spec):
    spec, state = issue_spec
    spec["branch"] = "fix/all-issues"
    with pytest.raises(ValueError, match="allocated per issue"):
        validate_issue_spec(spec, state)


@pytest.mark.parametrize("numbers", [[1, 1], [True], [0], ["12"]])
def test_issue_snapshot_selection_is_unambiguous(issue_spec, numbers):
    spec, state = issue_spec
    spec["queue"] = {"numbers": numbers}
    with pytest.raises(ValueError, match="unique positive"):
        validate_issue_spec(spec, state)


@pytest.mark.parametrize("remote", ["--upload-pack=evil", "origin/other", "origin other"])
def test_issue_remote_must_be_a_named_remote(issue_spec, remote):
    spec, state = issue_spec
    spec["github"]["remote"] = remote
    with pytest.raises(ValueError, match="named Git remote"):
        validate_issue_spec(spec, state)


@pytest.mark.parametrize("url", ["https://github.com/example/app.git", "git@github.com:example/app.git",
                                 "ssh://git@github.com/example/app.git"])
def test_issue_remote_accepts_matching_github_fetch_and_push_destinations(issue_spec, url):
    spec, state = issue_spec
    project = spec["project"]
    git(project, "remote", "set-url", "origin", url)
    check_remote(project, validate_issue_spec(spec, state)["github"], git)


@pytest.mark.parametrize("url", ["https://github.com/other/app.git", "https://evil.example/example/app.git",
                                 "https://secret@github.com/example/app.git", "https://github.com:443/example/app.git",
                                 "https://github.com/example/app.git?token=secret", "file:///tmp/app.git"])
def test_issue_remote_rejects_wrong_repository_credentials_or_host(issue_spec, url):
    spec, state = issue_spec
    git(spec["project"], "remote", "set-url", "origin", url)
    with pytest.raises(ValueError):
        check_remote(spec["project"], validate_issue_spec(spec, state)["github"], git)


def test_issue_remote_validates_push_url_separately_from_fetch_url(issue_spec):
    spec, state = issue_spec
    git(spec["project"], "remote", "set-url", "--push", "origin", "git@github.com:other/app.git")
    with pytest.raises(ValueError, match="differ"):
        check_remote(spec["project"], validate_issue_spec(spec, state)["github"], git)


@pytest.mark.parametrize("push", [False, True])
def test_issue_remote_rejects_multiple_fetch_or_push_urls(issue_spec, push):
    spec, state = issue_spec
    options = ["--push"] if push else []
    git(spec["project"], "remote", "set-url", "--add", *options, "origin", "https://github.com/example/app.git")
    if push:
        git(spec["project"], "remote", "set-url", "--add", "--push", "origin", "git@github.com:example/app.git")
    with pytest.raises(ValueError, match="exactly one fetch and push URL"):
        check_remote(spec["project"], validate_issue_spec(copy.deepcopy(spec), state)["github"], git)
