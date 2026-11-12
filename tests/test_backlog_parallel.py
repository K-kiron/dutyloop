"""Bounded concurrent, read-only backlog investigation contracts."""

import copy
import json
from pathlib import Path
import threading

import pytest

from dutyloop import backlog_runner, issues, workspace
from dutyloop.drivers import DriverResult
from dutyloop.issue_store import IssueStore
from dutyloop.store import Store
import test_backlog_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


class ConcurrentHost(workflow.PortfolioHost):
    def __init__(self, state, workers=3):
        super().__init__(foundation=6)
        self.state = state
        self.lock = threading.Lock()
        self.workers = workers
        self.barrier = threading.Barrier(workers, timeout=10)
        self.release = [threading.Event() for _ in range(workers - 1)]
        self.first_wave_finished = threading.Event()
        self.active = 0
        self.max_active = 0
        self.entered = []
        self.completed = []
        self.investigation_cwds = []
        self.observed_roadmaps = []
        self.git_controls = []
        self.request_bases = []
        self.synthesis_active = []

    def run(self, request, should_stop):
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        with self.lock:
            self.git_controls.append(workspace._CONTROL.get() is not None)
            self.request_bases.append(data.get("repository_base_sha"))
            if data.get("planning_operation") == "roadmap":
                self.synthesis_active.append(self.active)
        if data.get("planning_operation") != "assess":
            return super().run(request, should_stop)
        numbers = tuple(entry["number"] for entry in data["backlog"])
        with self.lock:
            ordinal = len(self.entered)
            self.entered.append(numbers)
            self.investigation_cwds.append(str(request.cwd))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.observed_roadmaps.append(issues.status(self.state)["planning"]["roadmap"])
        synchronized = False
        try:
            if ordinal < self.workers:
                try:
                    self.barrier.wait()
                    synchronized = True
                except threading.BrokenBarrierError:
                    # A serial implementation must finish and fail the overlap
                    # assertion, rather than hang waiting for absent workers.
                    pass
                if synchronized and ordinal < self.workers - 1:
                    assert self.release[ordinal].wait(10), "The later investigation did not return"
            elif not self.barrier.broken:
                assert self.first_wave_finished.wait(10), "The first investigation wave did not return"
            return super().run(request, should_stop)
        finally:
            with self.lock:
                self.completed.append(numbers)
                self.active -= 1
                if len([numbers for numbers in self.completed if numbers in self.entered[:self.workers]]) == self.workers:
                    self.first_wave_finished.set()
            if synchronized and ordinal > 0:
                self.release[ordinal - 1].set()


def assert_retained_children(planning, *, synthesis):
    chunks = planning["chunks"]
    assert chunks and all({"id", "operation", "issue_numbers", "child_state", "status"} <= chunk.keys()
                          for chunk in chunks)
    assert len({chunk["id"] for chunk in chunks}) == len(chunks)
    assert {chunk["child_state"] for chunk in chunks} == set(planning["child_states"])
    assert all(chunk["status"] not in {"pending", "prepared", "running", "reserved"} for chunk in chunks)
    assert any(chunk["operation"] == "roadmap" for chunk in chunks) is synthesis
    for child in planning["child_states"]:
        assert (Path(child) / "mission.sqlite3").is_file()
        assert Store(child).control()["runner_pid"] is None


def test_default_three_workers_overlap_isolated_chunks_with_deterministic_complete_coverage(batch, monkeypatch):
    project, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000}
    initial = issues.initialize(spec, state, github=github)
    monkeypatch.setattr(issues, "controlled_git", workspace.controlled_git)
    expected = {str(entry["number"]): entry["source_sha256"] for entry in backlog_runner.inputs(initial)}
    host = ConcurrentHost(state)
    result = issues.run(state, driver=host, github=github, plan_only=True)

    assert host.max_active == 3
    assert host.git_controls and all(host.git_controls)
    assert set(host.request_bases) == {initial["accepted_head"]}
    assert host.synthesis_active == [0]
    assert host.active == 0 and len(host.entered) >= 3
    assert len(set(host.investigation_cwds)) == len(host.investigation_cwds)
    assert all(roadmap is None for roadmap in host.observed_roadmaps)
    first_wave = host.entered[:3]
    assert [numbers for numbers in host.completed if numbers in first_wave] == list(reversed(first_wave))
    planning = result["planning"]
    assert planning["status"] == "ready" and planning["coverage"] == {
        "total": 6, "assessed": [1, 2, 3, 4, 5, 6], "unassessed": [],
    }
    assert planning["source_hashes"] == expected
    assert [note["items"][0]["number"] for note in planning["assessments"]] == sorted(
        note["items"][0]["number"] for note in planning["assessments"])
    assert result["invocations"] == len(host.entered) + 1
    assert_retained_children(planning, synthesis=True)
    assert len(host.planning("roadmap")) == 1 and host.detailed() == []
    assert all(row["plan"] is None for row in result["issues"])
    assert (project / "app.py").read_text() == "value = 0\n" and github.writes == []


@pytest.mark.parametrize("workers", [1, 2])
def test_configured_worker_count_bounds_investigation_waves(batch, workers):
    _, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": workers}
    issues.initialize(spec, state, github=github)
    host = ConcurrentHost(state, workers=workers)
    result = issues.run(state, driver=host, github=github, plan_only=True)
    assert host.max_active == workers and host.active == 0
    assert result["spec"]["queue"]["planning_workers"] == workers
    assert result["planning"]["coverage"]["assessed"] == [1, 2, 3, 4, 5, 6]
    assert result["invocations"] == len(host.entered) + 1
    assert_retained_children(result["planning"], synthesis=True)
    assert github.writes == []


def test_remaining_budget_caps_wave_and_retains_completed_siblings_without_synthesis(batch):
    _, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    spec["limits"]["max_invocations"] = 2
    issues.initialize(spec, state, github=github)
    host = ConcurrentHost(state, workers=2)
    result = issues.run(state, driver=host, github=github, plan_only=True)
    assert host.max_active == 2 and host.active == 0 and len(host.entered) == 2
    assert result["status"] == "limited" and result["invocations"] == 2
    planning = result["planning"]
    assert planning["status"] == "incomplete" and planning["roadmap"] is None
    assessed = {number for chunk in host.entered for number in chunk}
    assert set(planning["coverage"]["assessed"]) == assessed
    assert set(planning["coverage"]["unassessed"]) == set(range(1, 7)) - assessed
    assert len(planning["assessments"]) == 2 and host.planning("roadmap") == []
    assert_retained_children(planning, synthesis=False)
    repeated = issues.run(state, driver=host, github=github, plan_only=True)
    assert repeated["invocations"] == 2 and repeated["deadline"] == result["deadline"]
    assert len(host.entered) == 2 and github.writes == []


def test_small_backlog_retains_direct_one_call_roadmap_fast_path(batch):
    _, state, spec, github = batch
    workflow.populate(github, 3)
    spec["queue"] = {"planning_workers": 3}
    issues.initialize(spec, state, github=github)
    host = ConcurrentHost(state)
    result = issues.run(state, driver=host, github=github, plan_only=True)
    assert host.entered == [] and result["invocations"] == 1
    assert result["planning"]["status"] == "ready"
    assert len(host.planning("roadmap")) == 1
    assert len(result["planning"]["chunks"]) == 1
    assert_retained_children(result["planning"], synthesis=True)
    assert github.writes == []


def test_stop_joins_all_inflight_investigators_without_dispatching_another_wave(batch):
    _, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    issues.initialize(spec, state, github=github)
    rendezvous, stopping = threading.Barrier(3, timeout=10), threading.Event()
    lock, entered, finished = threading.Lock(), [], []

    class StoppingHost:
        def run(self, request, should_stop):
            data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
            assert data["planning_operation"] == "assess"
            with lock:
                ordinal = len(entered)
                entered.append(tuple(entry["number"] for entry in data["backlog"]))
            rendezvous.wait()
            if ordinal == 0:
                issues.stop(state)
                stopping.set()
            else:
                assert stopping.wait(10)
            assert should_stop()
            with lock:
                finished.append(ordinal)
            return DriverResult(cancelled=True)

    result = issues.run(state, driver=StoppingHost(), github=github, plan_only=True)
    assert result["status"] == "stopped" and result["stop_requested"]
    assert result["invocations"] == 3 and len(entered) == 3 and sorted(finished) == [0, 1, 2]
    assert result["planning"]["status"] == "incomplete" and result["planning"]["roadmap"] is None
    assert all(call["state"] not in {"running", "reserved", "unknown"} for call in result["calls"])
    assert_retained_children(result["planning"], synthesis=False)
    assert github.writes == []


def test_clean_cancelled_wave_preserves_receipts_and_resume_assesses_only_uncovered_sources(batch):
    _, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 3}
    issues.initialize(spec, state, github=github)

    class CancellingHost(workflow.PortfolioHost):
        def __init__(self):
            super().__init__(foundation=6)
            self.lock = threading.Lock()
            self.entered = []
            self.waves = [threading.Barrier(3, timeout=10), threading.Barrier(3, timeout=10)]

        def run(self, request, should_stop):
            data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
            assert data["planning_operation"] == "assess"
            with self.lock:
                ordinal = len(self.entered)
                self.entered.append(tuple(entry["number"] for entry in data["backlog"]))
            self.waves[ordinal // 3].wait()
            if ordinal >= 3:
                return DriverResult(cancelled=True)
            return super().run(request, should_stop)

    first_host = CancellingHost()
    first = issues.run(state, driver=first_host, github=github, plan_only=True)
    assert first["planning"]["status"] == "incomplete" and first["planning"]["roadmap"] is None
    assert first["stop_requested"] is False and first["status"] not in {"blocked", "stopped", "limited"}
    assert first["invocations"] == 6 and len(first_host.entered) == 6
    assessed = set(first["planning"]["coverage"]["assessed"])
    uncovered = set(first["planning"]["coverage"]["unassessed"])
    assert assessed and uncovered and assessed.isdisjoint(uncovered)
    assert assessed | uncovered == set(range(1, 7))
    assert_retained_children(first["planning"], synthesis=False)

    # Recovery can settle calls while retaining an interrupted UI projection.
    # Historical children must not continue to look active after the next run.
    interrupted = copy.deepcopy(first["planning"])
    interrupted["chunks"][0]["status"] = "running"
    interrupted["chunks"][1]["status"] = "prepared"
    historical = {chunk["id"] for chunk in interrupted["chunks"][:2]}
    IssueStore(state).save_planning(interrupted)
    recovered = issues.recover(state, acknowledge=True, github=github)
    assert recovered["invocations"] == first["invocations"] and recovered["deadline"] == first["deadline"]

    resumed_host = workflow.PortfolioHost(foundation=6)
    resumed = issues.run(state, driver=resumed_host, github=github, plan_only=True)
    investigated = [entry["number"] for request in resumed_host.planning("assess")
                    for entry in request["data"]["backlog"]]
    assert sorted(investigated) == sorted(uncovered)
    assert resumed["planning"]["status"] == "ready" and resumed["planning"]["coverage"]["unassessed"] == []
    assert resumed["invocations"] == first["invocations"] + len(resumed_host.planning("assess")) + 1
    assert resumed["deadline"] == first["deadline"]
    assert set(first["planning"]["child_states"]) <= set(resumed["planning"]["child_states"])
    assert all(chunk["status"] == "incomplete" for chunk in resumed["planning"]["chunks"]
               if chunk["id"] in historical)
    assert_retained_children(resumed["planning"], synthesis=True)
    assert github.writes == []


def test_protocol_failure_cannot_replace_unknown_cleanup_and_all_workers_are_joined(batch, monkeypatch):
    _, state, spec, github = batch
    workflow.populate(github, 6, body_size=5000)
    spec["queue"] = {"planning_max_bytes": 12000, "planning_workers": 2}
    issues.initialize(spec, state, github=github)
    rendezvous, protocol_recorded = threading.Barrier(2, timeout=10), threading.Event()
    lock, entered, finished, order = threading.Lock(), [], [], []
    original_status = Store.set_status

    def observe_child_status(child, status, reason=None, **fields):
        result = original_status(child, status, reason, **fields)
        if reason == "driver_protocol":
            with lock:
                order.append("protocol_recorded")
            protocol_recorded.set()
        return result

    monkeypatch.setattr(Store, "set_status", observe_child_status)

    class MixedFailureHost(workflow.PortfolioHost):
        def run(self, request, should_stop):
            payload = super().run(request, should_stop).payload
            with lock:
                ordinal = len(entered)
                entered.append(ordinal)
            rendezvous.wait()
            try:
                if ordinal == 0:
                    return DriverResult(payload={"invalid": True})
                assert protocol_recorded.wait(10), "Protocol failure was not recorded before unknown cleanup"
                with lock:
                    order.append("unknown_cleanup_returned")
                return DriverResult(payload=payload, cleanup_confirmed=False)
            finally:
                with lock:
                    finished.append(ordinal)

    with pytest.raises((RuntimeError, ValueError)):
        issues.run(state, driver=MixedFailureHost(foundation=6), github=github, plan_only=True)
    result = issues.status(state)
    assert result["status"] == "blocked" and result["reason"] == "worker_cleanup_unconfirmed"
    assert result["invocations"] == 2 and sorted(finished) == [0, 1]
    assert order.index("protocol_recorded") < order.index("unknown_cleanup_returned")
    assert any(call["state"] == "unknown" for call in result["calls"])
    assert all(call["state"] not in {"running", "reserved"} for call in result["calls"])
    assert result["planning"]["status"] == "incomplete" and result["planning"]["roadmap"] is None
    assert_retained_children(result["planning"], synthesis=False)
    assert github.writes == []
