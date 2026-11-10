"""Durable issue queue control contracts, independent of any model or GitHub API."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
from unittest.mock import patch

import pytest

from dutyloop.issue_store import IssueStore


@pytest.fixture
def store(tmp_path):
    result = IssueStore(tmp_path / "state")
    result.create(
        {"repository": "example/project", "limits": {"max_invocations": 4, "max_wall_seconds": 60}},
        [{"number": 12, "title": "Second", "body": "Details", "state": "open"},
         {"number": 3, "title": "First", "comments": [], "labels": []}],
        "base-sha",
    )
    return result


def test_create_snapshots_sorted_sources_and_refuses_overwrite(store):
    original = store.snapshot()
    assert original["accepted_head"] == original["base_sha"] == "base-sha"
    assert original["status"] == "ready"
    assert original["state_dir"] == str(store.directory)
    assert [item["number"] for item in original["issues"]] == [3, 12]
    assert original["issues"][0]["source"]["title"] == "First"
    assert original["issues"][0]["state"] == "queued"
    assert original["issues"][0]["feedback"] == []
    assert original["calls"] == []
    with pytest.raises(ValueError, match="already"):
        store.create(original["spec"], [], "different")
    assert store.snapshot() == original


@pytest.mark.parametrize("issues", [
    [{"number": 1}, {"number": 1}], [{"number": 0}], [{"number": -3}],
    [{"number": True}], [{"number": "3"}], [{}], [None],
])
def test_invalid_issue_numbers_fail_before_creating_state(tmp_path, issues):
    store = IssueStore(tmp_path / "missing")
    with pytest.raises(ValueError, match="issue|Issue"):
        store.create({"limits": {"max_invocations": 1, "max_wall_seconds": 1}}, issues, "base")
    assert not store.path.exists()


@pytest.mark.parametrize("operation", [
    lambda store: store.snapshot(), lambda store: store.control(),
    lambda store: store.begin(), lambda store: store.stop(),
    lambda store: store.set_status("running"), lambda store: store.events(),
    lambda store: store.event("x", {}), lambda store: store.reserve_call("plan", 1),
    lambda store: store.update_issue(1, "queued"), lambda store: store.has_unfinished_work(),
    lambda store: store.recover(acknowledge=True),
])
def test_uninitialized_reads_and_writes_do_not_create_database(tmp_path, operation):
    store = IssueStore(tmp_path / "state")
    with pytest.raises(ValueError, match="No issue batch"):
        operation(store)
    assert not store.path.exists()


def test_issue_patches_preserve_existing_fields_and_sources(store):
    store.update_issue(3, "planning", plan={"steps": ["reproduce"]}, child_state="child", feedback=["first"])
    store.update_issue(3, "waiting_human", reason="needs product decision")
    item = store.snapshot()["issues"][0]
    assert item["plan"] == {"steps": ["reproduce"]}
    assert item["child_state"] == "child"
    assert item["source"]["title"] == "First"
    assert item["feedback"] == ["first"]
    assert item["reason"] == "needs product decision"
    with pytest.raises(ValueError, match="Unknown issue"):
        store.update_issue(99, "queued")


def test_budget_reservations_are_atomic_and_unknown_calls_stay_charged(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        calls = list(pool.map(lambda _: store.reserve_call("plan", 3), range(10)))
    reserved = [call for call in calls if call is not None]
    assert len(reserved) == len(set(reserved)) == 4
    store.finish_call(reserved[0], {"message": "lost response"}, unknown=True)
    snapshot = store.snapshot()
    assert snapshot["invocations"] == 4
    assert snapshot["status"] == "limited"
    assert snapshot["reason"] == "invocation_limit"
    assert snapshot["calls"][0]["state"] == "unknown"
    with pytest.raises(RuntimeError, match="settled"):
        store.finish_call(reserved[0], {})


def test_begin_and_recovery_preserve_first_deadline_across_downtime(store):
    with patch("dutyloop.issue_store.time.time", return_value=100):
        assert store.begin()
        call = store.reserve_call("plan", 3)
        store.update_issue(3, "implementing", branch="fix/issue-3", head="work-head")
    with patch("dutyloop.issue_store.time.time", return_value=140):
        store.recover(acknowledge=True)
        assert store.begin()
    snapshot = store.snapshot()
    assert snapshot["opened_at"] == 100
    assert snapshot["deadline"] == 160
    assert snapshot["invocations"] == 1
    assert snapshot["calls"][0]["id"] == call
    assert snapshot["calls"][0]["state"] == "unknown"
    item = snapshot["issues"][0]
    assert item["state"] == "interrupted"
    assert item["previous_state"] == "implementing"
    assert item["branch"] == "fix/issue-3"
    assert item["head"] == "work-head"
    with patch("dutyloop.issue_store.time.time", return_value=161):
        assert not store.begin()
        assert store.reserve_call("plan", 12) is None
    assert store.control()["reason"] == "wall_time_limit"


@pytest.mark.parametrize("phase", ["planning", "implementing", "publishing", "merging", "closing"])
def test_recovery_requires_ack_and_never_replays_incomplete_work(store, phase):
    store.update_issue(3, phase, pr_number=8, feedback=["retain evidence"])
    assert store.has_unfinished_work()
    with pytest.raises(ValueError, match="acknowledge"):
        store.recover()
    assert store.snapshot()["issues"][0]["state"] == phase
    store.recover(acknowledge=True)
    item = store.snapshot()["issues"][0]
    assert item["state"] == "interrupted"
    assert item["pr_number"] == 8
    assert item["feedback"] == ["retain evidence"]
    assert not store.has_unfinished_work()


@pytest.mark.parametrize("status, reason", [
    ("blocked", "scope_violation"), ("limited", "invocation_limit"),
    ("stopped", "operator_stop"), ("completed", "queue_finished"),
])
def test_recovery_does_not_revive_terminal_state(store, status, reason):
    store.set_status(status, reason)
    store.recover(acknowledge=True)
    assert store.control()["status"] == status
    assert store.control()["reason"] == reason
    assert not store.begin()
    assert store.reserve_call("plan", 3) is None


def test_stop_is_sticky_during_status_changes_and_recovery(store):
    assert store.begin()
    store.stop()
    assert store.control()["status"] == "stopping"
    store.set_status("running")
    assert store.control()["status"] == "stopping"
    store.recover(acknowledge=True)
    snapshot = store.control()
    assert snapshot["status"] == "stopped"
    assert snapshot["stop_requested"]
    assert snapshot["runner_pid"] is None
    assert not store.begin()
    assert store.reserve_call("plan", 3) is None


def test_events_have_stable_bounded_cursor(store):
    for sequence in range(3):
        store.event("progress", {"sequence": sequence})
    first = store.events(limit=2)
    second = store.events(after_id=first[-1]["id"], limit=2)
    assert [row["data"]["sequence"] for row in first + second] == [0, 1, 2]
    assert store.events(after_id=second[-1]["id"]) == []
    for options in ({"after_id": -1}, {"after_id": True}, {"limit": 0}, {"limit": 1001}):
        with pytest.raises(ValueError):
            store.events(**options)


def test_reserved_metadata_cannot_be_overwritten_through_issue_patch(store):
    with pytest.raises(ValueError, match="reserved"):
        store.update_issue(3, "queued", number=12)
    with pytest.raises(ValueError, match="reserved"):
        store.update_issue(3, "queued", source={"number": 99})


def test_explicit_source_refresh_preserves_work_and_audits_old_and_new_hashes(store):
    store.update_issue(3, "needs_decision", plan={"summary": "old plan"}, feedback=["retain"])
    before = store.snapshot()["issues"][0]
    source = {**before["source"], "title": "Updated title", "comments": [{"body": "New acceptance"}]}
    store.refresh_source(3, source)
    after = store.snapshot()["issues"][0]
    assert after == {**before, "source": source}
    assert store.control()["invocations"] == 0
    audit = store.events()
    assert len(audit) == 1
    assert audit[0]["kind"] == "issue_source_refreshed"

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()

    assert audit[0]["data"] == {"number": 3, "previous_source_sha256": digest(before["source"]),
                                 "source_sha256": digest(source)}


@pytest.mark.parametrize("number,source", [
    (3, None), (3, []), (3, {}), (3, {"number": 12}),
    (3, {"number": True}), (True, {"number": 1}),
])
def test_source_refresh_rejects_invalid_or_different_identity(store, number, source):
    before = store.snapshot()
    with pytest.raises(ValueError, match="source|number"):
        store.refresh_source(number, source)
    assert store.snapshot() == before
    assert not store.events()


def test_source_refresh_rejects_unknown_issue(store):
    with pytest.raises(ValueError, match="Unknown issue"):
        store.refresh_source(99, {"number": 99})
    assert not store.events()


def test_source_refresh_rolls_back_when_audit_cannot_be_written(store):
    before = store.snapshot()
    with store.connection() as db:
        db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON events "
                   "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        store.refresh_source(3, {"number": 3, "title": "Changed"})
    assert store.snapshot() == before
    assert not store.events()


def test_source_refresh_without_initialized_batch_does_not_create_state(tmp_path):
    store = IssueStore(tmp_path / "missing")
    with pytest.raises(ValueError, match="No issue batch"):
        store.refresh_source(1, {"number": 1})
    assert not store.path.exists()
