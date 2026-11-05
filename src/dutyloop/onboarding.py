"""Generate and inspect operator-owned configuration without starting agents."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .config import plain_path, validate_spec


def read_spec(path: Path) -> dict:
    path = Path(path).expanduser()
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Mission configuration exceeds 1 MiB")

    def invalid_constant(value):
        raise ValueError(f"Mission configuration contains a non-JSON number: {value}")

    spec = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid_constant)
    if not isinstance(spec, dict):
        raise ValueError("The mission spec must be a JSON object.")
    return spec


def validation_report(spec: dict, state_dir: Path) -> dict:
    """Validate inputs and report snapshot pitfalls without creating refs or state."""
    normalized = validate_spec(spec, state_dir)
    project = Path(normalized["project"])
    warnings = []
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
         "-C", str(project), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    if result.stdout.strip():
        warnings.append("The checkout has uncommitted changes. Mission input is committed HEAD; commit intended input first.")
    for argv in normalized["verification"]:
        program = argv[0]
        if not shutil.which(program) and not (project / program).is_file():
            warnings.append(f"Verification executable was not found: {program}")
    if normalized["driver"]["name"] == "command":
        warnings.append("The custom wrapper runs with your local authority; its permission and host protocol behavior are your responsibility.")
    if (plain_path(state_dir) / "mission.sqlite3").exists():
        warnings.append("The state directory already contains a mission; init will not overwrite it.")
    return {"valid": True, "spec": normalized, "warnings": warnings}


def write_configuration(*, project: Path, output: Path, mission: str,
                        allowed_paths: list[str], verification: list[str],
                        driver: str = "codex", model: str | None = None,
                        max_invocations: int = 12, max_wall_seconds: float = 1800) -> dict:
    """Write a complete, validated spec exclusively; never overwrite user input."""
    commands = []
    for text in verification:
        try:
            commands.append(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ValueError("Each --verify-json must be a JSON argv array, such as [\"python\",\"-m\",\"unittest\"]") from exc
    project = plain_path(project)
    output = plain_path(output)
    spec = {
        "version": 1, "mission": mission, "project": str(project),
        "allowed_paths": allowed_paths, "verification": commands,
        "driver": {"name": driver, "model": model},
        "limits": {"max_invocations": max_invocations, "max_wall_seconds": max_wall_seconds},
    }
    report = validation_report(spec, project.parent / ".dutyloop-configuration-check")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".mission-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report["spec"], ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic creation without replacing an existing file, even under a race.
        os.link(temporary, output)
    finally:
        os.unlink(temporary)
    return {"created": str(output), "warnings": report["warnings"], "started": False}


def human_status(snapshot: dict) -> str:
    tasks = snapshot.get("tasks", [])
    counts = {state: sum(task.get("state") == state for task in tasks)
              for state in ("accepted", "rejected", "unknown")}
    limits = snapshot.get("limits", {})
    lines = [
        f"DutyLoop: {snapshot.get('status', 'unknown')}",
        f"Mission: {snapshot.get('mission', '')}",
        f"Project: {snapshot.get('project', '')}",
        f"Branch: {snapshot.get('branch', '')}",
        f"Accepted head: {snapshot.get('accepted_head', '')}",
        f"Calls: {snapshot.get('invocations', 0)}/{limits.get('max_invocations', '?')}",
        f"Tasks: {counts['accepted']} accepted, {counts['rejected']} rejected, {counts['unknown']} unknown",
    ]
    if snapshot.get("reason"):
        lines.append(f"Reason: {snapshot['reason']}")
    calls = snapshot.get("calls", [])
    if calls:
        last = calls[-1]
        lines.append(f"Latest call: {last.get('phase', '?')} ({last.get('state', '?')})")
        if last.get("error"):
            lines.append(f"Diagnostic: {last['error']}")
    known_cost = snapshot.get("reported_cost_usd")
    unknown = snapshot.get("calls_without_cost", 0)
    lines.append("Reported cost: unknown" if known_cost is None else f"Reported cost: USD {known_cost:.4f}; {unknown} calls without cost")
    if snapshot.get("state_dir"):
        lines.append(f"State and evidence: {snapshot['state_dir']}")
    return "\n".join(lines)
