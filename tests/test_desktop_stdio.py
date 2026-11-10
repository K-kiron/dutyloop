"""Offline MCP transport coverage, independent of any live desktop host login."""

import asyncio
import importlib.util
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from dutyloop.schemas import DISCOVERY_SCHEMA


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="Optional MCP SDK is not installed")
def test_official_sdk_stdio_completes_a_desktop_discovery_handoff(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project = tmp_path / "project"
    project.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args], check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()

    git("init", "--quiet")
    health = project / "health.txt"
    health.write_text("healthy\n", encoding="utf-8")
    git("add", "health.txt")
    git("-c", "user.name=Fixture User", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Add health fixture")
    original_head = git("rev-parse", "HEAD")
    original_branch = git("symbolic-ref", "HEAD")
    original_files = {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()}
    state_root = tmp_path / "states"
    state = state_root / "maintenance"
    source = Path(__file__).resolve().parents[1] / "src"
    worker = {"name": "maintenance", "kind": "mission", "worker_id": "offline-worker", "context_id": "discovery-context"}

    async def communicate():
        # The background coordinator changes cwd to its state directory, so it
        # must inherit the source path from the real stdio server's environment.
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "dutyloop", "mcp", "--root", str(state_root), "--allow-project", str(project)],
            env={"PYTHONPATH": str(source)},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, result
                    value = json.loads(result.content[0].text)
                    assert value.get("ok") is not False, value
                    return value

                async def poll(name, arguments, predicate, timeout=20):
                    deadline = asyncio.get_running_loop().time() + timeout
                    while True:
                        value = await call(name, arguments)
                        if predicate(value):
                            return value
                        assert asyncio.get_running_loop().time() < deadline, value
                        await asyncio.sleep(.05)

                initialized = await session.initialize()
                assert initialized.serverInfo.name == "DutyLoop"
                created = False
                try:
                    snapshot = await call("mission_create", {"name": "maintenance", "spec": {
                        "mission": "Inspect health without changing files",
                        "allowed_paths": ["."],
                        "verification": [[sys.executable, "-c", "raise SystemExit(0)"]],
                        "limits": {"max_invocations": 1, "max_wall_seconds": 90},
                        "call_timeout_seconds": 60,
                        "poll_seconds": .05,
                        "driver": {"name": "desktop"},
                    }})
                    created = True
                    assert snapshot["status"] == "ready"
                    assert snapshot["invocations"] == 0
                    assert snapshot["calls"] == []
                    assert not (state / "runner.stdout.log").exists()

                    started = await call("mission_start", {"name": "maintenance"})
                    assert started["status"] == "starting"
                    assert started["pid"] > 0
                    job = await poll("desktop_next", worker, lambda value: value["status"] == "claimed")
                    assert job["phase"] == "discover"
                    assert job["schema"] == DISCOVERY_SCHEMA
                    assert job["worker_id"] == worker["worker_id"]
                    assert job["context_id"] == worker["context_id"]
                    assert Path(job["cwd"]).is_dir()
                    assert Path(job["cwd"]).resolve() != project.resolve()
                    assert job["prompt"]
                    assert job["token"]

                    receipt = await call("desktop_submit", {
                        **worker, "request_id": job["request_id"], "token": job["token"],
                        "payload": {"status": "wait", "summary": "No candidate", "candidates": []},
                        "work_stopped": True,
                    })
                    assert receipt["status"] == "submitted"
                    assert receipt["request_id"] == job["request_id"]
                    assert receipt["cleanup_scope"] == "host_declaration"
                    assert "cooperative" in receipt["cleanup_evidence"]
                    snapshot = await poll("mission_status", {"name": "maintenance"}, lambda value: (
                        value["status"] == "limited" and value["runner_pid"] is None
                    ))
                    assert snapshot["reason"] == "invocation_limit"
                    assert snapshot["invocations"] == 1
                    assert snapshot["driver"] == {"name": "desktop"}
                    assert snapshot["accepted_head"] == original_head
                    assert snapshot["tasks"] == []
                    assert snapshot["reported_cost_usd"] is None
                    assert snapshot["calls_without_cost"] == 1
                    [invocation] = snapshot["calls"]
                    assert invocation["phase"] == "discover"
                    assert invocation["state"] == "finished"
                    assert invocation["returncode"] == 0
                    assert invocation["cleanup_confirmed"] is True
                    assert invocation["cleanup_scope"] == "host_declaration"
                    assert invocation["cost_usd"] is None
                    assert invocation["usage"] == {}
                finally:
                    if created or (state / "mission.sqlite3").is_file():
                        await call("mission_stop", {"name": "maintenance"})
                        await poll("mission_status", {"name": "maintenance"}, lambda value: (
                            value["status"] in {"stopped", "limited", "blocked", "completed"}
                            and value["runner_pid"] is None
                            and all(item["state"] not in {"reserved", "running"} for item in value["calls"])
                        ), timeout=15)

    asyncio.run(communicate())
    assert git("rev-parse", "HEAD") == original_head
    assert git("symbolic-ref", "HEAD") == original_branch
    assert git("status", "--porcelain") == ""
    assert {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()} == original_files
