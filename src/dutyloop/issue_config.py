"""Operator-owned authority for a finite GitHub issue backlog."""

import re
import subprocess
from urllib.parse import urlparse

from .config import _number, _unknown, validate_spec
from .schemas import DISCOVERY_SCHEMA, _object


ISSUE_PLAN_SCHEMA = _object({
    "status": {"type": "string", "enum": ["work", "needs_decision"]},
    "summary": {"type": "string", "minLength": 1},
    "dependencies": {"type": "array", "items": {"type": "integer", "minimum": 1}},
    "candidates": {"type": "array", "maxItems": 1,
                   "items": DISCOVERY_SCHEMA["properties"]["candidates"]["items"]},
})

PR_FEEDBACK_SCHEMA = _object({
    "status": {"type": "string", "enum": ["work", "no_work", "needs_decision"]},
    "summary": {"type": "string", "minLength": 1},
})


def branch_name(value):
    if (not isinstance(value, str) or not value or value.startswith("-")
            or re.match(r"(?i)(codex|claude|chatgpt|copilot)/", value)
            or subprocess.run(["git", "check-ref-format", "--branch", value],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=10).returncode):
        raise ValueError("Expected a functional Git branch name")
    return value


def validate_issue_spec(spec, state_dir):
    if not isinstance(spec, dict):
        raise ValueError("Issue configuration must be an object")
    core = {key: value for key, value in spec.items() if key not in {"github", "queue"}}
    normalized = validate_spec(core, state_dir)
    if normalized["branch"] is not None:
        raise ValueError("Issue branches are allocated per issue; omit branch")
    github = spec.get("github")
    _unknown(github, {"repository", "remote", "base_branch", "delivery", "close_issues",
                      "required_checks", "merge_method", "maintain_pull_requests", "review_authors"}, "github")
    repository = github.get("repository")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("github.repository must be owner/name")
    if any(part in {".", ".."} for part in repository.split("/")):
        raise ValueError("Invalid GitHub repository")
    remote = github.get("remote", "origin")
    if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
        raise ValueError("github.remote must be a named Git remote")
    base = branch_name(github.get("base_branch", "main"))
    delivery = github.get("delivery", "local")
    if delivery not in {"local", "pull_request", "merge"}:
        raise ValueError("github.delivery must be local, pull_request or merge")
    close = github.get("close_issues", False)
    if type(close) is not bool or close and delivery != "merge":
        raise ValueError("close_issues requires merge delivery and must be boolean")
    checks = github.get("required_checks", [])
    if not isinstance(checks, list) or any(not isinstance(name, str) or not name.strip() for name in checks):
        raise ValueError("required_checks must be check names")
    if delivery == "merge" and not checks:
        raise ValueError("merge delivery requires explicit required_checks")
    method = github.get("merge_method", "merge")
    if method not in {"merge", "squash", "rebase"}:
        raise ValueError("Invalid merge_method")
    maintain = github.get("maintain_pull_requests", True)
    if type(maintain) is not bool:
        raise ValueError("github.maintain_pull_requests must be boolean")
    authors = github.get("review_authors", [])
    if not isinstance(authors, list) or any(
            not isinstance(author, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", author)
            for author in authors):
        raise ValueError("github.review_authors must be GitHub logins")
    queue = spec.get("queue", {})
    _unknown(queue, {"numbers", "exclude_labels", "batch_size", "max_issues", "max_delivery_attempts"}, "queue")
    numbers = queue.get("numbers", [])
    if not isinstance(numbers, list) or any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError("queue.numbers must be unique positive issue numbers")
    labels = queue.get("exclude_labels", ["needs-decision", "needs-human"])
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("exclude_labels must be label names")
    normalized["github"] = {"repository": repository, "remote": remote, "base_branch": base,
                            "delivery": delivery, "close_issues": close,
                            "required_checks": list(dict.fromkeys(checks)), "merge_method": method,
                            "maintain_pull_requests": maintain,
                            "review_authors": list(dict.fromkeys(author.casefold() for author in authors))}
    normalized["queue"] = {
        "numbers": numbers, "exclude_labels": labels,
        "batch_size": _number(queue.get("batch_size", 10), "batch_size", 1, True),
        "max_issues": _number(queue.get("max_issues", 500), "max_issues", 1, True),
        "max_delivery_attempts": _number(queue.get("max_delivery_attempts", 2), "max_delivery_attempts", 1, True),
    }
    return normalized


def check_remote(project, github, git):
    """Bind both fetch and push destinations to the configured GitHub repository."""
    for options in (("get-url",), ("get-url", "--push")):
        value = git(project, "remote", *options, "--all", github["remote"]).splitlines()
        if len(value) != 1:
            raise ValueError("Issue delivery needs exactly one fetch and push URL")
        remote = value[0]
        if remote.startswith("git@github.com:"):
            name = remote[len("git@github.com:"):]
        else:
            parsed = urlparse(remote)
            if (parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com"
                    or parsed.password or parsed.query or parsed.fragment or parsed.port
                    or parsed.username not in (None, "git")):
                raise ValueError("Remote must identify the configured github.com repository without credentials")
            name = parsed.path.lstrip("/")
        if name.endswith(".git"):
            name = name[:-4]
        if name.casefold() != github["repository"].casefold():
            raise ValueError("Git remote and github.repository differ")
