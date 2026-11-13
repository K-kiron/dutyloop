"""Own task worktrees and an integration ref, never the operator's checkout."""

from __future__ import annotations

import hashlib
import contextvars
import re
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import plain_path, relative_path
from .process import run_process

_CONTROL = contextvars.ContextVar("git_control", default=None)


class GitCleanupError(RuntimeError):
    """A Git child may still be alive; no more work may be dispatched."""


class WorkspacePreparationError(RuntimeError):
    """A managed checkout failed before model work, with known Git cleanup."""

    def __init__(self, path, head, error):
        self.path = Path(path)
        self.head = head
        self.detail = str(error)
        super().__init__(f"Could not prepare worktree {self.path}: {self.detail}")

    def receipt(self):
        return {"operation": "worktree_add", "worktree": str(self.path),
                "head": self.head, "error": self.detail}


@contextmanager
def controlled_git(should_stop, remaining, output_dir):
    token = _CONTROL.set((should_stop, remaining, output_dir))
    try:
        yield
    finally:
        _CONTROL.reset(token)


def git(project: Path, *args: str) -> str:
    argv = ["git", "-C", str(project)]
    if sys.platform == "win32":
        # Managed file paths can exceed MAX_PATH; keep the override process-local.
        argv += ["-c", "core.longpaths=true"]
    argv += args
    control = _CONTROL.get()
    if control is not None:
        should_stop, remaining, output_dir = control
        operation_dir = plain_path(output_dir / uuid.uuid4().hex)
        result = run_process(argv, cwd=project, output_dir=operation_dir,
                             timeout_seconds=max(.001, remaining()), should_stop=should_stop)
        if not result.cleanup_confirmed:
            raise GitCleanupError("Git process-tree cleanup could not be confirmed; inspect retained logs")
        if result.error or result.cancelled or result.timed_out or result.output_truncated:
            raise RuntimeError(result.error or "Git operation interrupted by stop or deadline")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Git command failed")
        # Successful Git operations need no persistent empty capture files.
        # Failed/interrupted operations retain their logs for diagnosis.
        log_root = plain_path(output_dir)
        if operation_dir.parent != log_root:
            raise RuntimeError("Git log cleanup escaped its owned directory")
        shutil.rmtree(operation_dir)
        return result.stdout.strip()
    # A local MCP server owns stdin for its protocol. Noninteractive Git must
    # never inherit that pipe (including Windows handle inheritance).
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "Git command failed")
    return result.stdout.decode("utf-8", "replace").strip()


def create_branch(project: Path, branch: str, head: str):
    git(project, "update-ref", f"refs/heads/{branch}", head, "0" * len(head))


def make_worktree(project: Path, path: Path, head: str) -> Path:
    path = plain_path(path)
    if path.exists():
        raise ValueError(f"Worktree path already exists: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(project, "worktree", "add", "--detach", str(path), head)
    except GitCleanupError:
        raise
    except (RuntimeError, OSError) as error:
        raise WorkspacePreparationError(path, head, error) from error
    return path


def remove_observation(project: Path, path: Path, observations: Path):
    """Remove only an owned, unchanged discovery checkout via Git's registry."""
    path, observations = plain_path(path), plain_path(observations)
    if path.parent != observations:
        raise ValueError("Observation cleanup escaped its owned directory")
    # No --force: modified evidence is retained even if a caller misclassifies it.
    git(project, "worktree", "remove", str(path))


def assert_identity(path: Path, expected_head: str, git_file: bytes):
    pointer = plain_path(path / ".git")
    if not pointer.is_file() or pointer.read_bytes() != git_file:
        raise RuntimeError("Worker changed its Git worktree identity")
    if git(path, "rev-parse", "HEAD") != expected_head or git(path, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        raise RuntimeError("Worker changed or attached its base HEAD; integration refused")


def changed_paths(path: Path) -> list[str]:
    entries = git(path, "ls-files", "-v", "-z").split("\x00")
    if any(entry and (entry[0].islower() or entry[0] == "S") for entry in entries):
        raise ValueError("Unsupported assume-unchanged or skip-worktree index flags; inspect retained worktree")
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
        mode = target.stat().st_mode if target.exists() else 0
        content = target.read_bytes() if target.is_file() else b""
        digest.update(item.encode() + b"\x00")
        digest.update(str(mode).encode() + b"\x00")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def staged_diff(path: Path) -> str:
    git(path, "add", "--all")
    diff = git(path, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-renames", "--", ".")
    if len(diff.encode("utf-8")) > 150000:
        raise ValueError("Diff exceeds the independent review size limit")
    return diff


def prepare_commit(path: Path, title: str, kind: str, *, expected_tree: str, expected_base: str):
    title = " ".join(title.split())[:140]
    if re.search(r"generated (?:by|with)|written by ai|co-authored-by|https?://(?:chatgpt\.com|claude\.ai)/", title, re.I):
        raise ValueError("Commit title contains prohibited attribution or a private conversation link")
    if git(path, "write-tree") != expected_tree:
        raise RuntimeError("The staged tree differs from the reviewed tree; integration refused")
    if git(path, "rev-parse", "HEAD") != expected_base:
        raise RuntimeError("The worktree HEAD differs from the reviewed base; integration refused")
    git(path, "commit", "-m", f"{kind}: {title}")
    head = git(path, "rev-parse", "HEAD")
    if git(path, "rev-list", "--parents", "--max-count=1", head).split() != [head, expected_base]:
        raise RuntimeError("A commit hook changed the reviewed ancestry; integration refused")
    if git(path, "rev-parse", "HEAD^{tree}") != expected_tree:
        raise RuntimeError("A commit hook changed the reviewed tree; integration refused")
    return head
