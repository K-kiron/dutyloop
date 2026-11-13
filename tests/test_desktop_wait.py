"""Bounded desktop waits reduce host polling without relaxing handoff gates."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time

import pytest

from dutyloop import mcp_server
from dutyloop.desktop import DesktopQueue
from dutyloop.drivers import DriverRequest
from dutyloop.issue_config import validate_issue_spec
from dutyloop.issue_store import IssueStore
from dutyloop.workspace import git


@pytest.fixture
def desktop(tmp_path, request):
    project, state = tmp_path / "project", tmp_path / "states" / "work"
    project.mkdir()
    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "Fixture Maintainer")
    git(project, "config", "user.email", "fixture@example.invalid")
    git(project, "commit", "--allow-empty", "-m", "Add fixture")
    spec = validate_issue_spec({
        "mission": "Check desktop waits", "project": str(project), "allowed_paths": ["app.py"],
        "verification": [[sys.executable, "-c", "pass"]], "driver": {"name": "desktop"},
        "limits": {"max_invocations": 10, "max_wall_seconds": getattr(request, "param", 30)},
        "github": {"repository": "example/project", "delivery": "local"},
    }, state)
    IssueStore(state).create(spec, [], git(project, "rev-parse", "HEAD"))
    return mcp_server.ControlPlane(state.parent, [project]), state, project


def enqueue(state, project, phase="discover", output="output"):
    request = DriverRequest(phase, "Inspect the fixture", project, {"type": "object"}, state / output, 30)
    return DesktopQueue(state)._enqueue(request, "fixture-generation")


def test_one_desktop_wait_delivers_a_later_request(desktop):
    controller, state, project = desktop

    def later():
        time.sleep(.1)
        return enqueue(state, project)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(later)
        job = controller.desktop_next(state.name, "host", "context", wait_seconds=1)
        envelope = pending.result()
    assert job["status"] == "claimed"
    assert job["request_id"] == envelope["request_id"]
    assert controller.desktop_next(state.name, "host", "context") == job


@pytest.mark.parametrize("wait_seconds", [
    True, False, -1, 30.01, float("nan"), float("inf"), "1", None,
    pytest.param(10**400, id="oversized-integer"),
])
def test_invalid_wait_is_rejected_before_claiming(desktop, wait_seconds):
    controller, state, project = desktop
    enqueue(state, project)
    with pytest.raises(ValueError, match="wait_seconds"):
        controller.desktop_next(state.name, "host", "context", wait_seconds=wait_seconds)
    assert controller.desktop_next(state.name, "another", "fresh")["status"] == "claimed"


@pytest.mark.parametrize("status", ["stopped", "blocked", "limited", "completed", "unknown", "stop"])
def test_wait_returns_when_control_closes(desktop, status):
    controller, state, _ = desktop
    store = IssueStore(state)
    before = store.control()

    def close():
        time.sleep(.1)
        if status == "stop":
            store.stop()
        else:
            store.set_status(status, "fixture-control")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(close)
        job = controller.desktop_next(state.name, "host", "context", wait_seconds=1)
        pending.result()
    assert job["work_available"] is False
    assert job["status"] == ("stopped" if status == "stop" else status)
    assert store.control()["invocations"] == before["invocations"]
    assert store.control()["deadline"] == before["deadline"]
    assert not (state / "desktop.sqlite3").exists()


def test_empty_wait_is_bounded_and_default_remains_immediate(desktop):
    controller, state, _ = desktop
    before = IssueStore(state).control()
    assert controller.desktop_next(state.name, "host", "context") == {"status": "waiting"}
    started = time.monotonic()
    assert controller.desktop_next(state.name, "host", "context", wait_seconds=.15) == {"status": "waiting"}
    assert .12 <= time.monotonic() - started < 1
    assert IssueStore(state).control() == before
    assert not (state / "desktop.sqlite3").exists()


@pytest.mark.parametrize("desktop", [.2], indirect=True)
def test_control_deadline_bounds_the_wait_without_resetting_budget(desktop):
    controller, state, _ = desktop
    store = IssueStore(state)
    assert store.begin()
    before = store.control()
    started = time.monotonic()
    assert controller.desktop_next(state.name, "host", "context", wait_seconds=2)["work_available"] is False
    assert time.monotonic() - started < 1
    assert store.control() == before


def test_missing_state_does_not_wait_or_create_mailbox(desktop):
    controller, _, _ = desktop
    with pytest.raises(ValueError, match="No issue batch"):
        controller.desktop_next("missing", "host", "context", wait_seconds=1)
    assert not (controller.root / "missing").exists()


def test_wait_cannot_reuse_implementation_context_for_review(desktop):
    controller, state, project = desktop
    enqueue(state, project, "implement")
    job = controller.desktop_next(state.name, "implementer", "implementation-context")
    controller.desktop_submit(state.name, job["request_id"], job["token"],
                              "implementer", "implementation-context", {}, True)

    def later():
        time.sleep(.1)
        return enqueue(state, project, "review", "review-output")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(later)
        with pytest.raises(ValueError, match="fresh context"):
            controller.desktop_next(state.name, "implementer", "implementation-context", wait_seconds=1)
        review = pending.result()
    claimed = controller.desktop_next(state.name, "reviewer", "fresh-context", wait_seconds=30)
    assert claimed["request_id"] == review["request_id"]
    with pytest.raises(ValueError, match="already owns"):
        controller.desktop_next(state.name, "reviewer", "other-context", wait_seconds=1)


def test_concurrent_waits_preserve_single_context_ownership(desktop):
    controller, state, project = desktop
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(controller.desktop_next, state.name, "host", "context", "issues", 1)
                   for _ in range(2)]
        time.sleep(.1)
        envelope = enqueue(state, project)
        jobs = [future.result() for future in pending]
    assert jobs[0] == jobs[1]
    assert jobs[0]["request_id"] == envelope["request_id"]


def test_wait_rechecks_project_authority(desktop, tmp_path):
    controller, state, project = desktop
    other = tmp_path / "other"
    other.mkdir()

    def change_authority():
        time.sleep(.1)
        controller.projects = (other,)
        return enqueue(state, project)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(change_authority)
        with pytest.raises(ValueError, match="allowlist"):
            controller.desktop_next(state.name, "host", "context", wait_seconds=1)
        pending.result()
    controller.projects = (project,)
    assert controller.desktop_next(state.name, "fresh-worker", "fresh-context")["status"] == "claimed"


def test_mcp_exposes_bounded_wait_argument(desktop):
    pytest.importorskip("mcp")
    controller, _, project = desktop
    server = mcp_server.create_server(controller.root, [project])
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "desktop_next")
    assert tool.inputSchema["properties"]["wait_seconds"]["default"] == 0
    assert tool.inputSchema["properties"]["wait_seconds"]["minimum"] == 0
    assert tool.inputSchema["properties"]["wait_seconds"]["maximum"] == 30
    assert "wait_seconds=20" in tool.description


def test_mcp_forwards_wait_to_deliver_a_later_request(desktop):
    pytest.importorskip("mcp")
    controller, state, project = desktop
    server = mcp_server.create_server(controller.root, [project])

    def later():
        time.sleep(.1)
        return enqueue(state, project)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(later)
        result = asyncio.run(server.call_tool("desktop_next", {
            "name": state.name, "worker_id": "host", "context_id": "context", "wait_seconds": 1,
        }))
        job = json.loads(result[0].text)
        envelope = pending.result()
    assert job["status"] == "claimed"
    assert job["request_id"] == envelope["request_id"]


def test_mcp_stop_remains_responsive_during_wait(desktop):
    pytest.importorskip("mcp")
    controller, state, project = desktop
    server = mcp_server.create_server(controller.root, [project])

    async def communicate():
        async def stop_later():
            await asyncio.sleep(.1)
            return await server.call_tool("issues_stop", {"name": state.name})

        return await asyncio.gather(server.call_tool("desktop_next", {
            "name": state.name, "worker_id": "host", "context_id": "context", "wait_seconds": .4,
        }), stop_later())

    waiting, _ = asyncio.run(communicate())
    assert json.loads(waiting[0].text)["status"] == "stopped"


def test_cancelled_mcp_wait_cannot_claim_later_work(desktop):
    pytest.importorskip("mcp")
    controller, state, project = desktop
    server = mcp_server.create_server(controller.root, [project])

    async def communicate():
        pending = asyncio.create_task(server.call_tool("desktop_next", {
            "name": state.name, "worker_id": "cancelled-host", "context_id": "cancelled-context",
            "wait_seconds": 1,
        }))
        await asyncio.sleep(.1)
        assert not pending.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        envelope = enqueue(state, project)
        await asyncio.sleep(.15)
        return envelope

    envelope = asyncio.run(communicate())
    job = controller.desktop_next(state.name, "replacement-host", "replacement-context")
    assert job["status"] == "claimed"
    assert job["request_id"] == envelope["request_id"]


def test_mcp_wait_rejects_boolean_before_claiming(desktop):
    pytest.importorskip("mcp")
    from mcp.server.fastmcp.exceptions import ToolError

    controller, state, project = desktop
    enqueue(state, project)
    server = mcp_server.create_server(controller.root, [project])
    with pytest.raises(ToolError, match="wait_seconds"):
        asyncio.run(server.call_tool("desktop_next", {
            "name": state.name, "worker_id": "host", "context_id": "context", "wait_seconds": True,
        }))
    assert controller.desktop_next(state.name, "another", "fresh")["status"] == "claimed"
