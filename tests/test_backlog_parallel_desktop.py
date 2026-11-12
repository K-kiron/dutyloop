"""Real desktop mailbox overlap with simulated read-only hosts, never a live model."""

import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from dutyloop import backlog, drivers, issues
from dutyloop.desktop import DesktopQueue
from dutyloop.drivers import DriverRequest
from dutyloop.workspace import git
import test_backlog_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


@pytest.fixture
def desktop_backlog(batch, monkeypatch):
    project, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["driver"] = {"name": "desktop"}
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    spec["call_timeout_seconds"] = 45
    spec["limits"]["max_wall_seconds"] = 90
    monkeypatch.setattr(drivers, "_native_argv", lambda *_: pytest.fail("No model CLI may run"))
    initial = issues.initialize(spec, state, github=github)
    return project, state, github, initial


def start_coordinator(state, github):
    results, errors = [], []

    def run():
        try:
            results.append(issues.run(state, github=github, plan_only=True))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, errors


def claim(queue, worker, context, thread, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = queue.claim(worker, context)
        if job["status"] == "claimed":
            return job
        assert thread.is_alive(), "The coordinator exited before publishing the expected request"
        time.sleep(.01)
    raise AssertionError("The coordinator did not publish concurrent desktop work before the deadline")


def data(job):
    return json.loads(job["prompt"][job["prompt"].index('{"authority"'):])


def mailbox_rows(state):
    path = (state / "desktop.sqlite3").resolve()
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY created_at,request_id")]


def join_after_stop(thread, state):
    if thread.is_alive():
        issues.stop(state)
    thread.join(10)
    assert not thread.is_alive(), "The coordinator left a desktop phase running after stop"


def test_parallel_desktop_chunks_are_claimed_together_before_one_complete_synthesis(desktop_backlog):
    project, state, github, initial = desktop_backlog
    queue = DesktopQueue(state)
    thread, results, errors = start_coordinator(state, github)
    host = workflow.PortfolioHost(foundation=6)
    jobs, receipts, assessed = [], [], []
    try:
        # Hold every first-wave lease without submitting a result. A serial
        # producer cannot expose the second or third request in this interval.
        first = [claim(queue, f"host-{number}", f"context-{number}", thread) for number in range(3)]
        assert len({job["request_id"] for job in first}) == 3
        assert len({job["worker_id"] for job in first}) == 3
        assert len({job["context_id"] for job in first}) == 3
        assert len({job["cwd"] for job in first}) == 3
        assert all(data(job)["planning_operation"] == "assess" for job in first)
        rows = mailbox_rows(state)
        assert {row["request_id"] for row in rows if row["state"] == "claimed"} == {
            job["request_id"] for job in first}
        assert not any(row["state"] == "submitted" for row in rows)
        assert issues.status(state)["planning"]["roadmap"] is None

        def complete(job):
            supplied = data(job)
            operation = supplied["planning_operation"]
            assert job["phase"] == "discover" and "Inspect read-only" in job["instructions"]
            assert supplied["repository_base_sha"] == initial["base_sha"]
            assert git(Path(job["cwd"]), "rev-parse", "HEAD") == initial["base_sha"]
            assert (Path(job["cwd"]) / "app.py").read_text() == "value = 0\n"
            if operation == "assess":
                assert job["schema"] == backlog.ASSESSMENT_SCHEMA
                for entry in supplied["backlog"]:
                    assert entry["source"]["body"] == github.issues[entry["number"]]["body"]
                    assert entry["source"]["comments"] == github.issues[entry["number"]]["comments"]
                    assessed.append(entry["number"])
            else:
                assert operation == "roadmap" and job["schema"] == backlog.ROADMAP_SCHEMA
                assert sorted(assessed) == list(range(1, 7))
                assert not any(row["state"] in {"pending", "claimed"}
                               and row["request_id"] != job["request_id"] for row in mailbox_rows(state))
                assert sorted(item["number"] for note in supplied["assessments"] for item in note["items"]) == list(
                    range(1, 7))
            request = DriverRequest(job["phase"], job["prompt"], Path(job["cwd"]), job["schema"],
                                    Path(job["output_dir"]), 45)
            result = host.run(request, lambda: False)
            receipt = queue.submit(job["request_id"], job["token"], job["worker_id"], job["context_id"],
                                   result.payload, work_stopped=True)
            assert receipt["status"] == "submitted" and receipt["cleanup_scope"] == "host_declaration"
            jobs.append(job)
            receipts.append(receipt)

        for job in reversed(first):
            complete(job)
        deadline = time.monotonic() + 40
        while thread.is_alive() and time.monotonic() < deadline:
            job = queue.claim(f"later-host-{len(jobs)}", f"later-context-{len(jobs)}")
            if job["status"] == "claimed":
                complete(job)
            else:
                time.sleep(.01)
        thread.join(5)
        assert not thread.is_alive() and not errors
        result = results[0]
        assert result["planning"]["status"] == "ready"
        assert result["planning"]["coverage"] == {"total": 6, "assessed": list(range(1, 7)), "unassessed": []}
        child_states = {str(Path(job["output_dir"]).parents[1]) for job in jobs}
        assert set(result["planning"]["child_states"]) == child_states
        assert len(result["planning"]["chunks"]) == len(jobs)
        assert all(chunk["status"] == "completed" and chunk["child_state"] in child_states
                   for chunk in result["planning"]["chunks"])
        assert sorted(assessed) == list(range(1, 7))
        assert len([job for job in jobs if data(job)["planning_operation"] == "roadmap"]) == 1
        assert result["invocations"] == len(jobs) == len(receipts)
        assert len({job["cwd"] for job in jobs}) == len(jobs)
        assert all(call["cleanup_confirmed"] and call["cleanup_scope"] == "host_declaration"
                   and call["state"] == "finished" for call in result["calls"])
        assert all(row["plan"] is None for row in result["issues"])
        assert all(json.loads(row["result"])["work_stopped"] is True for row in mailbox_rows(state))
        assert git(project, "rev-parse", "HEAD") == initial["base_sha"]
        assert (project / "app.py").read_text() == "value = 0\n" and github.writes == []
    finally:
        join_after_stop(thread, state)


def test_stop_quarantines_every_claimed_parallel_desktop_chunk_and_joins(desktop_backlog):
    project, state, github, initial = desktop_backlog
    queue = DesktopQueue(state)
    thread, _results, errors = start_coordinator(state, github)
    try:
        claimed = [claim(queue, f"stopping-host-{number}", f"stopping-context-{number}", thread)
                   for number in range(3)]
        assert len({job["request_id"] for job in claimed}) == 3
        assert all(data(job)["planning_operation"] == "assess" for job in claimed)
        issues.stop(state)
        thread.join(10)
        assert not thread.is_alive(), "The stopped coordinator did not join all desktop driver waits"
        result = issues.status(state)
        assert result["status"] == "blocked" and result["reason"] == "worker_cleanup_unconfirmed"
        assert result["stop_requested"] and result["runner_pid"] is None
        assert errors and all(isinstance(error, RuntimeError) for error in errors)
        assert result["invocations"] == 3
        assert len(result["calls"]) == 3 and all(call["state"] == "unknown" for call in result["calls"])
        assert set(result["planning"]["child_states"]) == {
            str(Path(job["output_dir"]).parents[1]) for job in claimed}
        assert len(result["planning"]["chunks"]) == 3
        rows = {row["request_id"]: row for row in mailbox_rows(state)}
        for job in claimed:
            row = rows[job["request_id"]]
            evidence = json.loads(row["result"])
            assert row["state"] == "quarantined"
            assert evidence["cleanup_confirmed"] is False and evidence["work_stopped"] is None
            assert evidence["cleanup_scope"] == "host_declaration"
            assert data(json.loads(row["request"]))["planning_operation"] == "assess"
        assert queue.claim("replacement-host", "replacement-context") == {"status": "waiting"}
        assert result["planning"]["roadmap"] is None
        assert all(row["plan"] is None for row in result["issues"])
        assert git(project, "rev-parse", "HEAD") == initial["base_sha"]
        assert (project / "app.py").read_text() == "value = 0\n" and github.writes == []
    finally:
        join_after_stop(thread, state)
