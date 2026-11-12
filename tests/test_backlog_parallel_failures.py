"""Concurrent planning must retain the strongest process-cleanup failure."""

import json
import threading

import pytest

from dutyloop import backlog_runner, issues
from dutyloop.drivers import DriverResult
from dutyloop.store import Store
from dutyloop.workspace import GitCleanupError
import test_backlog_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


def test_protocol_failure_cannot_mask_sibling_git_cleanup_failure(batch, monkeypatch):
    project, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 2}
    issues.initialize(spec, state, github=github)
    git_waiting, protocol_settled, git_failed = (threading.Event() for _ in range(3))
    roles, lock = {}, threading.Lock()

    class Host:
        def run(self, request, should_stop):
            data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
            assert data["planning_operation"] == "assess"
            entries = data["backlog"]
            protocol = entries[0]["number"] == 1
            with lock:
                roles[str(request.cwd)] = protocol
            if protocol:
                assert git_waiting.wait(10), "The sibling never reached its Git identity check"
                return DriverResult(payload={"summary": "Invalid structured assessment"})
            return DriverResult(payload={
                "summary": "Inspect the complete chunk", "current_stage": "Correct documented behavior",
                "evidence": [{"source": "app.py", "observation": "The fixture needs value corrections"}],
                "items": [{"number": entry["number"], "source_sha256": entry["source_sha256"],
                           "assessment": "Correct the stated value", "dependencies": []} for entry in entries],
            })

    original_identity, original_save = backlog_runner.assert_identity, backlog_runner.Planner.save

    def identity(workspace, head, pointer):
        with lock:
            protocol = roles[str(workspace)]
        if not protocol:
            git_waiting.set()
            assert protocol_settled.wait(10), "The coordinator never settled the protocol failure"
            git_failed.set()
            raise GitCleanupError("Fixture Git process cleanup unconfirmed")
        return original_identity(workspace, head, pointer)

    def save(planner):
        original_save(planner)
        if any(chunk["status"] == "incomplete" and chunk["issue_numbers"][0] == 1
               for chunk in planner.state.get("chunks", [])):
            protocol_settled.set()

    monkeypatch.setattr(backlog_runner, "assert_identity", identity)
    monkeypatch.setattr(backlog_runner.Planner, "save", save)
    with pytest.raises(Exception) as raised:
        issues.run(state, driver=Host(), github=github, plan_only=True)

    result = issues.status(state)
    assert git_failed.is_set() and protocol_settled.is_set()
    assert isinstance(raised.value, GitCleanupError)
    assert result["status"] == "blocked" and result["reason"] == "git_cleanup_unconfirmed"
    assert result["invocations"] == 2
    assert all(call["state"] not in {"reserved", "running"} for call in result["calls"])
    assert result["planning"]["status"] == "incomplete" and result["planning"]["roadmap"] is None
    assert all(chunk["operation"] == "assess" and chunk["status"] == "incomplete"
               for chunk in result["planning"]["chunks"])
    assert all(Store(child).control()["runner_pid"] is None for child in result["planning"]["child_states"])
    assert (project / "app.py").read_text() == "value = 0\n" and github.writes == []
