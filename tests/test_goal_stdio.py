"""Offline finite-goal transport coverage with an explicitly simulated desktop host."""

import asyncio
import importlib.util
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from dutyloop.schemas import GOAL_INVESTIGATION_SCHEMA, GOAL_PLAN_SCHEMA, GOAL_REVIEW_SCHEMA, IMPLEMENT_SCHEMA


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="Optional MCP SDK is not installed")
def test_official_sdk_stdio_completes_goal_with_parallel_simulated_investigators(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project = tmp_path / "project"
    project.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args], check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Fixture User")
    git("config", "user.email", "fixture@example.invalid")
    (project / "health.txt").write_text("broken\n", encoding="utf-8")
    git("add", "health.txt")
    git("commit", "--quiet", "-m", "Add health fixture")
    original_head = git("rev-parse", "HEAD")
    original_branch = git("symbolic-ref", "HEAD")
    original_files = {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()}
    state_root = tmp_path / "states"
    state = state_root / "health-goal"
    source = Path(__file__).resolve().parents[1] / "src"
    common = {"name": "health-goal", "kind": "mission"}

    def worker(role):
        # These identities simulate distinct actual host contexts. This test
        # proves the mailbox contract, not real host isolation or permissions.
        return {**common, "worker_id": f"simulated-{role}", "context_id": f"simulated-{role}-context"}

    async def communicate():
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

                async def claim(identity, schema, phase):
                    job = await poll("desktop_next", identity, lambda value: value["status"] == "claimed")
                    assert job["phase"] == phase
                    assert job["schema"] == schema
                    assert job["worker_id"] == identity["worker_id"]
                    assert job["context_id"] == identity["context_id"]
                    assert Path(job["cwd"]).is_dir()
                    assert Path(job["cwd"]).resolve() != project.resolve()
                    return job

                async def submit(identity, job, payload):
                    receipt = await call("desktop_submit", {
                        **identity, "request_id": job["request_id"], "token": job["token"],
                        "payload": payload, "work_stopped": True,
                    })
                    assert receipt["status"] == "submitted"
                    assert receipt["cleanup_scope"] == "host_declaration"

                await session.initialize()
                created = False
                try:
                    snapshot = await call("mission_create", {"name": "health-goal", "spec": {
                        "mode": "goal", "mission": "Restore the fixture health marker and stop",
                        "goal_acceptance": ["The health marker contains healthy and the operator check passes"],
                        "allowed_paths": ["health.txt"],
                        "verification": [[sys.executable, "-B", "-c",
                            "from pathlib import Path; assert Path('health.txt').read_text() == 'healthy\\n'"]],
                        "team": {"max_workers": 2},
                        "limits": {"max_invocations": 5, "max_wall_seconds": 90},
                        "call_timeout_seconds": 60, "max_task_attempts": 1,
                        "driver": {"name": "desktop"},
                    }})
                    created = True
                    assert snapshot["status"] == "ready"
                    assert snapshot["invocations"] == 0
                    await call("mission_start", {"name": "health-goal"})
                    planner = worker("planner")
                    job = await claim(planner, GOAL_PLAN_SCHEMA, "discover")
                    await submit(planner, job, {
                        "status": "work", "summary": "Repair only the requested health marker",
                        "investigations": [
                            {"key": "behavior", "role": "behavior investigator",
                             "question": "What is the current marker?", "paths": ["health.txt"]},
                            {"key": "verification", "role": "verification investigator",
                             "question": "Which marker should the configured check accept?", "paths": ["health.txt"]},
                        ],
                        "tasks": [{"candidate": {
                            "key": "restore-health", "title": "Restore the health marker", "kind": "bugfix",
                            "why": "The fixture is broken", "priority": 5,
                            "evidence": [{"source": "health.txt", "observation": "The marker is broken"}],
                            "acceptance": ["The marker is healthy and the configured check passes"],
                        }, "depends_on": [], "blocked_reason": ""}],
                    })

                    first, second = worker("investigator-one"), worker("investigator-two")
                    one = await claim(first, GOAL_INVESTIGATION_SCHEMA, "discover")
                    # Hold the first claim while obtaining the second. Both
                    # jobs must be available before either result is returned.
                    two = await claim(second, GOAL_INVESTIGATION_SCHEMA, "discover")
                    assert one["request_id"] != two["request_id"]
                    assert one["context_id"] != two["context_id"]
                    assert Path(one["cwd"]).resolve() != Path(two["cwd"]).resolve()
                    assignments = [json.loads(item["prompt"].split("\n")[-1])["assignment"] for item in (one, two)]
                    assert {item["key"] for item in assignments} == {"behavior", "verification"}
                    assert len({item["role"] for item in assignments}) == 2
                    held = await call("mission_status", {"name": "health-goal"})
                    assert held["invocations"] == 3
                    assert len([item for item in held["calls"] if item["state"] != "finished"]) == 2
                    for identity, investigation in ((second, two), (first, one)):
                        assert (Path(investigation["cwd"]) / "health.txt").read_text() == "broken\n"
                        await submit(identity, investigation, {
                            "summary": "Inspected the assigned concern without editing",
                            "evidence": [{"source": "health.txt", "observation": "The marker is broken"}],
                            "recommendations": ["Change only the marker to healthy"],
                        })

                    implementer = worker("implementer")
                    implementation = await claim(implementer, IMPLEMENT_SCHEMA, "implement")
                    (Path(implementation["cwd"]) / "health.txt").write_text("healthy\n", encoding="utf-8")
                    await submit(implementer, implementation, {"summary": "Restored marker", "completed": True})
                    reviewer = worker("reviewer")
                    review = await claim(reviewer, GOAL_REVIEW_SCHEMA, "review")
                    assert review["context_id"] != implementation["context_id"]
                    assert (Path(review["cwd"]) / "health.txt").read_text() == "healthy\n"
                    await submit(reviewer, review, {
                        "accepted": True, "summary": "The checked change meets the entire finite goal",
                        "findings": [], "goal_completed": True,
                        "criteria": [{"criterion": 1, "met": True, "evidence": [
                            {"source": "health.txt", "observation": "The marker contains healthy"},
                        ]}],
                    })
                    snapshot = await poll("mission_status", {"name": "health-goal"}, lambda value: (
                        value["status"] == "completed" and value["runner_pid"] is None
                    ))
                    assert snapshot["reason"] == "goal_accepted"
                    assert snapshot["invocations"] == 5
                    assert [item["phase"] for item in snapshot["calls"]] == [
                        "discover", "discover", "discover", "implement", "review",
                    ]
                    assert all(item["state"] == "finished" for item in snapshot["calls"])
                    assert all(item["state"] == "finished" for item in snapshot["goal"]["investigations"])
                    completion = snapshot["goal"]["completion"]
                    assert completion["head"] == snapshot["accepted_head"] != original_head
                    assert completion["tree"] == git("rev-parse", f"{snapshot['accepted_head']}^{{tree}}")
                    assert completion["checks"][0]["returncode"] == 0
                    assert git("show", f"{snapshot['accepted_head']}:health.txt") == "healthy"
                    assert snapshot["tasks"][0]["state"] == "accepted"
                    assert snapshot["reported_cost_usd"] is None
                    assert snapshot["calls_without_cost"] == 5
                    assert await call("desktop_next", worker("after-completion")) == {
                        "status": "completed", "reason": "goal_accepted", "work_available": False,
                    }
                finally:
                    if created or (state / "mission.sqlite3").is_file():
                        await call("mission_stop", {"name": "health-goal"})
                        await poll("mission_status", {"name": "health-goal"}, lambda value: (
                            value["runner_pid"] is None
                            and all(item["state"] not in {"reserved", "running"} for item in value["calls"])
                        ), timeout=15)

    asyncio.run(asyncio.wait_for(communicate(), timeout=90))
    assert git("rev-parse", "HEAD") == original_head
    assert git("symbolic-ref", "HEAD") == original_branch
    assert git("status", "--porcelain") == ""
    assert {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()} == original_files
