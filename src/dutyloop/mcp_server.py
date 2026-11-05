"""Optional MCP control plane with operator-configured project boundaries."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from .cli import _reject_link, error_payload, start_background
from .drivers import NATIVE_DRIVERS

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
        if not self._native_driver(driver):
            raise ValueError("MCP driver configuration accepts only supported native drivers and an optional model.")
        supplied["project"] = str(project)
        return _engine().initialize(supplied, state)

    def mission_start(self, name: str) -> dict:
        """Launch a background runner; this may execute the mission's authorized verification commands."""
        state = self._state(name)
        snapshot = self._authorized_snapshot(state)
        if not self._native_driver(snapshot.get("driver")):
            raise ValueError("MCP can start only stored missions using a supported native driver.")
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
        """Create without starting. Supplying verification argv is operator authorization to execute those commands on start. Projects must exactly match the operator's allowlist; only native CLI drivers are permitted."""
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

    return server
