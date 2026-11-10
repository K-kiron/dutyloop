"""Repository-scoped GitHub REST transport using the operator's existing gh login.

No credential is read or supplied by DutyLoop. Requests have bounded capture,
pagination and deadlines; an interrupted mutation must be reconciled by reads
before retrying. These methods do not grant publication or merge authority.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
from typing import Callable
from urllib.parse import quote, urlencode
import uuid

from .process import run_process

_PAGE_SIZE = 100
_MAX_PAGES = 100
_MAX_OUTPUT_BYTES = 1024 * 1024
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class GitHubError(RuntimeError):
    """Safe transport error; remote mutation outcome may need reconciliation."""

    def __init__(self, message, *, outcome_unknown=False, cleanup_confirmed=True,
                 cancelled=False, timed_out=False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown
        self.cleanup_confirmed = cleanup_confirmed
        self.cancelled = cancelled
        self.timed_out = timed_out


def _number(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("GitHub issue and pull request numbers must be positive integers")
    return str(value)


def _branch(value):
    if (not isinstance(value, str) or not value or value == "@" or value.startswith(("-", "/"))
            or value.endswith(("/", ".")) or any(part.startswith(".") or part.endswith(".lock")
                                                  for part in value.split("/"))
            or any(fragment in value for fragment in ("..", "@{", "//"))
            or any(ord(char) < 33 or ord(char) == 127 or char in "~^:?*[\\" for char in value)):
        raise ValueError("Invalid GitHub branch name")
    return value


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("GitHub commit references must be full hexadecimal SHAs")
    return value.lower()


def _object(value):
    if not isinstance(value, dict):
        raise GitHubError("GitHub returned an invalid response object")
    return value


class GitHub:
    """GitHub.com operations limited to one explicit owner/repository."""

    def __init__(self, repository: str, *, cwd: Path, output_dir: Path,
                 timeout_seconds: float = 30, should_stop: Callable[[], bool] = lambda: False):
        parts = repository.split("/") if isinstance(repository, str) else []
        if (len(parts) != 2 or not _OWNER.fullmatch(parts[0]) or not _REPO.fullmatch(parts[1])
                or parts[1] in {".", ".."}):
            raise ValueError("GitHub repository must be owner/name")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("GitHub timeout must be a positive finite number")
        if not callable(should_stop):
            raise ValueError("GitHub stop callback must be callable")
        self.full_name = repository
        self.owner = parts[0]
        self.cwd = Path(cwd)
        self.output_dir = Path(output_dir)
        self.timeout_seconds = timeout_seconds
        self.should_stop = should_stop
        self._root = f"repos/{repository}"

    def _request(self, method, suffix="", *, params=None, body=None):
        if self.should_stop():
            raise GitHubError("GitHub operation cancelled before dispatch", cancelled=True)
        endpoint = self._root + suffix
        if params:
            endpoint += "?" + urlencode(params)
        argv = ["gh", "api", "--hostname", "github.com", "--method", method,
                "--header", "Accept: application/vnd.github+json",
                "--header", "X-GitHub-Api-Version: 2022-11-28"]
        if body is not None:
            argv += ["--input", "-"]
        argv.append(endpoint)
        operation_dir = self.output_dir / uuid.uuid4().hex
        mutation = method != "GET"
        try:
            result = run_process(argv, cwd=self.cwd, input_text=json.dumps(body) if body is not None else "",
                                 output_dir=operation_dir, timeout_seconds=self.timeout_seconds,
                                 should_stop=self.should_stop, max_output_bytes=_MAX_OUTPUT_BYTES,
                                 env={"GH_PROMPT_DISABLED": "1", "GH_DEBUG": "", "GH_FORCE_TTY": "",
                                      "GH_PAGER": "", "NO_COLOR": "1"})
        except Exception:
            # Do not echo subprocess exceptions, URLs, response bodies or credentials.
            raise GitHubError("GitHub process failed; inspect remote state before retrying",
                              outcome_unknown=mutation, cleanup_confirmed=False) from None
        try:
            if (result.error or result.returncode != 0 or result.cancelled or result.timed_out
                    or result.output_truncated or not result.cleanup_confirmed):
                if not result.cleanup_confirmed:
                    message = "GitHub process cleanup could not be confirmed"
                elif result.cancelled:
                    message = "GitHub operation cancelled"
                elif result.timed_out:
                    message = "GitHub operation timed out"
                elif result.output_truncated:
                    message = "GitHub response exceeded the capture limit; partial data refused"
                else:
                    message = "GitHub API request failed; check gh authentication, access and remote state"
                raise GitHubError(message, outcome_unknown=mutation,
                                  cleanup_confirmed=result.cleanup_confirmed,
                                  cancelled=result.cancelled, timed_out=result.timed_out)
            try:
                value = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise GitHubError("GitHub returned malformed JSON", outcome_unknown=mutation) from None
            if mutation and not isinstance(value, dict):
                raise GitHubError("GitHub returned an invalid mutation response", outcome_unknown=True)
            return value
        finally:
            # Successful cleanup permits removal of raw private issue content and
            # stderr. Unknown cleanup retains only the bounded subprocess capture.
            if result.cleanup_confirmed and operation_dir.is_dir():
                shutil.rmtree(operation_dir)

    def _pages(self, suffix, *, params=None, key=None):
        rows = []
        seen = set()
        expected = None
        for page in range(1, _MAX_PAGES + 1):
            response = self._request("GET", suffix,
                                     params={**(params or {}), "per_page": _PAGE_SIZE, "page": page})
            if key is not None:
                response = _object(response)
                total = response.get("total_count")
                if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                    raise GitHubError("GitHub returned an invalid pagination count")
                expected = max(expected or 0, total)
                items = response.get(key)
            else:
                items = response
            if not isinstance(items, list) or len(items) > _PAGE_SIZE or any(not isinstance(x, dict) for x in items):
                raise GitHubError("GitHub returned an invalid response page")
            for item in items:
                identity = item.get("id")
                if identity is not None:
                    if not isinstance(identity, int) or isinstance(identity, bool) or identity in seen:
                        raise GitHubError("GitHub pagination changed or repeated; partial data refused")
                    seen.add(identity)
            rows.extend(items)
            if len(items) < _PAGE_SIZE:
                if expected is not None and len(rows) < expected:
                    raise GitHubError("GitHub pagination was incomplete; partial data refused")
                return rows
        raise GitHubError("GitHub pagination limit reached; partial data refused")

    def repository(self):
        result = _object(self._request("GET"))
        if (not isinstance(result.get("full_name"), str)
                or result["full_name"].lower() != self.full_name.lower()):
            raise GitHubError("GitHub repository identity did not match the configured repository")
        _branch(result.get("default_branch"))
        return result

    def _issue(self, value):
        value = _object(value)
        if "pull_request" in value:
            raise GitHubError("Expected an issue but GitHub returned a pull request")
        try:
            number = int(_number(value.get("number")))
        except ValueError:
            raise GitHubError("GitHub returned an invalid issue number") from None
        if (not isinstance(value.get("title"), str) or value.get("state") not in {"open", "closed"}
                or value.get("body") is not None and not isinstance(value["body"], str)
                or not isinstance(value.get("html_url"), str)
                or not isinstance(value.get("updated_at"), str)):
            raise GitHubError("GitHub returned an invalid issue")
        labels = value.get("labels", [])
        if not isinstance(labels, list) or any(not isinstance(label, (str, dict)) for label in labels):
            raise GitHubError("GitHub returned invalid issue labels")
        labels = [label if isinstance(label, str) else label.get("name") for label in labels]
        if any(not isinstance(label, str) for label in labels):
            raise GitHubError("GitHub returned invalid issue labels")
        comments = self._pages(f"/issues/{number}/comments")
        for comment in comments:
            if not isinstance(comment.get("body"), str):
                raise GitHubError("GitHub returned an invalid issue comment")
        count = value.get("comments", 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0 or count > len(comments):
            raise GitHubError("GitHub issue comments were incomplete; partial data refused")
        return {"number": number, "title": value["title"], "body": value.get("body") or "",
                "html_url": value["html_url"], "state": value["state"], "updated_at": value["updated_at"],
                "labels": labels, "comments": comments}

    def list_open_issues(self):
        issues = self._pages("/issues", params={"state": "open", "sort": "created", "direction": "asc"})
        return [self._issue(issue) for issue in issues if "pull_request" not in issue]

    def get_issue(self, number):
        result = self._issue(self._request("GET", f"/issues/{_number(number)}"))
        if result["number"] != number:
            raise GitHubError("GitHub issue identity did not match the requested issue")
        return result

    def pull_request(self, number):
        return _object(self._request("GET", f"/pulls/{_number(number)}"))

    def find_pull_request(self, head, base):
        head, base = _branch(head), _branch(base)
        pulls = self._pages("/pulls", params={"state": "all", "head": f"{self.owner}:{head}", "base": base})
        matches = []
        for pull in pulls:
            branch = pull.get("head", {})
            target = pull.get("base", {})
            if (isinstance(branch, dict) and isinstance(target, dict)
                    and branch.get("ref") == head and target.get("ref") == base
                    and isinstance(branch.get("repo"), dict)
                    and str(branch["repo"].get("full_name", "")).lower() == self.full_name.lower()
                    and isinstance(target.get("repo"), dict)
                    and str(target["repo"].get("full_name", "")).lower() == self.full_name.lower()):
                matches.append(pull)
        if len(matches) > 1:
            raise GitHubError("Multiple pull requests match the owned branch; reconciliation required")
        return matches[0] if matches else None

    def create_pull_request(self, head, base, title, body):
        head, base = _branch(head), _branch(base)
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise ValueError("GitHub pull request title and body must be strings with a nonempty title")
        return _object(self._request("POST", "/pulls", body={"head": head, "base": base,
                                                            "title": title, "body": body}))

    def update_pull_request(self, number, title, body):
        number = _number(number)
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise ValueError("GitHub pull request title and body must be strings with a nonempty title")
        return _object(self._request("PATCH", f"/pulls/{number}", body={"title": title, "body": body}))

    def checks(self, head_sha):
        sha = _sha(head_sha)
        runs = self._pages(f"/commits/{sha}/check-runs", params={"filter": "latest"}, key="check_runs")
        statuses = self._pages(f"/commits/{sha}/statuses")
        result = []
        for run in runs:
            if (not isinstance(run.get("name"), str) or not isinstance(run.get("status"), str)
                    or run.get("conclusion") is not None and not isinstance(run["conclusion"], str)
                    or not isinstance(run.get("head_sha", sha), str)
                    or run.get("head_sha", sha).lower() != sha):
                raise GitHubError("GitHub returned an invalid check run")
            result.append({"name": run["name"], "status": run["status"], "conclusion": run.get("conclusion")})
        # Statuses are returned newest first. Only the latest per context applies.
        contexts = set()
        for status in statuses:
            context, state = status.get("context"), status.get("state")
            if not isinstance(context, str) or state not in {"pending", "success", "failure", "error"}:
                raise GitHubError("GitHub returned an invalid commit status")
            if context in contexts:
                continue
            contexts.add(context)
            result.append({"name": context, "status": "in_progress" if state == "pending" else "completed",
                           "conclusion": None if state == "pending" else "success" if state == "success" else "failure"})
        return result

    def merge_pull_request(self, number, head_sha, method="squash"):
        number, sha = _number(number), _sha(head_sha)
        if method not in {"merge", "squash", "rebase"}:
            raise ValueError("Invalid GitHub merge method")
        return _object(self._request("PUT", f"/pulls/{number}/merge", body={"sha": sha, "merge_method": method}))

    def close_issue(self, number):
        return _object(self._request("PATCH", f"/issues/{_number(number)}",
                                     body={"state": "closed", "state_reason": "completed"}))

    def branch_head(self, branch):
        response = _object(self._request("GET", "/git/ref/heads/" + quote(_branch(branch), safe="/")))
        value = response.get("object")
        if not isinstance(value, dict) or value.get("type") != "commit":
            raise GitHubError("GitHub returned an invalid branch reference")
        try:
            return _sha(value.get("sha"))
        except ValueError:
            raise GitHubError("GitHub returned an invalid branch SHA") from None

    def protection(self, branch):
        """Read classic branch protection; missing/inaccessible rules fail closed.

        This never creates, relaxes or changes repository policy. The caller
        must inspect enforcement and strict checks before authorizing a merge.
        """
        return _object(self._request("GET", "/branches/" + quote(_branch(branch), safe="") + "/protection"))

    def compare(self, base, head):
        base, head = _branch(base), _branch(head)
        return _object(self._request("GET", "/compare/" + quote(base, safe="") + "..." + quote(head, safe="")))
