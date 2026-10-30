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


def _probe_driver(name: str) -> dict:
    from .drivers import probe_driver

    return probe_driver(name)


def error_payload(error: Exception) -> dict:
    return {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}


def _reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Symlinks and reparse points are not allowed: {path}")


def start_background(state_dir: Path) -> dict:
    """Launch a runner without waiting; the engine enforces exclusive ownership."""
    state_dir = Path(state_dir).expanduser().absolute()
    for path in (state_dir, *state_dir.parents):
        _reject_link(path)
    state_dir = state_dir.resolve()
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
            [sys.executable, "-m", "dutyloop", "run", "--state", str(state_dir)],
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
    parser = argparse.ArgumentParser(prog="dutyloop", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
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
    doctor = commands.add_parser("doctor", help="Probe native CLI executable availability without logging in")
    doctor.add_argument("--driver", choices=("codex", "claude"))
    demo = commands.add_parser("demo", help="Run the deterministic local demonstration")
    demo.add_argument("--directory", type=Path, required=True)
    mcp = commands.add_parser("mcp", help="Serve the optional MCP control interface over stdio")
    mcp.add_argument("--root", type=Path, required=True)
    mcp.add_argument("--allow-project", type=Path, action="append", required=True)
    return parser


def _dispatch(arguments: argparse.Namespace) -> dict | None:
    command = arguments.command
    if command == "init":
        spec = json.loads(arguments.spec.read_text(encoding="utf-8-sig"))
        if not isinstance(spec, dict):
            raise ValueError("The mission spec must be a JSON object.")
        return _engine().initialize(spec, arguments.state)
    if command == "run":
        return _engine().run(arguments.state)
    if command == "start":
        return start_background(arguments.state)
    if command == "status":
        return _engine().status(arguments.state)
    if command == "stop":
        return _engine().request_stop(arguments.state)
    if command == "recover":
        return _engine().recover(arguments.state, acknowledge=arguments.acknowledge)
    if command == "doctor":
        names = [arguments.driver] if arguments.driver else ["codex", "claude"]
        return {"drivers": [_probe_driver(name) for name in names]}
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
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps(error_payload(InterruptedError("Interrupted."))), file=sys.stderr)
        return 130
    except Exception as error:
        print(json.dumps(error_payload(error), ensure_ascii=False), file=sys.stderr)
        return 1
