"""Validate operator authority before any agent process is started."""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path, PurePosixPath

KINDS = {"feature", "bugfix", "qa", "docs"}


def plain_path(path: Path) -> Path:
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError(f"Symlink or junction is not allowed: {part}")
        if part.exists():
            attrs = getattr(part.lstat(), "st_file_attributes", 0)
            if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise ValueError(f"Reparse point is not allowed: {part}")
    return path.resolve()


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        raise ValueError("Scope paths must be nonempty project-relative paths")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError(f"Unsafe project-relative path: {value}")
    return path.as_posix()


def _number(value, label: str, minimum: float, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(value) or value < minimum or (integer and not isinstance(value, int)):
        raise ValueError(f"Invalid {label}")
    return value


def _unknown(mapping, allowed, label):
    if not isinstance(mapping, dict):
        raise ValueError(f"{label} must be an object")
    extra = mapping.keys() - allowed
    if extra:
        raise ValueError(f"Unknown {label} keys: {', '.join(sorted(extra))}; monetary hard caps are unsupported")


def validate_spec(spec: dict, state_dir: Path) -> dict:
    allowed = {"version", "mission", "project", "allowed_paths", "verification", "limits", "constraints", "kinds", "research_domains", "driver", "team", "poll_seconds", "max_task_attempts", "max_failures", "branch", "call_timeout_seconds", "max_changed_files"}
    _unknown(spec, allowed, "spec")
    if spec.get("version", 1) != 1:
        raise ValueError("Only spec version 1 is supported")
    mission = spec.get("mission")
    if not isinstance(mission, str) or not mission.strip() or len(mission) > 20000:
        raise ValueError("mission must contain 1..20000 characters")
    if not isinstance(spec.get("project"), str):
        raise ValueError("project must be a Git root path")
    project, state = plain_path(Path(spec["project"])), plain_path(state_dir)
    if state == project or project in state.parents or state in project.parents:
        raise ValueError("State directory must be outside the project and must not contain it")
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != project:
        raise ValueError("project must name the root of an existing Git repository")
    paths = spec.get("allowed_paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("allowed_paths must be a nonempty list")
    paths = [relative_path(path) for path in paths]
    verification = spec.get("verification")
    if not isinstance(verification, list) or not verification:
        raise ValueError("At least one operator-owned verification command is required")
    for argv in verification:
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise ValueError("verification must contain nonempty argv arrays, not shell strings")
    limits = spec.get("limits")
    _unknown(limits, {"max_invocations", "max_wall_seconds"}, "limits")
    limits = {
        "max_invocations": _number(limits.get("max_invocations"), "max_invocations", 1, True),
        "max_wall_seconds": _number(limits.get("max_wall_seconds"), "max_wall_seconds", .1),
    }
    driver = spec.get("driver", {"name": "codex"})
    _unknown(driver, {"name", "model", "argv"}, "driver")
    if driver.get("name") not in {"codex", "claude", "command"}:
        raise ValueError("driver.name must be codex, claude or command")
    if driver.get("model") is not None and (not isinstance(driver["model"], str) or not driver["model"].strip()):
        raise ValueError("driver.model must be a nonempty string or null")
    if driver["name"] == "command":
        argv = driver.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise ValueError("command driver requires an argv array")
    elif "argv" in driver:
        raise ValueError("argv is only valid for the command driver")
    team = spec.get("team", {"discovery_workers": 1})
    _unknown(team, {"discovery_workers"}, "team")
    workers = _number(team.get("discovery_workers", 1), "discovery_workers", 1, True)
    if workers > 3:
        raise ValueError("At most three discovery workers are supported")
    kinds = spec.get("kinds", sorted(KINDS))
    if not isinstance(kinds, list) or not kinds or any(kind not in KINDS for kind in kinds):
        raise ValueError("kinds must select feature, bugfix, qa or docs")
    constraints = spec.get("constraints", [])
    domains = spec.get("research_domains", [])
    if not isinstance(constraints, list) or any(not isinstance(item, str) for item in constraints):
        raise ValueError("constraints must be strings")
    if not isinstance(domains, list) or any(not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+", item) for item in domains):
        raise ValueError("research_domains must contain hostnames")
    branch = spec.get("branch")
    if branch is not None:
        if not isinstance(branch, str) or branch.startswith(("codex/", "claude/")) or subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True).returncode:
            raise ValueError("branch must be a functional Git branch name")
    return {
        "version": 1, "mission": mission.strip(), "project": str(project),
        "allowed_paths": paths, "verification": verification, "limits": limits,
        "constraints": constraints, "kinds": kinds, "research_domains": [x.lower() for x in domains],
        "driver": driver, "team": {"discovery_workers": workers}, "branch": branch,
        "poll_seconds": _number(spec.get("poll_seconds", 60), "poll_seconds", .05),
        "max_task_attempts": _number(spec.get("max_task_attempts", 2), "max_task_attempts", 1, True),
        "max_failures": _number(spec.get("max_failures", 3), "max_failures", 1, True),
        "call_timeout_seconds": _number(spec.get("call_timeout_seconds", 300), "call_timeout_seconds", .1),
        "max_changed_files": _number(spec.get("max_changed_files", 20), "max_changed_files", 1, True),
    }
