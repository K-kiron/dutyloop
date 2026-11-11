import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dutyloop import mcp_server

EXPECTED_TOOLS = {
    "mission_create", "mission_start", "mission_status", "mission_stop", "mission_events", "mission_recover",
    "issues_create", "issues_start", "issues_status", "issues_stop", "issues_events", "issues_recover", "issues_resolve",
    "repository_create", "repository_start", "repository_status", "repository_reconcile", "repository_stop",
    "repository_events", "repository_recover", "repository_resolve", "repository_attention", "repository_set_mode",
    "desktop_next", "desktop_submit", "desktop_cancel",
}


@pytest.fixture
def control(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    calls = []
    engine = SimpleNamespace(
        initialize=lambda spec, state: calls.append(("initialize", spec, state)) or {"status": "ready"},
        status=lambda state: calls.append(("status", state)) or {
            "status": "waiting", "project": str(project.resolve()), "driver": {"name": "codex"}
        },
        request_stop=lambda state: calls.append(("stop", state)) or {"stop_requested": True},
        events=lambda state, **options: calls.append(("events", state, options)) or {"events": [], "next_cursor": options["after_id"]},
    )
    monkeypatch.setattr(mcp_server, "_engine", lambda: engine)
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    return controller, project, calls


def test_create_only_initializes_and_keeps_explicit_project(control):
    controller, project, calls = control
    spec = {"mission": "Fix bugs", "verification": [["python", "-m", "unittest"]]}
    result = controller.mission_create("maintenance", spec)
    assert result == {"status": "ready"}
    assert [call[0] for call in calls] == ["initialize"]
    assert calls[0][1]["project"] == str(project.resolve())
    assert calls[0][2] == controller.root / "maintenance"
    assert "project" not in spec


def test_create_cannot_expand_project_allowlist(control, tmp_path):
    controller, _project, calls = control
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="allowlist"):
        controller.mission_create("maintenance", {"project": str(elsewhere)})
    assert calls == []


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", ".", "", "CAPS", "a--b", "con", "nul", "a" * 65])
def test_state_names_are_strict_slugs(control, name):
    controller, _project, calls = control
    with pytest.raises(ValueError, match="name"):
        controller.mission_status(name)
    assert calls == []


@pytest.mark.parametrize("driver", [{"name": "command", "argv": ["python"]}, {"name": "codex", "argv": ["python"]}, {"name": "other"}])
def test_mcp_rejects_custom_driver_and_argv(control, driver):
    controller, _project, calls = control
    with pytest.raises(ValueError, match="driver"):
        controller.mission_create("maintenance", {"driver": driver})
    assert calls == []


def test_multiple_projects_require_selection(tmp_path, monkeypatch):
    projects = [tmp_path / "one", tmp_path / "two"]
    for project in projects:
        project.mkdir()
    monkeypatch.setattr(mcp_server, "_engine", lambda: SimpleNamespace(initialize=lambda spec, state: spec))
    controller = mcp_server.ControlPlane(tmp_path / "states", projects)
    with pytest.raises(ValueError, match="project"):
        controller.mission_create("maintenance", {})
    assert controller.mission_create("maintenance", {"project": str(projects[1])})["project"] == str(projects[1].resolve())


def test_root_must_be_outside_projects(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match="outside"):
        mcp_server.ControlPlane(project / "states", [project])


def test_stop_only_requests_stop_without_waiting(control):
    controller, _project, calls = control
    assert controller.mission_stop("maintenance") == {"stop_requested": True}
    assert calls == [("status", controller.root / "maintenance"), ("stop", controller.root / "maintenance")]


def test_start_launches_separately(control, monkeypatch):
    controller, _project, calls = control
    started = []
    monkeypatch.setattr(mcp_server, "start_background", lambda state: started.append(state) or {"pid": 42})
    assert controller.mission_start("maintenance") == {"pid": 42}
    assert started == [controller.root / "maintenance"]
    assert calls == [("status", controller.root / "maintenance")]


@pytest.mark.parametrize("snapshot", [
    {"project": "outside", "driver": {"name": "codex"}},
    {"driver": {"name": "codex"}},
])
def test_existing_mission_cannot_bypass_project_allowlist(control, monkeypatch, snapshot):
    controller, _project, _calls = control
    monkeypatch.setattr(mcp_server, "_engine", lambda: SimpleNamespace(status=lambda state: snapshot))
    monkeypatch.setattr(mcp_server, "start_background", lambda state: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="allowlist"):
        controller.mission_start("maintenance")


def test_existing_custom_driver_cannot_be_started_through_mcp(control, monkeypatch):
    controller, project, _calls = control
    monkeypatch.setattr(mcp_server, "_engine", lambda: SimpleNamespace(status=lambda state: {
        "project": str(project), "driver": {"name": "command", "argv": ["python"]}
    }))
    monkeypatch.setattr(mcp_server, "start_background", lambda state: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="native"):
        controller.mission_start("maintenance")


def test_tool_errors_have_json_shape():
    def invalid():
        raise ValueError("Invalid name")

    assert mcp_server._tool_result(invalid) == {
        "ok": False,
        "error": {"type": "ValueError", "message": "Invalid name"},
    }


def test_events_checks_the_registered_project_before_reading(control):
    controller, _project, calls = control
    assert controller.mission_events("maintenance", after=4, limit=2) == {"events": [], "next_cursor": 4}
    assert calls == [("status", controller.root / "maintenance"),
                     ("events", controller.root / "maintenance", {"after_id": 4, "limit": 2})]


@pytest.mark.parametrize("name", ["opencode", "dsh"])
def test_mcp_uses_the_same_native_driver_registry(control, name):
    controller, _project, calls = control
    controller.mission_create("maintenance", {"driver": {"name": name}})
    assert calls[0][1]["driver"]["name"] == name


def test_real_mcp_control_creates_and_stops_without_starting_driver(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True, capture_output=True)
    (project / "health.txt").write_text("healthy\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "health.txt"], check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(project), "-c", "user.name=Fixture User", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Add health fixture",
    ], check=True, capture_output=True)
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    created = controller.mission_create("maintenance", {
        "mission": "Inspect health",
        "allowed_paths": ["."],
        "verification": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "limits": {"max_invocations": 1, "max_wall_seconds": 10},
        "driver": {"name": "codex"},
    })
    assert created["status"] == "ready"
    assert controller.mission_status("maintenance")["calls"] == []
    stopped = controller.mission_stop("maintenance")
    assert stopped["status"] == "stopped"
    assert stopped["invocations"] == 0
    with pytest.raises(ValueError, match="cannot restart"):
        controller.mission_start("maintenance")
    assert not (controller.root / "maintenance" / "runner.stdout.log").exists()


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="Optional MCP SDK is not installed")
def test_official_sdk_registers_tools_without_starting_mission(control):
    controller, project, calls = control
    server = mcp_server.create_server(controller.root, [project])
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    create = next(tool for tool in tools if tool.name == "mission_create")
    assert "authorization" in create.description.lower()
    assert calls == []


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="Optional MCP SDK is not installed")
def test_official_sdk_stdio_handshake_is_inert_and_returns_json_errors(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "states"
    source = Path(__file__).resolve().parents[1] / "src"

    async def communicate():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "dutyloop", "mcp", "--root", str(state_root), "--allow-project", str(project)],
            env={"PYTHONPATH": str(source)},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "DutyLoop"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
                result = await session.call_tool("mission_status", {"name": "../escape"})
                return json.loads(result.content[0].text)

    result = asyncio.run(communicate())
    assert result["ok"] is False
    assert result["error"]["type"] == "ValueError"
    assert not state_root.exists()
