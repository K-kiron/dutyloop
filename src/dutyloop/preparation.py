"""Read-only evidence for retrying a child that never dispatched model work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from .config import plain_path
from .issue_config import ISSUE_PLAN_SCHEMA
from .schemas import validate_payload


def _git(project, *arguments):
    # No optional index refresh or operation logs: inspection must not change state.
    result = subprocess.run(
        ["git", "-C", str(project), "-c", "core.longpaths=true", *arguments],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    result.check_returncode()
    return result.stdout.decode("utf-8").strip()


def _common(project):
    common = Path(_git(project, "rev-parse", "--git-common-dir"))
    return plain_path(common if common.is_absolute() else project / common)


def _read_child(database):
    # mode=ro prevents inspection from creating an absent or replacement database.
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=10) as db:
        db.execute("BEGIN")
        child = json.loads(db.execute("SELECT value FROM mission WHERE id=1").fetchone()[0])
        if db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] or db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
            return None, None
        event = db.execute("SELECT data FROM events WHERE kind='selected_task_error' ORDER BY id DESC LIMIT 1").fetchone()
        return child, json.loads(event[0]) if event else None


def _plan_current(batch, row, head):
    planning = batch.get("planning") or {}
    item = next((item for item in (planning.get("roadmap") or {}).get("items", [])
                 if item["number"] == row["number"]), None)
    source = {key: value for key, value in row["source"].items() if key != "updated_at"}
    source_hash = hashlib.sha256(json.dumps(
        source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    expected_head = row.get("head") or planning.get("expected_head") or batch.get("accepted_head")
    return bool(item and item.get("source_sha256") == source_hash
                and row.get("roadmap_revision") == planning.get("revision")
                and row.get("roadmap_revision") is not None and expected_head == head)


def inspect_child(batch_store, row):
    """Return narrowly proven preparation evidence, or None without changing state.

    A stale plan does not invalidate clean-setup evidence; plan_current tells the
    caller whether to retain that plan or return the issue to detailed planning.
    This helper grants neither recovery nor execution permission.
    """
    try:
        if not isinstance(row, dict) or type(row.get("number")) is not int or row["number"] < 1:
            return None
        if row.get("state") not in {"interrupted", "needs_decision"} or row.get("reason") not in {
                "interrupted_work_requires_inspection", "workspace_preparation_failed", "selected_task_failed"}:
            return None
        if row.get("pending_scope_sha256") is not None:
            return None
        plan = row["plan"]
        candidate = {key: value for key, value in plan["candidates"][0].items() if key != "source_receipts"}
        validate_payload({**plan, "candidates": [candidate]}, ISSUE_PLAN_SCHEMA)
        if (plan["status"] != "work" or len(plan["candidates"]) != 1
                or candidate["key"] != f"issue-{row['number']}"
                or any(not value.strip() for value in candidate["acceptance"])):
            return None
        batch = batch_store.control()
        root = plain_path(batch_store.directory)
        child_state = plain_path(row["child_state"])
        token = child_state.name
        if child_state.parent != root / "missions" or not re.fullmatch(r"[0-9a-f]{10}", token):
            return None
        database = plain_path(child_state / "mission.sqlite3")
        if not database.is_file():
            return None
        child, event = _read_child(database)
        if (not child or child.get("runner_pid") is not None or child.get("stop_requested") is not False
                or type(child.get("invocations")) is not int or child["invocations"] != 0):
            return None
        project = plain_path(child["spec"]["project"])
        if (project != root / "bases" / token or child.get("branch") != f"work/issue-{row['number']}-fix-{token}"
                or child["spec"].get("branch") != child["branch"]):
            return None
        head = row["plan_base_sha"]
        if (not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head)
                or child.get("accepted_head") != head
                or _git(project, "rev-parse", f"refs/heads/{child['branch']}") != head
                or _git(project, "rev-parse", "HEAD") != head
                or _git(project, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD"
                or plain_path(_git(project, "rev-parse", "--show-toplevel")) != project
                or _common(project) != _common(plain_path(batch["spec"]["project"]))):
            return None
        flags = _git(project, "ls-files", "-v", "-z").split("\x00")
        if (any(flag and (flag[0].islower() or flag[0] == "S") for flag in flags)
                or _git(project, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", "HEAD")
                or _git(project, "ls-files", "--others", "--exclude-standard", "-z")):
            return None
        legacy, worktree = False, None
        if child.get("status") == "blocked" and child.get("reason") == "workspace_preparation_failed":
            receipt = child["preparation_failure"]
            if (receipt.get("operation") != "worktree_add" or receipt.get("head") != head
                    or receipt.get("cleanup_confirmed") is not True or receipt.get("dispatch_started") is not False):
                return None
            worktree = plain_path(receipt["worktree"])
            if worktree.parent not in {child_state / "observations", child_state / "worktrees"}:
                return None
            error = receipt["error"]
        elif (child.get("status") == "blocked" and child.get("reason") == "selected_task_failed"
              or child.get("status") == "stopped" and child.get("reason") == "operator_stop"):
            error = event["error"] if event else ""
            prefix = re.match(r"^Preparing worktree \(detached HEAD ([0-9a-f]{7,64})\)(?:\r?\n|$)", error)
            if (not prefix or not head.startswith(prefix[1])
                    or not re.search(r"(?:^|\n)error: unable to create file [^\r\n]+: Filename too long(?:\r?\n|$)", error)):
                return None
            legacy = True
        else:
            return None
        if not isinstance(error, str) or not error.strip():
            return None
        return {"issue_number": row["number"], "child_state": str(child_state), "head": head,
                "worktree": str(worktree) if worktree is not None else None, "error": error, "legacy": legacy,
                "operation": "worktree_add", "cleanup_confirmed": True, "dispatch_started": False,
                "plan_current": _plan_current(batch, row, head)}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, sqlite3.Error,
            subprocess.SubprocessError):
        return None
