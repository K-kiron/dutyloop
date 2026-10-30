"""Own task worktrees and an integration ref, never the operator's checkout."""

from __future__ import annotations

import hashlib
import contextvars
import re
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import plain_path, relative_path
from .process import run_process

_CONTROL = contextvars.ContextVar("git_control", default=None)


@contextmanager
def controlled_git(should_stop, remaining, output_dir):
    token = _CONTROL.set((should_stop, remaining, output_dir))
    try:
        yield
    finally:
        _CONTROL.reset(token)


def git(project: Path, *args: str) -> str:
    argv = ["git", "-C", str(project), *args]
    control = _CONTROL.get()
    if control is not None:
        should_stop, remaining, output_dir = control
        result = run_process(argv, cwd=project, output_dir=output_dir / uuid.uuid4().hex,
                             timeout_seconds=max(.001, remaining()), should_stop=should_stop)
        if result.error or result.cancelled or result.timed_out or not result.cleanup_confirmed or result.output_truncated:
            raise RuntimeError(result.error or "Git operation interrupted by stop or deadline")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Git command failed")
        return result.stdout.strip()
    result = subprocess.run(argv, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "Git command failed")
    return result.stdout.decode("utf-8", "replace").strip()


def create_branch(project: Path, branch: str, head: str):
    git(project, "update-ref", f"refs/heads/{branch}", head, "0" * len(head))


def make_worktree(project: Path, path: Path, head: str) -> Path:
    path = plain_path(path)
    if path.exists():
        raise ValueError(f"Worktree path already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    git(project, "worktree", "add", "--detach", str(path), head)
    return path


def assert_identity(path: Path, expected_head: str, git_file: bytes):
    pointer = plain_path(path / ".git")
    if not pointer.is_file() or pointer.read_bytes() != git_file:
        raise RuntimeError("Worker changed its Git worktree identity")
    if git(path, "rev-parse", "HEAD") != expected_head or git(path, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        raise RuntimeError("Worker changed or attached its base HEAD; integration refused")


def changed_paths(path: Path) -> list[str]:
    tracked = git(path, "diff", "--no-renames", "--name-only", "-z", "HEAD").split("\x00")
    untracked = git(path, "ls-files", "--others", "--exclude-standard", "-z").split("\x00")
    return sorted(set(item for item in tracked + untracked if item))


def check_scope(path: Path, allowed: list[str], max_files: int):
    changes = changed_paths(path)
    if len(changes) > max_files:
        raise ValueError(f"Task changed {len(changes)} files; limit is {max_files}")
    for item in changes:
        item = relative_path(item)
        if not any(prefix == "." or item == prefix or item.startswith(prefix + "/") for prefix in allowed):
            raise ValueError(f"Out-of-scope change: {item}")
        plain_path(path / item)
    return changes


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for item in changed_paths(path):
        target = plain_path(path / relative_path(item))
        digest.update(item.encode())
        digest.update(target.read_bytes() if target.is_file() else b"<deleted>")
    return digest.hexdigest()


def staged_diff(path: Path) -> str:
    git(path, "add", "--all")
    diff = git(path, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-renames", "--", ".")
    if len(diff.encode("utf-8")) > 150000:
        raise ValueError("Diff exceeds the independent review size limit")
    return diff


def prepare_commit(path: Path, title: str, kind: str):
    title = " ".join(title.split())[:140]
    if re.search(r"generated (?:by|with)|written by ai|co-authored-by|https?://(?:chatgpt\.com|claude\.ai)/", title, re.I):
        raise ValueError("Commit title contains prohibited attribution or a private conversation link")
    tree = git(path, "write-tree")
    git(path, "commit", "-m", f"{kind}: {title}")
    head = git(path, "rev-parse", "HEAD")
    if git(path, "rev-parse", "HEAD^{tree}") != tree:
        raise RuntimeError("A commit hook changed the reviewed tree; integration refused")
    return head
