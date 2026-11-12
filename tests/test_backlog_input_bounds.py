"""Planning bounds apply to complete serialized host requests, not source estimates."""

import json

from dutyloop import backlog_runner, engine, issues
from dutyloop.drivers import DriverResult
import test_issue_workflow as workflow


contained_batch, batch = workflow.contained_batch, workflow.batch


class BoundedHost(workflow.Host):
    def __init__(self, *, long_assessments=False, cancel_second_chunk=False):
        super().__init__(decisions=())
        self.requests = []
        self.long_assessments = long_assessments
        self.cancel_second_chunk = cancel_second_chunk
        self.chunk_calls = 0

    def run(self, request, should_stop):
        data = json.loads(request.prompt[request.prompt.index('{"authority"'):])
        self.requests.append({"data": data, "bytes": len(request.prompt.encode("utf-8"))})
        if data["planning_operation"] == "assess":
            self.chunk_calls += 1
            if self.cancel_second_chunk and self.chunk_calls == 2:
                self.calls.append(request.phase)
                return DriverResult(cancelled=True)
        result = super().run(request, should_stop)
        if self.long_assessments and data["planning_operation"] == "assess":
            for item in result.payload["items"]:
                item["assessment"] = "Preserve the complete acceptance contract. " + "x" * 1150
        return result


def large_sources(spec, github, *, large_authority=False):
    spec["queue"] = {"planning_max_bytes": 20000}
    if large_authority:
        spec["constraints"] = ['Preserve the quoted API "acceptance": ' + "约束" * 1000]
    github.issues = {number: workflow.source(number) for number in range(1, 7)}
    for number, source in github.issues.items():
        source["body"] += "x" * 3500 + f" REQUIRED-BODY-{number}"
        source["comments"] = [{"body": f"REQUIRED-COMMENT-{number}"}]


def test_short_backlog_near_compact_limit_uses_complete_bounded_chunks(batch, monkeypatch):
    _, state, spec, github = batch
    github.issues = {number: workflow.source(number) for number in range(1, 191)}
    for number, source in github.issues.items():
        source["comments"] = [{"body": f"Preserve comment acceptance {number}"}]
    captured = {"issues": [{"number": number, "source": source, "state": "queued"}
                           for number, source in github.issues.items()], "spec": spec}
    compact_size = len(backlog_runner.encoded(backlog_runner.inputs(captured)).encode("utf-8"))
    padding = (121000 - compact_size) // 190
    assert padding > 0
    for source in github.issues.values():
        source["body"] += "x" * padding
    initialized = issues.initialize(spec, state, github=github)
    compact_size = len(backlog_runner.encoded(backlog_runner.inputs(initialized)).encode("utf-8"))
    limit = initialized["spec"]["queue"]["planning_max_bytes"]
    assert compact_size < limit - 6000
    built = []
    original_prompt = engine.Supervisor.prompt

    def record_prompt(self, instruction, **data):
        prompt = original_prompt(self, instruction, **data)
        built.append((data, len(prompt.encode("utf-8"))))
        return prompt

    monkeypatch.setattr(engine.Supervisor, "prompt", record_prompt)
    host = BoundedHost()
    result = issues.run(state, driver=host, github=github, plan_only=True)
    assert any(data.get("planning_operation") == "roadmap" and len(data["backlog"]) == 190
               and size > limit for data, size in built)
    chunks = [request for request in host.requests if request["data"]["planning_operation"] == "assess"]
    assert chunks
    entries = [entry for request in chunks for entry in request["data"]["backlog"]]
    assert [entry["number"] for entry in entries] == list(range(1, 191))
    assert all(entry["source"]["body"] == github.issues[entry["number"]]["body"]
               and entry["source"]["comments"] == github.issues[entry["number"]]["comments"] for entry in entries)
    assert all(request["bytes"] <= limit for request in host.requests)
    assert result["planning"]["coverage"]["unassessed"] == []
    assert result["planning"]["status"] == "ready"
    assert all(row["plan"] is None for row in result["issues"])
    assert not github.writes


def test_chunk_packing_counts_authority_context_and_utf8_bytes(batch):
    _, state, spec, github = batch
    large_sources(spec, github, large_authority=True)
    initialized = issues.initialize(spec, state, github=github)
    host = BoundedHost()
    result = issues.run(state, driver=host, github=github, plan_only=True)
    chunks = [request for request in host.requests if request["data"]["planning_operation"] == "assess"]
    assert len(chunks) >= 2
    entries = [entry for request in chunks for entry in request["data"]["backlog"]]
    assert [entry["number"] for entry in entries] == list(range(1, 7))
    assert all(entry["source"]["body"] == github.issues[entry["number"]]["body"]
               and entry["source"]["comments"] == github.issues[entry["number"]]["comments"] for entry in entries)
    assert all(request["data"]["authority"]["constraints"] == initialized["spec"]["constraints"]
               and request["bytes"] <= 20000 for request in host.requests)
    assert result["planning"]["status"] == "ready"
    assert not github.writes


def test_oversized_complete_synthesis_retains_assessments_without_dispatch(batch):
    _, state, spec, github = batch
    large_sources(spec, github, large_authority=True)
    issues.initialize(spec, state, github=github)
    host = BoundedHost(long_assessments=True)
    result = issues.run(state, driver=host, github=github, plan_only=True)
    planning = result["planning"]
    assert planning["status"] == "needs_decision"
    assert planning["reason"] == "synthesis_exceeds_planning_input_limit"
    assert planning["coverage"] == {"total": 6, "assessed": list(range(1, 7)), "unassessed": []}
    assert planning["roadmap"] is None and planning["assessments"]
    assert all(request["data"]["planning_operation"] == "assess" and request["bytes"] <= 20000
               for request in host.requests)
    assert result["invocations"] == len(host.requests)
    assert all(row["plan"] is None for row in result["issues"])
    assert not github.writes


def test_chunk_resume_keeps_finished_notes_and_only_reassesses_unfinished_sources(batch):
    _, state, spec, github = batch
    large_sources(spec, github)
    issues.initialize(spec, state, github=github)
    host = BoundedHost(cancel_second_chunk=True)
    first = issues.run(state, driver=host, github=github, plan_only=True)
    retained = first["planning"]["coverage"]["assessed"]
    assert first["planning"]["status"] == "incomplete"
    assert first["invocations"] == 2 and 0 < len(retained) < 6
    finished_note = first["planning"]["assessments"][0]
    assert all(call["state"] != "unknown" for call in first["calls"])
    before_resume = len(host.requests)
    resumed = issues.run(state, driver=host, github=github, plan_only=True)
    assert resumed["planning"]["status"] == "ready"
    assert resumed["planning"]["assessments"][0] == finished_note
    assert resumed["planning"]["revision"] == first["planning"]["revision"]
    assert resumed["deadline"] == first["deadline"]
    remaining = [entry["number"] for request in host.requests[before_resume:]
                 if request["data"]["planning_operation"] == "assess" for entry in request["data"]["backlog"]]
    assert remaining == first["planning"]["coverage"]["unassessed"]
    assert all(request["bytes"] <= 20000 for request in host.requests)
    assert resumed["planning"]["coverage"]["unassessed"] == []
    assert not github.writes
