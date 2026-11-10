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


def error_payload(error: Exception) -> dict:
    return {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}


def _reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Symlinks and reparse points are not allowed: {path}")


def start_background(state_dir: Path, *, issue_batch=False) -> dict:
    """Launch a runner without waiting; the engine enforces exclusive ownership."""
    state_dir = Path(state_dir).expanduser().absolute()
    for path in (state_dir, *state_dir.parents):
        _reject_link(path)
    state_dir = state_dir.resolve()
    if issue_batch:
        from . import issues
        snapshot = issues.status(state_dir)
    else:
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
            [sys.executable, "-m", "dutyloop", *(["issues"] if issue_batch else []), "run", "--state", str(state_dir)],
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
    backlog = commands.add_parser("issues", help="Plan and process a finite GitHub issue backlog")
    issue_commands = backlog.add_subparsers(dest="issue_command", required=True)
    for name in ("init", "run", "start", "status", "stop", "events", "recover", "resolve"):
        item = issue_commands.add_parser(name)
        item.add_argument("--state", type=Path, required=True)
        if name == "init":
            item.add_argument("--spec", type=Path, required=True)
        if name == "run":
            item.add_argument("--plan-only", action="store_true")
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
    configure.add_argument("--driver", choices=NATIVE_DRIVERS, default="codex")
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
    return parser


def _dispatch(arguments: argparse.Namespace) -> dict | str | None:
    command = arguments.command
    if command == "issues":
        from . import issues
        action = arguments.issue_command
        if action == "init":
            from .onboarding import read_spec
            return issues.initialize(read_spec(arguments.spec), arguments.state)
        if action == "run":
            return issues.run(arguments.state, plan_only=arguments.plan_only)
        if action == "start":
            return start_background(arguments.state, issue_batch=True)
        if action == "status":
            snapshot = issues.status(arguments.state)
            if not arguments.human:
                return snapshot
            lines = [f"DutyLoop issues: {snapshot['status']}",
                     f"Repository: {snapshot['spec']['github']['repository']}",
                     f"Delivery: {snapshot['spec']['github']['delivery']}",
                     f"Calls: {snapshot['invocations']}/{snapshot['spec']['limits']['max_invocations']}"]
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
    raise ValueError(f"Unknown command: {command}")


def main(argv: list[str] | None = None) -> int:
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
