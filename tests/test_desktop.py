import importlib
import importlib.util
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from dutyloop.drivers import DriverRequest


SCHEMA = {
    "type": "object", "properties": {"summary": {"type": "string"}},
    "required": ["summary"], "additionalProperties": False,
}
PAYLOAD = {"summary": "Inspected the requested fixture"}


def test_idle_mailbox_never_creates_state(tmp_path):
    assert importlib.util.find_spec("dutyloop.desktop") is not None, "Desktop mailbox is missing"
    module = importlib.import_module("dutyloop.desktop")
    queue = module.DesktopQueue(tmp_path / "missing")
    assert queue.claim("worker", "context") == {"status": "waiting"}
    assert queue.invalidate() == {"quarantined": 0}
    with pytest.raises(ValueError, match="request|mailbox"):
        queue.submit("missing", "token", "worker", "context", PAYLOAD, True)
    assert not (tmp_path / "missing").exists()


@pytest.fixture
def running(tmp_path):
    module = importlib.import_module("dutyloop.desktop")
    pool = ThreadPoolExecutor(max_workers=4)
    futures, stops = [], []
    queue = module.DesktopQueue(tmp_path / "mailbox")

    def start(phase="implement", timeout=5, output_dir=None):
        cwd = tmp_path / "project"
        cwd.mkdir(exist_ok=True)
        request = DriverRequest(
            phase, "Perform the bounded fixture phase", cwd, SCHEMA,
            output_dir or tmp_path / "calls" / uuid.uuid4().hex, timeout,
        )
        stop = threading.Event()
        future = pool.submit(module.DesktopDriver(tmp_path / "mailbox").run, request, stop.is_set)
        futures.append(future)
        stops.append(stop)
        return future, request, stop

    yield queue, start
    for stop in stops:
        stop.set()
    for future in futures:
        future.result(timeout=3)
    pool.shutdown()


def claim(queue, worker="worker", context="context"):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        result = queue.claim(worker, context)
        if result["status"] != "waiting":
            return result
        time.sleep(.005)
    raise AssertionError("Driver did not publish work")


def submit(queue, job, payload=PAYLOAD, **changes):
    values = {
        "request_id": job["request_id"], "token": job["token"],
        "worker_id": job["worker_id"], "context_id": job["context_id"],
        "payload": payload, "work_stopped": True,
    }
    return queue.submit(**{**values, **changes})


def cancel(queue, job, **changes):
    values = {
        "request_id": job["request_id"], "token": job["token"],
        "worker_id": job["worker_id"], "context_id": job["context_id"], "work_stopped": True,
    }
    return queue.cancel(**{**values, **changes})


def published(request):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        if (request.output_dir / "desktop.request.json").exists():
            return
        time.sleep(.005)
    raise AssertionError("Driver did not persist its request")


def test_driver_hands_off_without_starting_a_host_cli(running, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Must not launch a CLI"))
    queue, start = running
    future, request, _stop = start()
    job = claim(queue)
    assert job["phase"] == "implement"
    assert job["prompt"] == request.prompt
    assert job["cwd"] == str(request.cwd.resolve())
    assert job["schema"] == SCHEMA
    assert job["context_identity"] == "self-reported"
    assert queue.claim("worker", "context") == job
    receipt = submit(queue, job)
    result = future.result(timeout=2)
    assert receipt["status"] == "submitted"
    assert result.payload == PAYLOAD
    assert result.cleanup_confirmed and result.cleanup_scope == "host_declaration"
    assert result.cost_usd is None and result.usage == {}
    evidence = json.loads((request.output_dir / "desktop.result.json").read_text(encoding="utf-8"))
    assert evidence["work_stopped"] is True
    assert evidence["context_identity"] == "self-reported"
    assert "cooperative" in evidence["cleanup_evidence"]
    recorded = json.loads((request.output_dir / "desktop.request.json").read_text(encoding="utf-8"))
    assert recorded["request_id"] == job["request_id"]
    assert recorded["generation"] == job["generation"]
    assert recorded["deadline"] == job["deadline"]


def test_competing_claims_expose_one_owner_only(running):
    queue, start = running
    _future, request, _stop = start()
    barrier = threading.Barrier(3)

    def compete(number):
        barrier.wait()
        return claim_once(number)

    def claim_once(number):
        return queue.claim(f"worker-{number}", f"context-{number}")

    # Wait for publication without leasing the request.
    published(request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(compete, number) for number in range(2)]
        barrier.wait()
        results = [future.result(timeout=2) for future in attempts]
    jobs = [result for result in results if result["status"] == "claimed"]
    assert len(jobs) == 1
    assert sum(result["status"] == "waiting" for result in results) == 1
    cancel(queue, jobs[0])


def test_worker_or_context_cannot_own_two_active_requests(running):
    queue, start = running
    start()
    first = claim(queue)
    start()
    with pytest.raises(ValueError, match="active"):
        queue.claim("worker", "different-context")
    with pytest.raises(ValueError, match="active"):
        queue.claim("different-worker", "context")
    assert queue.claim("worker", "context") == first


def test_submit_is_idempotent_and_rejects_conflicts_and_wrong_owners(running):
    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    for changed in ({"token": uuid.uuid4().hex}, {"worker_id": "other"}, {"context_id": "other"}):
        with pytest.raises(ValueError, match="owner|token"):
            submit(queue, job, **changed)
    receipt = submit(queue, job)
    future.result(timeout=2)
    assert submit(queue, job) == receipt
    with pytest.raises(ValueError, match="[Cc]onflict|different"):
        submit(queue, job, {"summary": "A different result"})
    with pytest.raises(ValueError):
        cancel(queue, job)


def test_schema_and_cooperative_stop_are_required_before_submission(running):
    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    for payload in ({}, {"summary": True}, {"summary": "ok", "extra": True}):
        with pytest.raises(ValueError):
            submit(queue, job, payload)
    for value in (False, 1, "true", None):
        with pytest.raises(ValueError, match="work_stopped"):
            submit(queue, job, work_stopped=value)
    assert not future.done()
    submit(queue, job)
    assert future.result(timeout=2).payload == PAYLOAD


def test_closed_batch_blocks_new_results_but_allows_identical_receipt_retry(running):
    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    with pytest.raises(ValueError, match="Batch stopped or exhausted"):
        submit(queue, job, accept_new=False)
    assert not future.done()
    receipt = submit(queue, job)
    future.result(timeout=2)
    assert submit(queue, job, accept_new=False) == receipt
    with pytest.raises(ValueError, match="[Cc]onflict"):
        submit(queue, job, {"summary": "A changed result"}, accept_new=False)


@pytest.mark.parametrize("phase", ["implement", "review", "discover"])
def test_unclaimed_deadline_is_clean_and_never_exposed(running, phase):
    queue, start = running
    future, request, _stop = start(phase, timeout=.08)
    result = future.result(timeout=2)
    assert result.timed_out and result.cleanup_confirmed
    assert result.cleanup_scope == "host_declaration"
    assert queue.claim("worker", "context") == {"status": "waiting"}
    evidence = json.loads((request.output_dir / "desktop.result.json").read_text(encoding="utf-8"))
    assert evidence["work_stopped"] is None
    assert evidence["cleanup_evidence"] == "never claimed by an external worker"


def test_claimed_deadline_quarantines_and_rejects_late_result(running):
    queue, start = running
    future, request, _stop = start(timeout=.15)
    job = claim(queue)
    result = future.result(timeout=2)
    assert result.timed_out and not result.cleanup_confirmed
    assert result.cleanup_scope == "host_declaration"
    assert queue.claim("other", "other-context") == {"status": "waiting"}
    with pytest.raises(ValueError, match="expired|quarantin|late"):
        submit(queue, job)
    with pytest.raises(ValueError):
        cancel(queue, job)
    assert json.loads((request.output_dir / "desktop.result.json").read_text())["status"] == "quarantined"


@pytest.mark.parametrize("claimed", [False, True])
def test_stop_distinguishes_unexposed_from_external_work(running, claimed):
    queue, start = running
    future, request, stop = start()
    job = claim(queue) if claimed else None
    if not claimed:
        published(request)
    stop.set()
    result = future.result(timeout=2)
    assert result.cancelled
    assert result.cleanup_confirmed is not claimed
    assert result.cleanup_scope == "host_declaration"
    if job:
        with pytest.raises(ValueError):
            submit(queue, job)


def test_acknowledged_cancellation_is_clean_and_idempotent(running):
    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    with pytest.raises(ValueError, match="work_stopped"):
        cancel(queue, job, work_stopped=False)
    receipt = cancel(queue, job)
    assert cancel(queue, job) == receipt
    result = future.result(timeout=2)
    assert result.cancelled and result.cleanup_confirmed
    assert result.cleanup_scope == "host_declaration"
    with pytest.raises(ValueError):
        submit(queue, job)


def test_reopening_queue_retains_claim_and_does_not_rotate_token(running):
    from dutyloop.desktop import DesktopQueue

    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    reopened = DesktopQueue(queue.directory)
    assert reopened.claim("worker", "context") == job
    assert reopened.claim("other", "other-context") == {"status": "waiting"}
    with pytest.raises(ValueError):
        submit(reopened, job, token=uuid.uuid4().hex)
    submit(reopened, job)
    assert future.result(timeout=2).payload == PAYLOAD


@pytest.mark.parametrize("claimed", [False, True])
def test_recovery_invalidation_retains_requests_without_replay(running, claimed):
    queue, start = running
    future, request, _stop = start()
    job = claim(queue) if claimed else None
    published(request)
    assert queue.invalidate() == {"quarantined": 1}
    assert queue.invalidate() == {"quarantined": 0}
    result = future.result(timeout=2)
    assert result.cancelled and result.cleanup_confirmed is not claimed
    assert queue.claim("worker", "context") == {"status": "waiting"}
    if job:
        with pytest.raises(ValueError):
            submit(queue, job)
    assert (request.output_dir / "desktop.request.json").is_file()
    assert (request.output_dir / "desktop.result.json").is_file()


def test_review_requires_context_different_from_every_prior_implementation(running):
    queue, start = running
    for context in ("implementer-one", "implementer-two"):
        future, _request, _stop = start()
        job = claim(queue, "worker", context)
        submit(queue, job)
        future.result(timeout=2)
    future, _request, _stop = start("review")
    # Wait until review is published, then reject each prior implementation context.
    until = time.monotonic() + 2
    while time.monotonic() < until:
        with sqlite3.connect(queue.path) as db:
            if db.execute("SELECT count(*) FROM requests").fetchone()[0] == 3:
                break
        time.sleep(.005)
    for context in ("implementer-one", "implementer-two"):
        with pytest.raises(ValueError, match="fresh|review|context"):
            queue.claim("reviewer", context)
    review = claim(queue, "reviewer", "fresh-reviewer")
    assert "fresh" in review["instructions"].lower()
    assert "subagent" in review["instructions"].lower()
    submit(queue, review)
    assert future.result(timeout=2).payload == PAYLOAD


@pytest.mark.parametrize("value", ["", " ", "x" * 201, None, 1, "line\nbreak"])
def test_identity_inputs_are_bounded_and_nonempty(tmp_path, value):
    from dutyloop.desktop import DesktopQueue

    queue = DesktopQueue(tmp_path / "missing")
    for worker, context in ((value, "context"), ("worker", value)):
        with pytest.raises(ValueError):
            queue.claim(worker, context)
    assert not queue.directory.exists()


def test_submission_size_and_nonfinite_numbers_are_rejected(running):
    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    for payload in ({"summary": "x" * (1024 * 1024 + 1)}, {"summary": float("nan")}):
        with pytest.raises(ValueError):
            submit(queue, job, payload)
    submit(queue, job)
    assert future.result(timeout=2).payload == PAYLOAD


def test_unique_output_directory_is_never_reused(running):
    queue, start = running
    future, request, _stop = start()
    job = claim(queue)
    submit(queue, job)
    future.result(timeout=2)
    repeated, _request, _stop = start(output_dir=request.output_dir)
    result = repeated.result(timeout=2)
    assert result.error and result.error_kind == "config"
    assert queue.claim("worker", "context") == {"status": "waiting"}


def test_lost_database_does_not_allow_overwriting_existing_request_evidence(running, tmp_path):
    from dutyloop.desktop import DesktopDriver

    queue, start = running
    future, request, _stop = start()
    job = claim(queue)
    submit(queue, job)
    future.result(timeout=2)
    before = (request.output_dir / "desktop.request.json").read_bytes()
    result = DesktopDriver(tmp_path / "different-mailbox").run(request, lambda: False)
    assert result.error and result.error_kind == "config"
    assert (request.output_dir / "desktop.request.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["oversized_prompt", "nonfinite_timeout", "zero_timeout"])
def test_invalid_request_is_rejected_before_mailbox_creation(tmp_path, mutation):
    from dutyloop.desktop import DesktopDriver

    request = DriverRequest("implement", "fixture", tmp_path, SCHEMA, tmp_path / "output", 1)
    if mutation == "oversized_prompt":
        request.prompt = "x" * (1024 * 1024 + 1)
    else:
        request.timeout_seconds = float("inf") if mutation == "nonfinite_timeout" else 0
    result = DesktopDriver(tmp_path / "mailbox").run(request, lambda: False)
    assert result.error_kind == "config" and result.cleanup_confirmed
    assert result.cleanup_scope == "host_declaration"
    assert not (tmp_path / "mailbox").exists()
    assert not request.output_dir.exists()


def test_concurrent_identical_submissions_return_one_durable_receipt(running):
    queue, start = running
    future, _request, _stop = start()
    job = claim(queue)
    barrier = threading.Barrier(3)

    def complete():
        barrier.wait()
        return submit(queue, job)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(complete), pool.submit(complete)
        barrier.wait()
        assert first.result(timeout=2) == second.result(timeout=2)
    assert future.result(timeout=2).payload == PAYLOAD


def test_deadline_is_rechecked_after_payload_validation(running, monkeypatch):
    from dutyloop import desktop

    queue, start = running
    future, _request, _stop = start(timeout=.2)
    job = claim(queue)
    validate = desktop.validate_payload

    def validation_crosses_deadline(payload, schema):
        validate(payload, schema)
        time.sleep(max(0, job["deadline"] - time.time()) + .02)

    monkeypatch.setattr(desktop, "validate_payload", validation_crosses_deadline)
    with pytest.raises(ValueError, match="expired|deadline"):
        submit(queue, job)
    result = future.result(timeout=2)
    assert result.timed_out and not result.cleanup_confirmed
