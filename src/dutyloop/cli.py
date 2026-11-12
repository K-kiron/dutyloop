"""Command-line control for explicitly authorized project missions."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


def _engine():
    from . import engine

    return engine


def _configure_output_streams() -> None:
    """Keep terminal handling native; pipes and background logs use UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8")


def error_payload(error: Exception) -> dict:
    return {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}


def _reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Symlinks and reparse points are not allowed: {path}")


def _unambiguous_state(state_dir: Path) -> None:
    if (state_dir / "mission.sqlite3").exists() and (state_dir / "issues.sqlite3").exists():
        raise ValueError("Ambiguous state contains both mission and issue databases; inspect it before continuing")


def _mission_state(state_dir: Path) -> None:
    _unambiguous_state(state_dir)
    if (state_dir / "issues.sqlite3").exists():
        raise ValueError("This state belongs to an issue batch or repository; use its corresponding commands.")


def _repository_state(state_dir: Path) -> None:
    _unambiguous_state(state_dir)
    if (state_dir / "mission.sqlite3").exists():
        raise ValueError("This state belongs to a mission; use mission commands.")


def _finite_issue_state(state_dir: Path) -> None:
    from . import repository

    _repository_state(state_dir)
    if repository.is_repository(state_dir):
        raise ValueError("Repository maintenance state requires repository commands, not finite issue commands.")


def start_background(state_dir: Path, *, issue_batch=False, plan_only=False, repository=False) -> dict:
    """Launch a runner without waiting; the engine enforces exclusive ownership."""
    state_dir = Path(state_dir).expanduser().absolute()
    for path in (state_dir, *state_dir.parents):
        _reject_link(path)
    state_dir = state_dir.resolve()
    _unambiguous_state(state_dir)
    if repository and issue_batch:
        raise ValueError("Select either repository maintenance or a finite issue batch.")
    if plan_only and (not issue_batch or repository):
        raise ValueError("plan_only requires an issue batch")
    if repository:
        from . import repository as repository_module
        _repository_state(state_dir)
        snapshot = repository_module.status(state_dir)
    elif issue_batch:
        from . import issues
        _finite_issue_state(state_dir)
        snapshot = issues.status(state_dir)
    else:
        _mission_state(state_dir)
        snapshot = _engine().status(state_dir)
    if snapshot.get("status") in {"stopped", "limited", "blocked"} or snapshot.get("stop_requested"):
        raise ValueError("This mission cannot restart after stopping, reaching a limit, or becoming blocked.")

    stdout_path = state_dir / "runner.stdout.log"
    stderr_path = state_dir / "runner.stderr.log"
    _reject_link(stdout_path)
    _reject_link(stderr_path)
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(state_dir),
    }
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "dutyloop", *(["repository"] if repository else ["issues"] if issue_batch else []), "run", "--state", str(state_dir),
             *(["--plan-only"] if plan_only else [])],
            stdout=stdout,
            stderr=stderr,
            **options,
        )
    return {
        "status": "starting",
        "pid": process.pid,
        "state_dir": str(state_dir),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def _serve_mcp(root: Path, projects: list[Path]) -> None:
    from .mcp_server import create_server

    create_server(root, projects).run(transport="stdio")


def _parser() -> argparse.ArgumentParser:
    from . import __version__
    from .drivers import NATIVE_DRIVERS

    parser = argparse.ArgumentParser(prog="dutyloop", description=__doc__)
    parser.add_argument("--version", action="version", version=f"DutyLoop {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    maintenance = commands.add_parser("repository", help="Continuously observe and maintain an authorized repository")
    repository_commands = maintenance.add_subparsers(dest="repository_command", required=True)
    for name in ("init", "reconcile", "run", "start", "status", "stop", "events", "recover", "resolve", "attention", "set-mode"):
        item = repository_commands.add_parser(name)
        item.add_argument("--state", type=Path, required=True)
        if name == "init":
            item.add_argument("--spec", type=Path, required=True)
        if name == "status":
            item.add_argument("--human", action="store_true")
        if name == "events":
            item.add_argument("--after", type=int, default=0)
            item.add_argument("--limit", type=int, default=20)
        if name == "recover":
            item.add_argument("--acknowledge", action="store_true")
        if name == "resolve":
            item.add_argument("--number", type=int, required=True)
            item.add_argument("--decision", required=True)
        if name == "set-mode":
            item.add_argument("--mode", choices=("observe", "auto"), required=True)
    backlog = commands.add_parser("issues", help="Plan and process a finite GitHub issue backlog")
    issue_commands = backlog.add_subparsers(dest="issue_command", required=True)
    for name in ("init", "run", "start", "status", "stop", "events", "recover", "resolve"):
        item = issue_commands.add_parser(name)
        item.add_argument("--state", type=Path, required=True)
        if name == "init":
            item.add_argument("--spec", type=Path, required=True)
        if name in {"run", "start"}:
            item.add_argument("--plan-only", action="store_true", help="Assess the backlog and create its phased roadmap without detail planning or implementation")
        if name == "status":
            item.add_argument("--human", action="store_true")
        if name == "events":
            item.add_argument("--after", type=int, default=0)
            item.add_argument("--limit", type=int, default=20)
        if name == "recover":
            item.add_argument("--acknowledge", action="store_true")
        if name == "resolve":
            item.add_argument("--number", type=int, required=True)
            item.add_argument("--decision", required=True)
    configure = commands.add_parser("configure", help="Write a validated mission configuration without starting work")
    configure.add_argument("--project", type=Path, required=True)
    configure.add_argument("--output", type=Path, required=True)
    configure.add_argument("--mission", required=True)
    configure.add_argument("--allow-path", action="append", required=True)
    configure.add_argument("--verify-json", action="append", required=True, help="Verification argv encoded as one JSON array; repeat for more checks")
    configure.add_argument("--driver", choices=(*NATIVE_DRIVERS, "desktop"), default="codex")
    configure.add_argument("--model")
    configure.add_argument("--max-invocations", type=int, default=12)
    configure.add_argument("--max-wall-seconds", type=float, default=1800)
    validate = commands.add_parser("validate", help="Validate configuration read-only; do not create state or branches")
    validate.add_argument("--spec", type=Path, required=True)
    validate.add_argument("--state", type=Path, required=True)
    initialize = commands.add_parser("init", help="Initialize a mission without starting it")
    initialize.add_argument("--spec", type=Path, required=True)
    initialize.add_argument("--state", type=Path, required=True)
    for name, help_text in (
        ("run", "Run a mission in the foreground"),
        ("start", "Launch a mission in the background"),
        ("status", "Read mission state"),
        ("stop", "Request asynchronous cancellation"),
        ("recover", "Inspect or explicitly acknowledge interrupted work"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--state", type=Path, required=True)
        if name == "recover":
            command.add_argument("--acknowledge", action="store_true")
        if name == "status":
            command.add_argument("--human", action="store_true", help="Show a compact operator summary instead of JSON")
    events = commands.add_parser("events", help="Read ordered mission events after a durable cursor")
    events.add_argument("--state", type=Path, required=True)
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=20)
    doctor = commands.add_parser("doctor", help="Check native CLI versions and Git prerequisites without invoking models")
    doctor.add_argument("--driver", choices=NATIVE_DRIVERS)
    doctor.add_argument("--project", type=Path)
    doctor.add_argument("--timeout-seconds", type=float, default=5)
    demo = commands.add_parser("demo", help="Run the deterministic local demonstration")
    demo.add_argument("--directory", type=Path, required=True)
    mcp = commands.add_parser("mcp", help="Serve the optional MCP control interface over stdio")
    mcp.add_argument("--root", type=Path, required=True)
    mcp.add_argument("--allow-project", type=Path, action="append", required=True)
    desktop = commands.add_parser("desktop-config", help="Print local desktop MCP configuration without changing host settings")
    desktop.add_argument("--client", choices=("codex", "claude"), required=True)
    desktop.add_argument("--root", type=Path, required=True)
    desktop.add_argument("--allow-project", type=Path, action="append", required=True)
    return parser


def _dispatch(arguments: argparse.Namespace) -> dict | str | None:
    command = arguments.command
    if command == "repository":
        from . import repository
        _repository_state(arguments.state)
        action = arguments.repository_command
        if action == "init":
            from .onboarding import read_spec
            return repository.initialize(read_spec(arguments.spec), arguments.state)
        if action == "start":
            return start_background(arguments.state, repository=True)
        if action == "events":
            return repository.events(arguments.state, after_id=arguments.after, limit=arguments.limit)
        if action == "recover":
            return repository.recover(arguments.state, acknowledge=arguments.acknowledge)
        if action == "resolve":
            return repository.resolve(arguments.state, arguments.number, arguments.decision)
        if action == "set-mode":
            return repository.set_mode(arguments.state, arguments.mode)
        if action in {"status", "attention"}:
            snapshot = repository.status(arguments.state)
            if action == "attention":
                return {"attention": snapshot.get("attention", [])}
            return _repository_human_status(snapshot) if arguments.human else snapshot
        return getattr(repository, action)(arguments.state)
    if command == "issues":
        from . import issues
        action = arguments.issue_command
        _finite_issue_state(arguments.state)
        if action == "init":
            from .onboarding import read_spec
            return issues.initialize(read_spec(arguments.spec), arguments.state)
        if action == "run":
            return issues.run(arguments.state, plan_only=arguments.plan_only)
        if action == "start":
            options = {"issue_batch": True}
            if arguments.plan_only:
                options["plan_only"] = True
            return start_background(arguments.state, **options)
        if action == "status":
            snapshot = issues.status(arguments.state)
            if not arguments.human:
                return snapshot
            lines = [f"DutyLoop issues: {snapshot['status']}",
                     f"Repository: {snapshot['spec']['github']['repository']}",
                     f"Delivery: {snapshot['spec']['github']['delivery']}",
                     f"Calls: {snapshot['invocations']}/{snapshot['spec']['limits']['max_invocations']}"]
            lines.extend(_planning_human_lines(snapshot))
            lines.extend(f"#{row['number']} [{row['state']}] {row['source']['title']}"
                         + (f" — {row['reason']}" if row.get("reason") else "") for row in snapshot["issues"])
            return "\n".join(lines)
        if action == "stop":
            return issues.stop(arguments.state)
        if action == "events":
            return issues.events(arguments.state, after_id=arguments.after, limit=arguments.limit)
        if action == "recover":
            return issues.recover(arguments.state, acknowledge=arguments.acknowledge)
        if action == "resolve":
            return issues.resolve(arguments.state, arguments.number, arguments.decision)
    if command in {"configure", "validate", "init"}:
        from .onboarding import read_spec, validation_report, write_configuration
    if command in {"init", "run", "start", "status", "events", "stop", "recover"}:
        _mission_state(arguments.state)
    if command == "configure":
        return write_configuration(project=arguments.project, output=arguments.output, mission=arguments.mission,
                                   allowed_paths=arguments.allow_path, verification=arguments.verify_json,
                                   driver=arguments.driver, model=arguments.model,
                                   max_invocations=arguments.max_invocations, max_wall_seconds=arguments.max_wall_seconds)
    if command == "validate":
        return validation_report(read_spec(arguments.spec), arguments.state)
    if command == "init":
        return _engine().initialize(read_spec(arguments.spec), arguments.state)
    if command == "run":
        return _engine().run(arguments.state)
    if command == "start":
        return start_background(arguments.state)
    if command == "status":
        snapshot = _engine().status(arguments.state)
        if arguments.human:
            from .onboarding import human_status
            return human_status(snapshot)
        return snapshot
    if command == "events":
        return _engine().events(arguments.state, after_id=arguments.after, limit=arguments.limit)
    if command == "stop":
        return _engine().request_stop(arguments.state)
    if command == "recover":
        return _engine().recover(arguments.state, acknowledge=arguments.acknowledge)
    if command == "doctor":
        from .diagnostics import doctor
        return doctor(driver=arguments.driver, project=arguments.project, timeout_seconds=arguments.timeout_seconds)
    if command == "demo":
        from .demo import run_demo

        return run_demo(arguments.directory)
    if command == "mcp":
        _serve_mcp(arguments.root, arguments.allow_project)
        return None
    if command == "desktop-config":
        from .mcp_server import desktop_configuration
        return desktop_configuration(arguments.client, arguments.root, arguments.allow_project)
    raise ValueError(f"Unknown command: {command}")


def _planning_human_lines(snapshot: dict) -> list[str]:
    planning = snapshot.get("planning")
    if not planning:
        return []
    coverage = planning.get("coverage", {})
    lines = [f"Planning: {planning.get('status', 'pending')} "
             f"(revision {planning.get('revision', 0)}; "
             f"assessed {len(coverage.get('assessed', []))}/{coverage.get('total', 0)})"]
    workers = planning.get("workers", snapshot.get("spec", {}).get("queue", {}).get("planning_workers"))
    if workers is not None:
        lines.append(f"Planning workers: up to {workers} for chunk assessments")
    chunks = planning.get("chunks", [])
    if chunks:
        counts = {status: sum(chunk["status"] == status for chunk in chunks)
                  for status in ("completed", "running", "prepared", "incomplete")}
        lines.append(f"Planning requests: {counts['completed']}/{len(chunks)} completed; "
                     f"{counts['running']} running; {counts['prepared']} prepared; {counts['incomplete']} incomplete")
    if planning.get("reused_assessments"):
        lines.append(f"Reused assessments: {planning['reused_assessments']} chunks "
                     f"covering {len(planning.get('reused_issues', []))} issues")
    if planning.get("reason"):
        lines.append(f"Planning reason: {planning['reason']}")
    if coverage.get("unassessed"):
        lines.append("Unassessed: " + ", ".join(f"#{number}" for number in coverage["unassessed"]))
    barrier = planning.get("stage_barrier")
    if barrier:
        stage = barrier["stage"]
        numbers = ", ".join(f"#{number}" for number in stage["issue_numbers"])
        lines.append(f"Stage barrier: {stage['title']} — original issues {numbers}")
        lines.append(f"  Original goal: {stage['goal']}")
    roadmap = planning.get("roadmap") or {}
    if not roadmap:
        return lines
    lines.append(f"Current stage: {roadmap.get('current_stage', 'unknown')}")
    if roadmap.get("summary"):
        lines.append(f"Roadmap: {roadmap['summary']}")
    for stage in roadmap.get("stages", []):
        numbers = ", ".join(f"#{number}" for number in stage.get("issue_numbers", [])) or "no issues"
        lines.append(f"Stage {stage['id']}: {stage['title']} — {numbers}")
        lines.append(f"  Goal: {stage['goal']}")
        lines.append(f"  Rationale: {stage['rationale']}")
        lines.append("  Entry: " + "; ".join(stage.get("entry_conditions", [])))
        lines.append("  Exit: " + "; ".join(stage.get("exit_criteria", [])))
        gate = ("human acceptance required before new work" if stage.get("requires_human_acceptance")
                else "no additional human acceptance gate")
        lines.append(f"  Gate: {gate}")
    for item in roadmap.get("items", []):
        if item.get("disposition") in {"needs_decision", "defer"}:
            lines.append(f"#{item['number']} [{item['disposition']}]: {item.get('reason', '')}")
            if item.get("question"):
                lines.append(f"  Question: {item['question']}")
    return lines


def _repository_human_status(snapshot: dict) -> str:
    spec = snapshot["spec"]
    executor = snapshot.get("executor", {})
    mode = executor.get("mode", spec.get("driver", {}).get("name", "unknown"))
    execution = mode + (f" [{executor['status']}]" if executor.get("status") else "")
    lines = [f"DutyLoop repository: {snapshot['status']}",
             f"Repository: {spec['github']['repository']}",
             f"Intake: {snapshot.get('intake', spec.get('intake', {})).get('mode', 'observe')}",
             f"Delivery: {spec['github']['delivery']}",
             f"Executor: {execution}",
             f"Calls: {snapshot['invocations']}/{spec['limits']['max_invocations']}"]
    lines.extend(_planning_human_lines(snapshot))
    if executor.get("host_driven"):
        lines.append("Desktop execution requires an active connected host; DutyLoop cannot wake it or stop its external tools.")
    if snapshot.get("reason"):
        lines.append(f"Reason: {snapshot['reason']}")
    attention = snapshot.get("attention", [])
    lines.append(f"Attention: {len(attention)}")
    for item in attention:
        identity = (f"#{item['issue_number']} " if item.get("issue_number") else
                    f"PR #{item['pr_number']} " if item.get("pr_number") else "")
        detail = item.get("required_action") or item.get("reason") or item.get("type", "Inspect state")
        if item.get("reason") and item["reason"] != detail:
            detail += f" — {item['reason']}"
        lines.append(f"{identity}{detail}")
    lines.extend(f"#{row['number']} [{row['state']}] {row['source']['title']}"
                 + (f" — {row['reason']}" if row.get("reason") else "") for row in snapshot.get("issues", []))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _configure_output_streams()
    arguments = _parser().parse_args(argv)
    try:
        result = _dispatch(arguments)
        if result is not None:
            print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps(error_payload(InterruptedError("Interrupted."))), file=sys.stderr)
        return 130
    except Exception as error:
        print(json.dumps(error_payload(error), ensure_ascii=False), file=sys.stderr)
        return 1
