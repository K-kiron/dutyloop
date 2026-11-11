"""Optional MCP control plane with operator-configured project boundaries."""

from __future__ import annotations

import copy
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .cli import _finite_issue_state, _mission_state, _reject_link, error_payload, start_background
from .drivers import NATIVE_DRIVERS
from .store import runner_lock

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def _engine():
    from . import engine

    return engine


def _safe_path(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    for parent in (path, *path.parents):
        _reject_link(parent)
    return path.resolve()


class ControlPlane:
    """Control only missions beneath one state root and exact registered projects."""

    def __init__(self, root: Path, allow_projects: Iterable[Path]):
        self.root = _safe_path(root)
        if self.root == Path(self.root.anchor):
            raise ValueError("The state root cannot be a filesystem root.")
        self.projects = tuple(dict.fromkeys(_safe_path(project) for project in allow_projects))
        if not self.projects:
            raise ValueError("At least one project must be explicitly registered in the allowlist.")
        for project in self.projects:
            if not project.is_dir():
                raise ValueError(f"The allowlist project is not a directory: {project}")
            if self.root.is_relative_to(project):
                raise ValueError("The state root must be outside every registered project.")

    def _state(self, name: str) -> Path:
        if not isinstance(name, str) or len(name) > 64 or not _SLUG.fullmatch(name) or name in _RESERVED_NAMES:
            raise ValueError("Mission name must be a lowercase slug of at most 64 characters, excluding device names.")
        state = _safe_path(self.root / name)
        if state.parent != self.root:
            raise ValueError("Mission name must identify an immediate child of the state root.")
        if any(state.is_relative_to(project) or project.is_relative_to(state) for project in self.projects):
            raise ValueError("Mission state must remain outside the project allowlist.")
        return state

    def _authorized_snapshot(self, state: Path) -> dict:
        self._unambiguous(state)
        _mission_state(state)
        snapshot = _engine().status(state)
        project = snapshot.get("project")
        if not isinstance(project, str) or _safe_path(project) not in self.projects:
            raise ValueError("The stored mission project must exactly match a registered allowlist root.")
        return snapshot

    @staticmethod
    def _native_driver(driver: Any) -> bool:
        return (
            isinstance(driver, dict)
            and not (set(driver) - {"name", "model"})
            and isinstance(driver.get("name", "codex"), str)
            and driver.get("name", "codex") in NATIVE_DRIVERS
            and (driver.get("model") is None or isinstance(driver["model"], str))
        )

    def mission_create(self, name: str, spec: dict) -> dict:
        """Initialize only; verification argv is operator authorization to execute those commands when started."""
        state = self._state(name)
        supplied = self._project_spec(spec)
        state.mkdir(parents=True, exist_ok=True)
        with runner_lock(state):
            if (state / "issues.sqlite3").exists():
                raise ValueError("This name already belongs to an issue batch")
            return _engine().initialize(supplied, state)

    def _project_spec(self, spec: dict) -> dict:
        if not isinstance(spec, dict):
            raise ValueError("The mission spec must be an object.")
        supplied = copy.deepcopy(spec)
        selected = supplied.get("project")
        if selected is None:
            if len(self.projects) != 1:
                raise ValueError("Select one explicitly registered project when the allowlist contains several projects.")
            project = self.projects[0]
        else:
            if not isinstance(selected, str) or not selected:
                raise ValueError("The selected project must be an allowlist root path.")
            project = _safe_path(selected)
            if project not in self.projects:
                raise ValueError("The selected project must exactly match a registered allowlist root.")
        driver = supplied.get("driver", {"name": "codex"})
        if not self._native_driver(driver) and driver != {"name": "desktop"}:
            raise ValueError("MCP driver configuration accepts native drivers or desktop; custom commands are not permitted.")
        supplied["project"] = str(project)
        return supplied

    def mission_start(self, name: str) -> dict:
        """Launch a background runner; this may execute the mission's authorized verification commands."""
        state = self._state(name)
        snapshot = self._authorized_snapshot(state)
        if not self._native_driver(snapshot.get("driver")) and snapshot.get("driver") != {"name": "desktop"}:
            raise ValueError("MCP can start only stored missions using a supported native driver or desktop.")
        return start_background(state)

    def mission_status(self, name: str) -> dict:
        """Read state without starting or resuming a mission."""
        return self._authorized_snapshot(self._state(name))

    def mission_stop(self, name: str) -> dict:
        """Request cancellation and return immediately; inspect status for confirmed termination."""
        state = self._state(name)
        self._authorized_snapshot(state)
        return _engine().request_stop(state)

    def mission_events(self, name: str, after: int = 0, limit: int = 20) -> dict:
        """Read audit events without starting, recovering or changing a mission."""
        state = self._state(name)
        self._authorized_snapshot(state)
        return _engine().events(state, after_id=after, limit=limit)

    def mission_recover(self, name: str, acknowledge: bool = False) -> dict:
        state = self._state(name)
        self._authorized_snapshot(state)
        return _engine().recover(state, acknowledge=acknowledge)

    def _issues_snapshot(self, state: Path) -> dict:
        from . import issues
        self._unambiguous(state)
        _finite_issue_state(state)
        snapshot = issues.status(state)
        spec = snapshot["spec"]
        if _safe_path(spec["project"]) not in self.projects:
            raise ValueError("The stored issue project must exactly match a registered allowlist root.")
        if not self._native_driver(spec["driver"]) and spec["driver"] != {"name": "desktop"}:
            raise ValueError("MCP accepts native drivers or desktop, not custom commands.")
        return snapshot

    @staticmethod
    def _unambiguous(state):
        if (state / "mission.sqlite3").exists() and (state / "issues.sqlite3").exists():
            raise ValueError("Ambiguous state contains both mission and issue databases; inspect it before continuing")

    def issues_create(self, name: str, spec: dict) -> dict:
        from . import issues
        if not isinstance(spec, dict):
            raise ValueError("Issue spec must be an object")
        supplied = copy.deepcopy(spec)
        supplied.setdefault("driver", {"name": "desktop"})
        state = self._state(name)
        supplied = self._project_spec(supplied)
        state.mkdir(parents=True, exist_ok=True)
        with runner_lock(state):
            if (state / "mission.sqlite3").exists():
                raise ValueError("This name already belongs to a mission")
            _finite_issue_state(state)
            return issues.initialize(supplied, state)

    def _repository_snapshot(self, state: Path) -> dict:
        from . import repository
        self._unambiguous(state)
        snapshot = repository.status(state)
        spec = snapshot["spec"]
        if _safe_path(spec["project"]) not in self.projects:
            raise ValueError("The stored repository project must exactly match a registered allowlist root.")
        if not self._native_driver(spec["driver"]) and spec["driver"] != {"name": "desktop"}:
            raise ValueError("MCP accepts native drivers or desktop, not custom commands.")
        return snapshot

    def repository_create(self, name: str, spec: dict) -> dict:
        """Initialize observation without launching models or publishing GitHub changes."""
        from . import repository
        if not isinstance(spec, dict):
            raise ValueError("Repository spec must be an object")
        supplied = copy.deepcopy(spec)
        supplied.setdefault("driver", {"name": "desktop"})
        state = self._state(name)
        supplied = self._project_spec(supplied)
        state.mkdir(parents=True, exist_ok=True)
        with runner_lock(state):
            if (state / "mission.sqlite3").exists() or (state / "issues.sqlite3").exists():
                raise ValueError("This name already belongs to an initialized mission, issue batch, or repository")
            return repository.initialize(supplied, state)

    def repository_start(self, name: str) -> dict:
        state = self._state(name)
        self._repository_snapshot(state)
        return start_background(state, repository=True)

    def repository_status(self, name: str) -> dict:
        return self._repository_snapshot(self._state(name))

    def repository_reconcile(self, name: str) -> dict:
        """Refresh GitHub observations using GET and local state only."""
        from . import repository
        state = self._state(name)
        self._repository_snapshot(state)
        return repository.reconcile(state)

    def repository_stop(self, name: str) -> dict:
        from . import repository
        state = self._state(name)
        self._repository_snapshot(state)
        return repository.stop(state)

    def repository_events(self, name: str, after: int = 0, limit: int = 20) -> dict:
        from . import repository
        state = self._state(name)
        self._repository_snapshot(state)
        return repository.events(state, after_id=after, limit=limit)

    def repository_recover(self, name: str, acknowledge: bool = False) -> dict:
        from . import repository
        state = self._state(name)
        self._repository_snapshot(state)
        return repository.recover(state, acknowledge=acknowledge)

    def repository_resolve(self, name: str, number: int, decision: str) -> dict:
        from . import repository
        state = self._state(name)
        self._repository_snapshot(state)
        return repository.resolve(state, number, decision)

    def repository_attention(self, name: str) -> dict:
        snapshot = self.repository_status(name)
        return {"attention": snapshot.get("attention", [])}

    def repository_set_mode(self, name: str, mode: str) -> dict:
        from . import repository
        state = self._state(name)
        self._repository_snapshot(state)
        return repository.set_mode(state, mode)

    def issues_start(self, name: str, plan_only: bool = False) -> dict:
        state = self._state(name)
        self._issues_snapshot(state)
        if type(plan_only) is not bool:
            raise ValueError("plan_only must be boolean")
        return start_background(state, issue_batch=True, plan_only=True) if plan_only else start_background(state, issue_batch=True)

    def issues_status(self, name: str) -> dict:
        return self._issues_snapshot(self._state(name))

    def issues_stop(self, name: str) -> dict:
        from . import issues
        state = self._state(name)
        self._issues_snapshot(state)
        return issues.stop(state)

    def issues_events(self, name: str, after: int = 0, limit: int = 20) -> dict:
        from . import issues
        state = self._state(name)
        self._issues_snapshot(state)
        return issues.events(state, after_id=after, limit=limit)

    def issues_recover(self, name: str, acknowledge: bool = False) -> dict:
        from . import issues
        state = self._state(name)
        self._issues_snapshot(state)
        return issues.recover(state, acknowledge=acknowledge)

    def issues_resolve(self, name: str, number: int, decision: str) -> dict:
        from . import issues
        state = self._state(name)
        self._issues_snapshot(state)
        return issues.resolve(state, number, decision)

    def _desktop_state(self, name: str, kind: str):
        state = self._state(name)
        if kind == "issues":
            from . import repository
            self._unambiguous(state)
            snapshot = self._repository_snapshot(state) if repository.is_repository(state) else self._issues_snapshot(state)
        elif kind == "repository":
            snapshot = self._repository_snapshot(state)
        elif kind == "mission":
            snapshot = self._authorized_snapshot(state)
        else:
            raise ValueError("kind must be issues, repository or mission")
        spec = snapshot.get("spec", snapshot)
        if spec["driver"] != {"name": "desktop"}:
            raise ValueError("Desktop work tools require driver.name=desktop")
        return state, snapshot

    @staticmethod
    def _closed(snapshot):
        return (snapshot["status"] in {"completed", "limited", "blocked", "stopped"}
                or snapshot.get("stop_requested")
                or snapshot.get("deadline") is not None and snapshot["deadline"] <= time.time())

    def desktop_next(self, name: str, worker_id: str, context_id: str, kind: str = "issues") -> dict:
        from .desktop import DesktopQueue
        state, snapshot = self._desktop_state(name, kind)
        if self._closed(snapshot):
            return {"status": snapshot["status"], "reason": snapshot.get("reason"), "work_available": False}
        return DesktopQueue(state).claim(worker_id, context_id)

    def desktop_submit(self, name: str, request_id: str, token: str, worker_id: str,
                       context_id: str, payload: dict, work_stopped: bool, kind: str = "issues") -> dict:
        from .desktop import DesktopQueue
        state, snapshot = self._desktop_state(name, kind)
        if self._closed(snapshot) and not (state / "desktop.sqlite3").exists():
            raise ValueError("Batch is stopped, blocked, completed or exhausted; stop host tools and cancel retained work")
        return DesktopQueue(state).submit(request_id, token, worker_id, context_id, payload, work_stopped,
                                          accept_new=not self._closed(snapshot))

    def desktop_cancel(self, name: str, request_id: str, token: str, worker_id: str,
                       context_id: str, work_stopped: bool, kind: str = "issues") -> dict:
        from .desktop import DesktopQueue
        state, _ = self._desktop_state(name, kind)
        return DesktopQueue(state).cancel(request_id, token, worker_id, context_id, work_stopped)


def desktop_configuration(client: str, root: Path, projects: Iterable[Path]) -> str:
    """Render connection settings only. Never modify the host's configuration."""
    controller = ControlPlane(root, projects)
    args = ["-m", "dutyloop", "mcp", "--root", str(controller.root)]
    for project in controller.projects:
        args.extend(["--allow-project", str(project)])
    if client == "claude":
        return json.dumps({"mcpServers": {"dutyloop": {"command": sys.executable, "args": args}}}, indent=2)
    if client == "codex":
        return ("[mcp_servers.dutyloop]\ncommand = " + json.dumps(sys.executable, ensure_ascii=False)
                + "\nargs = " + json.dumps(args, ensure_ascii=False) + "\ntool_timeout_sec = 60\n")
    raise ValueError("client must be codex or claude")


def _tool_result(action: Callable[[], dict]) -> dict:
    try:
        return action()
    except Exception as error:
        return error_payload(error)


def create_server(root: Path, allow_projects: Iterable[Path]):
    """Build the official SDK server without starting a mission or opening credentials."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError("MCP support is optional; install dutyloop[mcp] to enable this command.") from error

    controller = ControlPlane(root, allow_projects)
    server = FastMCP("DutyLoop")

    @server.tool()
    def mission_create(name: str, spec: dict[str, Any]) -> dict:
        """Create without starting. Verification argv is authorization to execute those commands on start. Projects must exactly match the allowlist; native or desktop drivers only."""
        return _tool_result(lambda: controller.mission_create(name, spec))

    @server.tool()
    def mission_start(name: str) -> dict:
        """Start a named mission in the background, including its operator-authorized verification commands."""
        return _tool_result(lambda: controller.mission_start(name))

    @server.tool()
    def mission_status(name: str) -> dict:
        """Read a named mission's current state without starting work."""
        return _tool_result(lambda: controller.mission_status(name))

    @server.tool()
    def mission_stop(name: str) -> dict:
        """Request asynchronous cancellation; poll status to learn whether cleanup has completed."""
        return _tool_result(lambda: controller.mission_stop(name))

    @server.tool()
    def mission_events(name: str, after: int = 0, limit: int = 20) -> dict:
        """Read ordered audit events after a cursor without starting or recovering work."""
        return _tool_result(lambda: controller.mission_events(name, after, limit))

    @server.tool()
    def mission_recover(name: str, acknowledge: bool = False) -> dict:
        """Recover only after inspecting retained work and stopping orphaned host tools. Unknown calls stay charged."""
        return _tool_result(lambda: controller.mission_recover(name, acknowledge))

    @server.tool()
    def issues_create(name: str, spec: dict[str, Any]) -> dict:
        """Snapshot open issues using read-only GitHub access; default to desktop execution. Verification and optional delivery settings are operator authorization on start."""
        return _tool_result(lambda: controller.issues_create(name, spec))

    @server.tool()
    def issues_start(name: str, plan_only: bool = False) -> dict:
        """Start the backlog coordinator, optionally planning only. Desktop mode waits for this host to claim and complete phases; it invokes no model CLI."""
        return _tool_result(lambda: controller.issues_start(name, plan_only))

    @server.tool()
    def issues_status(name: str) -> dict:
        """Read backlog progress and decisions without starting work."""
        return _tool_result(lambda: controller.issues_status(name))

    @server.tool()
    def issues_stop(name: str) -> dict:
        """Stop future backlog dispatch. The host must separately stop its own tools and subagents; active work may remain unknown."""
        return _tool_result(lambda: controller.issues_stop(name))

    @server.tool()
    def issues_events(name: str, after: int = 0, limit: int = 20) -> dict:
        """Read ordered issue audit events after a cursor without starting work."""
        return _tool_result(lambda: controller.issues_events(name, after, limit))

    @server.tool()
    def issues_recover(name: str, acknowledge: bool = False) -> dict:
        """Explicit read-only reconciliation after the operator inspects work and stops orphaned host tools; never replay uncertain work."""
        return _tool_result(lambda: controller.issues_recover(name, acknowledge))

    @server.tool()
    def issues_resolve(name: str, number: int, decision: str) -> dict:
        """Record an operator decision for replanning without resetting limits or publishing."""
        return _tool_result(lambda: controller.issues_resolve(name, number, decision))

    @server.tool()
    def repository_create(name: str, spec: dict[str, Any]) -> dict:
        """Create ongoing repository observation with read-only GitHub access; default to desktop execution and observe intake. Creation does not launch models or publish. Projects must exactly match the allowlist; custom commands are forbidden."""
        return _tool_result(lambda: controller.repository_create(name, spec))

    @server.tool()
    def repository_start(name: str) -> dict:
        """Start the repository coordinator in the background. Native execution uses configured limits; desktop execution needs an active connected host and cannot wake it."""
        return _tool_result(lambda: controller.repository_start(name))

    @server.tool()
    def repository_status(name: str) -> dict:
        """Read repository sources, task attempts, attention, receipts and executor capability without starting or reconciling work."""
        return _tool_result(lambda: controller.repository_status(name))

    @server.tool()
    def repository_reconcile(name: str) -> dict:
        """Refresh repository observations using GitHub GET requests and local state only; never launch a model or publish GitHub changes."""
        return _tool_result(lambda: controller.repository_reconcile(name))

    @server.tool()
    def repository_stop(name: str) -> dict:
        """Stop future repository dispatch without resetting limits. Desktop hosts must separately stop their external tools and subagents."""
        return _tool_result(lambda: controller.repository_stop(name))

    @server.tool()
    def repository_events(name: str, after: int = 0, limit: int = 20) -> dict:
        """Read ordered repository audit events after a durable cursor without starting or reconciling work."""
        return _tool_result(lambda: controller.repository_events(name, after, limit))

    @server.tool()
    def repository_recover(name: str, acknowledge: bool = False) -> dict:
        """Inspect retained repository work; acknowledgement requires the operator to stop orphaned tools first and never resets limits or stop authority."""
        return _tool_result(lambda: controller.repository_recover(name, acknowledge))

    @server.tool()
    def repository_resolve(name: str, number: int, decision: str) -> dict:
        """Record an operator decision for repository work without resetting limits or publishing."""
        return _tool_result(lambda: controller.repository_resolve(name, number, decision))

    @server.tool()
    def repository_attention(name: str) -> dict:
        """Read the structured repository attention list without reconciling or changing work."""
        return _tool_result(lambda: controller.repository_attention(name))

    @server.tool()
    def repository_set_mode(name: str, mode: str) -> dict:
        """Persist and audit observe or auto intake authority without resetting limits or a stop request. Auto permits eligible repository execution on subsequent runs."""
        return _tool_result(lambda: controller.repository_set_mode(name, mode))

    @server.tool()
    def desktop_next(name: str, worker_id: str, context_id: str, kind: str = "issues") -> dict:
        """Claim one desktop phase. Follow its exact cwd, prompt and schema. Review needs a fresh reviewer context; IDs are self-reported. Poll waiting responses only while the host task is active."""
        return _tool_result(lambda: controller.desktop_next(name, worker_id, context_id, kind))

    @server.tool()
    def desktop_submit(name: str, request_id: str, token: str, worker_id: str, context_id: str,
                       payload: dict[str, Any], work_stopped: bool, kind: str = "issues") -> dict:
        """Submit structured results only after all tools/subagents for this phase have stopped. work_stopped is a cooperative host declaration, not OS containment. DutyLoop still runs acceptance checks."""
        return _tool_result(lambda: controller.desktop_submit(name, request_id, token, worker_id,
                                                              context_id, payload, work_stopped, kind))

    @server.tool()
    def desktop_cancel(name: str, request_id: str, token: str, worker_id: str, context_id: str,
                       work_stopped: bool, kind: str = "issues") -> dict:
        """Acknowledge cancellation only after stopping host tools. Retain expired/unknown work for explicit recovery; do not reassign it."""
        return _tool_result(lambda: controller.desktop_cancel(name, request_id, token, worker_id,
                                                              context_id, work_stopped, kind))

    return server
