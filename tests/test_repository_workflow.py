"""Repository intake and restart contracts using contained Git and fake hosts."""

import copy
import json
import signal
import sqlite3
import threading
import time

import pytest

from dutyloop import issues, repository
from dutyloop.github import GitHubError
from dutyloop.issue_store import IssueStore
from dutyloop.repository_store import RepositoryStore
from dutyloop.store import runner_lock
import test_issue_workflow as workflow

Host, source = workflow.Host, workflow.source
# Reuse the fixture's real Git containment, without importing parameter names.
contained_batch, batch = workflow.contained_batch, workflow.batch


@pytest.fixture
def one_tick(monkeypatch):
    # Yield between polls, as a clean host restart would, without clearing any clock.
    monkeypatch.setattr(repository, "_wait", lambda *_: True)


def test_observe_initial_plans_only_and_new_sources_are_non_executable(batch, one_tick):
    _, state, spec, github = batch
    initial = repository.initialize(spec, state, github=github)
    assert initial["intake"] == {"mode": "observe", "poll_seconds": 30}
    host = Host()
    planned = repository.run(state, driver=host, github=github)
    assert host.calls == ["discover"] * 3
    assert [row["state"] for row in planned["issues"]] == ["planned", "needs_decision", "planned"]
    github.issues[4] = source(4)
    observed = repository.reconcile(state, github=github)
    assert len(observed["issues"]) == 3
    assert observed["sources"][-1]["admitted"] is False
    assert observed["attention"][-1]["id"] == "issue:4:intake"
    assert github.writes == []


def test_observe_to_auto_admits_once_and_preserves_clock_calls_and_children(batch, one_tick):
    _, state, spec, github = batch
    github.issues = {1: source(1)}
    repository.initialize(spec, state, github=github)
    host = Host(decisions=())
    planned = repository.run(state, driver=host, github=github)
    deadline = planned["deadline"]
    repository.set_mode(state, "auto")
    first = repository.run(state, driver=host, github=github)
    assert first["invocations"] == 3
    assert first["issues"][0]["state"] == "accepted_local"
    assert first["deadline"] == deadline
    assert len(first["tasks"][0]["attempts"]) == 2
    github.issues[3] = source(3)
    second = repository.run(state, driver=host, github=github)
    assert second["invocations"] == 6
    assert second["deadline"] == deadline
    assert [row["state"] for row in second["issues"]] == ["accepted_local", "accepted_local"]
    again = repository.run(state, driver=host, github=github)
    assert again["invocations"] == 6
    assert host.calls == ["discover", "implement", "review"] * 2
    admitted = [event for event in repository.events(state, limit=1000)["events"]
                if event["kind"] == "repository_issue_admitted"]
    assert [event["data"]["number"] for event in admitted] == [3]


def test_repeated_poll_has_stable_event_cursor_and_receipt_and_timestamp_only_is_not_scope(batch):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    first = repository.reconcile(state, github=github)
    cursor = repository.events(state, limit=1000)["next_cursor"]
    repeated = repository.reconcile(state, github=github)
    assert repository.events(state, after_id=cursor)["events"] == []
    assert repeated["repository"]["last_receipt_id"] == first["repository"]["last_receipt_id"]
    github.issues[1]["updated_at"] = "2026-02-01T00:00:00Z"
    updated = repository.reconcile(state, github=github)
    assert updated["issues"][0]["state"] == "queued"
    github.issues[1]["body"] = "A changed requirement"
    changed = repository.reconcile(state, github=github)
    assert changed["issues"][0]["state"] == "needs_decision"
    assert changed["issues"][0]["source"]["body"] != github.issues[1]["body"]
    cursor = repository.events(state, limit=1000)["next_cursor"]
    repository.reconcile(state, github=github)
    assert repository.events(state, after_id=cursor)["events"] == []
    repository.resolve(state, 1, "The changed requirement is intended", github=github)
    approved = repository.reconcile(state, github=github)
    assert approved["issues"][0]["state"] == "queued"


def test_auto_respects_selection_excluded_labels_and_cumulative_maximum(batch):
    _, state, spec, github = batch
    github.issues = {1: source(1)}
    spec.update(intake={"mode": "auto"}, queue={"numbers": [1, 3], "max_issues": 2})
    # Initial explicit selections must exist; adding new issue 3 below tests observation instead.
    spec["queue"]["numbers"] = []
    repository.initialize(spec, state, github=github)
    github.issues.update({2: source(2), 3: source(3), 4: source(4)})
    github.issues[2]["labels"] = ["needs-human"]
    result = repository.reconcile(state, github=github)
    assert [row["number"] for row in result["issues"]] == [1, 3]
    assert {row["number"]: row["reason"] for row in result["sources"] if not row["admitted"]} == {
        2: "excluded_label", 4: "max_issues_reached"}
    assert len(repository.reconcile(state, github=github)["issues"]) == 2


def test_explicit_selection_cannot_expand_on_later_polls(batch):
    _, state, spec, github = batch
    spec.update(queue={"numbers": [1]}, intake={"mode": "auto"})
    repository.initialize(spec, state, github=github)
    github.issues[4] = source(4)
    result = repository.reconcile(state, github=github)
    assert [row["number"] for row in result["sources"]] == [1]
    assert [row["number"] for row in result["issues"]] == [1]


def test_observed_issue_requires_explicit_admission_and_observe_still_only_plans(batch, one_tick):
    _, state, spec, github = batch
    github.issues = {}
    repository.initialize(spec, state, github=github)
    github.issues[1] = source(1)
    repository.reconcile(state, github=github)
    admitted = repository.resolve(state, 1, "Plan the documented behavior", github=github)
    assert admitted["issues"][0]["decision"] == "Plan the documented behavior"
    host = Host()
    result = repository.run(state, driver=host, github=github)
    assert result["issues"][0]["state"] == "planned"
    assert host.calls == ["discover"]


def test_empty_idle_tick_does_not_construct_or_call_driver_and_restart_keeps_deadline(batch, one_tick, monkeypatch):
    _, state, spec, github = batch
    github.issues = {}
    repository.initialize(spec, state, github=github)
    monkeypatch.setattr(issues, "get_driver", lambda *_a, **_k: pytest.fail("idle instantiated a driver"))
    first = repository.run(state, github=github)
    second = repository.run(state, github=github)
    assert first["invocations"] == second["invocations"] == 0
    assert first["deadline"] == second["deadline"]
    assert first["deadline"] is not None


def test_limit_across_ticks_never_readmits_or_renews(batch, one_tick):
    _, state, spec, github = batch
    github.issues = {1: source(1)}
    spec.update(intake={"mode": "auto"}, limits={"max_invocations": 3, "max_wall_seconds": 300})
    repository.initialize(spec, state, github=github)
    host = Host(decisions=())
    first = repository.run(state, driver=host, github=github)
    github.issues[3] = source(3)
    second = repository.run(state, driver=host, github=github)
    assert first["invocations"] == second["invocations"] == 3
    assert second["status"] == "limited"
    assert second["deadline"] == first["deadline"]
    observed = repository.reconcile(state, github=github)
    assert [row["number"] for row in observed["issues"]] == [1]
    assert observed["sources"][-1]["reason"] == "control_inactive"


def test_wall_limit_of_empty_foreground_loop_is_bounded_without_model_calls(batch):
    _, state, spec, github = batch
    github.issues = {}
    spec.update(limits={"max_invocations": 4, "max_wall_seconds": .1}, intake={"poll_seconds": .01})
    repository.initialize(spec, state, github=github)
    result = repository.run(state, driver=Host(), github=github)
    assert result["status"] == "limited"
    assert result["reason"] == "wall_time_limit"
    assert result["invocations"] == 0


def test_stop_between_remote_read_and_admission_wins(batch):
    _, state, spec, github = batch
    github.issues = {}
    spec["intake"] = {"mode": "auto"}
    repository.initialize(spec, state, github=github)
    original = github.list_open_issues
    github.issues[1] = source(1)

    def stop_on_read():
        result = original()
        repository.stop(state)
        return result

    github.list_open_issues = stop_on_read
    result = repository.reconcile(state, github=github)
    assert result["status"] == "stopped"
    assert result["issues"] == []
    assert result["sources"][0]["admitted"] is False


def test_foreground_stop_is_responsive_and_cannot_be_revived_by_mode(batch):
    _, state, spec, github = batch
    github.issues = {}
    repository.initialize(spec, state, github=github)
    output = []
    worker = threading.Thread(target=lambda: output.append(repository.run(state, driver=Host(), github=github)))
    worker.start()
    try:
        until = time.monotonic() + 5
        while repository.status(state)["repository"]["last_reconciled_at"] is None and time.monotonic() < until:
            time.sleep(.01)
        repository.stop(state)
        worker.join(3)
        assert not worker.is_alive()
        assert output[0]["status"] == "stopped"
        changed = repository.set_mode(state, "auto")
        assert changed["stop_requested"] is True
        assert repository.run(state, driver=Host(), github=github)["status"] == "stopped"
    finally:
        repository.stop(state)
        worker.join(3)


def test_unknown_call_blocks_restart_until_acknowledged_without_replay(batch, one_tick):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    repository.initialize(spec, state, github=github)
    store = IssueStore(state)
    assert store.begin()
    call = store.reserve_call("implement", 1)
    store.update_issue(1, "implementing")
    with pytest.raises(RuntimeError, match="explicit recovery"):
        repository.run(state, driver=Host(), github=github)
    with pytest.raises(ValueError, match="acknowledge"):
        repository.recover(state, github=github)
    recovered = repository.recover(state, acknowledge=True, github=github)
    assert recovered["calls"][0]["id"] == call
    assert recovered["calls"][0]["state"] == "unknown"
    assert recovered["issues"][0]["state"] == "needs_decision"
    host = Host()
    final = repository.run(state, driver=host, github=github)
    assert final["invocations"] == 1
    assert host.calls == []


def test_reconcile_cannot_erase_interrupted_delivery(batch):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    IssueStore(state).update_issue(1, "interrupted", previous_state="publishing", head="retained")
    github.issues[1]["body"] = "changed"
    result = repository.reconcile(state, github=github)
    assert result["issues"][0]["state"] == "interrupted"
    assert result["issues"][0]["head"] == "retained"


def test_coordinator_lock_is_distinct_from_serial_issue_runner_lock(batch, one_tick):
    _, state, spec, github = batch
    github.issues = {}
    repository.initialize(spec, state, github=github)
    (state / "coordinator").mkdir()
    with runner_lock(state / "coordinator"):
        with pytest.raises(RuntimeError, match="Another supervisor"):
            repository.run(state, github=github)
        # Read-only intake can take the issue lock while the coordinator owns its own lock.
        assert repository.reconcile(state, github=github)["invocations"] == 0


def test_marker_does_not_convert_finite_issue_batch(batch):
    _, state, spec, github = batch
    assert not repository.is_repository(state)
    issues.initialize(spec, state, github=github)
    assert not repository.is_repository(state)
    with pytest.raises(ValueError, match="repository agent"):
        repository.status(state)


def test_incomplete_initialization_remains_repository_and_cannot_bypass_observe(batch, monkeypatch):
    _, state, spec, github = batch

    def fail_metadata(*_args):
        raise RuntimeError("Simulated interruption after issue capture")

    monkeypatch.setattr(RepositoryStore, "create", fail_metadata)
    with pytest.raises(RuntimeError, match="Simulated interruption"):
        repository.initialize(spec, state, github=github)
    assert repository.is_repository(state)
    assert (state / "repository.initializing").is_file()
    with pytest.raises(ValueError, match="repository agent"):
        repository.run(state, driver=Host(), github=github)


def test_known_read_timeouts_retry_boundedly_without_dispatch_and_record_health(batch, monkeypatch):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    attempted = []

    def timeout():
        attempted.append(1)
        raise GitHubError("sanitized timeout", timed_out=True)

    github.list_open_issues = timeout
    monkeypatch.setattr(repository, "_wait", lambda *_: False)
    host = Host()
    with pytest.raises(GitHubError):
        repository.run(state, driver=host, github=github)
    result = repository.status(state)
    assert len(attempted) == 3
    assert host.calls == []
    assert result["repository"]["source_error"]["attempts"] == 3
    assert result["repository"]["coordinator_pid"] is None
    assert any(row["id"] == "repository:source" for row in result["attention"])


def test_unknown_read_cleanup_blocks_without_retry(batch, monkeypatch):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    attempted = []

    def failed():
        attempted.append(1)
        raise GitHubError("cleanup unknown", cleanup_confirmed=False)

    github.list_open_issues = failed
    with pytest.raises(GitHubError):
        repository.run(state, driver=Host(), github=github)
    assert len(attempted) == 1
    assert repository.status(state)["status"] == "blocked"


@pytest.mark.parametrize("failure", ["cancelled", "timed_out"])
def test_expired_deadline_during_clean_intake_finishes_limited_without_source_error(batch, failure):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    store = IssueStore(state)

    def deadline_during_read():
        with store.connection() as db:
            control = store._read(db)
            control["deadline"] = time.time() - 1
            store._save(db, control)
        raise GitHubError("Read ended at its deadline", **{failure: True})

    github.list_open_issues = deadline_during_read
    host = Host()
    result = repository.run(state, driver=host, github=github)
    assert result["status"] == "limited"
    assert result["reason"] == "wall_time_limit"
    assert result["invocations"] == 0
    assert not result["stop_requested"]
    assert result["repository"].get("source_error") is None
    assert result["repository"]["coordinator_pid"] is None
    assert host.calls == []
    assert not any(row["kind"] == "repository_source_error" for row in repository.events(state, limit=1000)["events"])


@pytest.mark.parametrize("unknown", [{"cleanup_confirmed": False}, {"outcome_unknown": True}])
def test_unknown_intake_cleanup_or_outcome_takes_precedence_over_expired_deadline(batch, unknown):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    store = IssueStore(state)

    def deadline_during_unknown_read():
        with store.connection() as db:
            control = store._read(db)
            control["deadline"] = time.time() - 1
            store._save(db, control)
        raise GitHubError("Read outcome or cleanup requires inspection", cancelled=True, **unknown)

    github.list_open_issues = deadline_during_unknown_read
    host = Host()
    with pytest.raises(GitHubError):
        repository.run(state, driver=host, github=github)
    result = repository.status(state)
    assert result["status"] == "blocked"
    assert result["reason"] == "worker_cleanup_unconfirmed"
    assert result["repository"]["source_error"]["attempts"] == 1
    assert result["invocations"] == 0
    assert host.calls == []


@pytest.fixture
def recorded_signal_handlers(monkeypatch):
    installed = {}
    original = signal.signal

    def record(signum, handler):
        previous = original(signum, handler)
        installed[signum] = handler
        return previous

    monkeypatch.setattr(repository.signal, "signal", record)
    return installed


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("unknown", [{"cleanup_confirmed": False}, {"outcome_unknown": True}])
def test_signal_during_unknown_intake_latches_stop_and_preserves_blocked_reason(batch, recorded_signal_handlers, signum, unknown):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def interrupted_read():
        # Invoke the captured handler directly; do not send a process OS signal.
        recorded_signal_handlers[signum](signum, None)
        raise GitHubError("Read cleanup or outcome remains unknown", cancelled=True, **unknown)

    github.list_open_issues = interrupted_read
    with pytest.raises(GitHubError):
        repository.run(state, driver=Host(), github=github)
    result = repository.status(state)
    assert result["status"] == "blocked"
    assert result["reason"] == "worker_cleanup_unconfirmed"
    assert result["stop_requested"] is True
    assert result["repository"]["source_error"]["attempts"] == 1
    assert result["repository"]["coordinator_pid"] is None
    assert result["invocations"] == 0
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_stale_coordinator_record_is_not_an_unfinished_issue_and_is_audited_on_restart(batch, one_tick):
    _, state, spec, github = batch
    github.issues = {}
    repository.initialize(spec, state, github=github)
    store = RepositoryStore(state)
    with store.issues.connection() as db:
        metadata = store.metadata(db)
        metadata.update(coordinator_pid=123, coordinator_started_at=1, coordinator_heartbeat_at=1)
        store.save_metadata(db, metadata)
    stale = repository.status(state)
    assert stale["health"]["coordinator_liveness"] == "unverified"
    assert any(row["reason"] == "coordinator_last_observation_old" for row in stale["attention"])
    restarted = repository.run(state, driver=Host(), github=github)
    assert restarted["invocations"] == 0
    assert restarted["repository"]["coordinator_pid"] is None
    assert any(row["kind"] == "repository_coordinator_restarted" for row in repository.events(state, limit=1000)["events"])


@pytest.mark.parametrize("stored", ["ready", "completed", "running", "blocked", "limited", "stopped"])
def test_public_coordinator_status_preserves_finite_pass_and_control_terminals(batch, stored):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    store = RepositoryStore(state)
    store.issues.set_status(stored)
    with store.issues.connection() as db:
        metadata = store.metadata(db)
        metadata.update(coordinator_pid=123, coordinator_started_at=time.time(), coordinator_heartbeat_at=time.time())
        store.save_metadata(db, metadata)
    observed = repository.status(state)
    assert observed["pass_status"] == stored
    assert observed["status"] == ("watching" if stored in {"ready", "completed"} else stored)
    assert observed["health"]["coordinator_liveness"] == "unverified"
    assert store.issues.control()["status"] == stored
    with store.issues.connection() as db:
        metadata = store.metadata(db)
        metadata["coordinator_pid"] = None
        store.save_metadata(db, metadata)
    cleared = repository.status(state)
    assert cleared["status"] == cleared["pass_status"] == stored


def test_recovery_rejects_a_coordinator_still_holding_its_lock(batch):
    _, state, spec, github = batch
    repository.initialize(spec, state, github=github)
    (state / "coordinator").mkdir()
    with runner_lock(state / "coordinator"):
        with pytest.raises(RuntimeError, match="Another supervisor"):
            repository.recover(state, acknowledge=True, github=github)


def test_desktop_status_reports_requests_without_claiming_expiring_or_exposing_tokens(batch):
    _, state, spec, github = batch
    spec["driver"] = {"name": "desktop"}
    repository.initialize(spec, state, github=github)
    path = state / "desktop.sqlite3"
    now = time.time()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE requests(request_id TEXT,phase TEXT,state TEXT,created_at REAL,claimed_at REAL,deadline REAL,result TEXT,token TEXT)")
    db.executemany("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)", [
        ("waiting", "discover", "pending", now - 15, None, now - 1, None, "private-token"),
        ("working", "implement", "claimed", now - 12, now - 10, now + 30, None, "private-token"),
        ("uncertain", "review", "quarantined", now - 20, now - 18, now - 10,
         json.dumps({"cleanup_confirmed": False}), "private-token"),
    ])
    db.commit()
    db.close()
    before = path.read_bytes()
    result = repository.status(state)
    executor = result["executor"]
    assert executor["pending"] == executor["claimed"] == 1
    assert executor["oldest_pending_age_seconds"] >= 15
    assert executor["oldest_claimed_age_seconds"] >= 10
    assert executor["status"] == "external_work_claimed"
    assert executor["cannot_wake_host"] is executor["cannot_terminate_external"] is True
    assert "private-token" not in json.dumps(result)
    assert {row["reason"] for row in result["attention"] if row["type"] == "executor"} == {
        "desktop_request_overdue", "desktop_request_claimed", "external_cleanup_unknown"}
    assert path.read_bytes() == before


def test_reappearing_unapproved_scope_cannot_reuse_a_stale_approval_hash(batch):
    _, state, spec, github = batch
    github.issues = {1: source(1)}
    repository.initialize(spec, state, github=github)
    original = copy.deepcopy(github.issues[1])
    github.issues[1]["body"] = "Unapproved revision B"
    assert repository.reconcile(state, github=github)["issues"][0]["state"] == "needs_decision"
    github.issues[1] = original
    repository.reconcile(state, github=github)
    approved = repository.resolve(state, 1, "Approve original behavior A", github=github)
    assert approved["issues"][0].get("pending_scope_sha256") is None
    # Model acceptance is orthogonal to source-approval history.
    IssueStore(state).update_issue(1, "accepted_local")
    github.issues[1]["body"] = "Unapproved revision B"
    assert repository.reconcile(state, github=github)["issues"][0]["state"] == "needs_decision"


@pytest.mark.parametrize("value", [{"mode": "always"}, {"poll_seconds": 0}, {"poll_seconds": True}, {"extra": 1}])
def test_invalid_intake_leaves_no_database(batch, value):
    _, state, spec, github = batch
    spec["intake"] = value
    with pytest.raises(ValueError):
        repository.initialize(spec, state, github=github)
    assert not (state / "issues.sqlite3").exists()


def test_identical_duplicates_deduplicate_and_conflicting_duplicates_fail_atomically(batch):
    _, state, spec, github = batch
    github.issues = {1: source(1)}
    repository.initialize(spec, state, github=github)
    same = source(3)
    github.list_open_issues = lambda: [source(1), same, copy.deepcopy(same)]
    result = repository.reconcile(state, github=github)
    assert result["receipts"][0]["data"]["duplicates"] == 1
    old = repository.events(state, limit=1000)["next_cursor"]
    github.list_open_issues = lambda: [source(1), same, {**same, "body": "conflicting"}]
    with pytest.raises(ValueError, match="conflicting"):
        repository.reconcile(state, github=github)
    assert repository.events(state, after_id=old)["events"] == []


def test_missing_open_listing_is_verified_before_external_closure(batch):
    _, state, spec, github = batch
    spec["queue"] = {"numbers": [1]}
    repository.initialize(spec, state, github=github)
    github.list_open_issues = lambda: []
    assert repository.reconcile(state, github=github)["issues"][0]["state"] == "queued"
    github.issues[1]["state"] = "closed"
    assert repository.reconcile(state, github=github)["issues"][0]["state"] == "external_closed"
