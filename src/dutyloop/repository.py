"""Persistent, scope-bound repository intake over the finite issue executor.

The foreground coordinator must be run by a host. It neither wakes a desktop
agent nor renews the operator's finite budget when a poll or process restarts.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import sqlite3
import threading
import time

from . import backlog_runner, issues
from .github import GitHubError
from .issue_store import INCOMPLETE_ISSUES, IssueStore
from .repository_store import RepositoryStore, encoded, fingerprint, scope_fingerprint
from .store import Store, runner_lock


def _intake(value):
    if not isinstance(value, dict) or set(value) - {"mode", "poll_seconds"}:
        raise ValueError("intake must contain only mode and poll_seconds")
    mode, seconds = value.get("mode", "observe"), value.get("poll_seconds", 30)
    if mode not in {"observe", "auto"}:
        raise ValueError("intake.mode must be observe or auto")
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or seconds <= 0):
        raise ValueError("intake.poll_seconds must be a positive finite number")
    return {"mode": mode, "poll_seconds": seconds}


def is_repository(state_dir):
    return RepositoryStore(state_dir).exists()


def initialize(spec, state_dir, *, github=None):
    if not isinstance(spec, dict):
        raise ValueError("Repository configuration must be an object")
    intake = _intake(spec.get("intake", {}))
    store = RepositoryStore(state_dir)
    store.directory.mkdir(parents=True, exist_ok=True)
    marker = store.directory / "repository.initializing"
    if store.path.exists():
        raise ValueError("State directory already contains an issue batch")
    # Finite CLI/MCP dispatch must recognize the repository before issue rows
    # exist. A process death leaves the marker and cannot grant auto authority.
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Repository initialization is incomplete; inspect its retained state") from exc
    os.close(descriptor)
    try:
        issues.initialize({key: value for key, value in spec.items() if key != "intake"}, state_dir, github=github)
        store.create(intake)
    except BaseException:
        if not store.path.exists():
            marker.unlink(missing_ok=True)
        raise
    marker.unlink()
    return status(state_dir)


def _client(store, github=None, *, interrupted=None):
    if github is not None:
        return github
    data = store.issues.control()

    def stopped():
        current = store.issues.control()
        return (bool(interrupted and interrupted.is_set()) or current["stop_requested"]
                or current["deadline"] is not None and current["deadline"] <= time.time())

    remaining = max(.001, data["deadline"] - time.time()) if data["deadline"] is not None else 30
    return issues._client(data["spec"], store.directory, should_stop=stopped, timeout=min(30, remaining))


def _new_issue(source):
    return {"source": source, "plan": None, "child_state": None, "branch": None,
            "head": None, "pr_number": None, "feedback": []}


def reconcile(state_dir, *, github=None):
    """GET a complete remote snapshot, then atomically record/admit its changes."""
    store = RepositoryStore(state_dir)
    with runner_lock(store.directory):
        before = store.snapshot()
        client = _client(store, github)
        if client.repository()["full_name"].casefold() != before["spec"]["github"]["repository"].casefold():
            raise ValueError("GitHub repository identity mismatch")
        found, duplicates = {}, 0
        for source in client.list_open_issues():
            number = source.get("number") if isinstance(source, dict) else None
            if type(number) is not int or number < 1 or source.get("state") not in {"open", "closed"}:
                raise ValueError("Repository intake returned an invalid issue")
            if number in found:
                if found[number] != source:
                    raise ValueError("Repository intake returned conflicting duplicate issues")
                duplicates += 1
            found[number] = source
        # Missing from an open listing is not evidence of closure by itself.
        for row in before["sources"]:
            if row["number"] not in found and row["source"].get("state") == "open":
                source = client.get_issue(row["number"])
                if source.get("number") != row["number"] or source.get("state") not in {"open", "closed"}:
                    raise ValueError("Repository intake returned a mismatched issue")
                found[row["number"]] = source
        selected = set(before["spec"]["queue"]["numbers"])
        found = {number: source for number, source in found.items() if not selected or number in selected}
        digest = fingerprint([found[number] for number in sorted(found)])
        with store.issues.connection() as db:
            metadata, batch = store.metadata(db), IssueStore._read(db)
            rows = {row["number"]: row for row in db.execute("SELECT * FROM issues")}
            known = {row["number"]: row for row in store.sources(db)}
            now = time.time()
            limit = IssueStore._limit(batch, now)
            enabled = (metadata["intake"]["mode"] == "auto" and not batch["stop_requested"]
                       and batch["status"] not in {"limited", "blocked", "stopped"} and limit is None)
            admitted, changed, observed = [], [], []
            for number, source in sorted(found.items()):
                previous = known.get(number)
                source_hash, scope_hash = fingerprint(source), scope_fingerprint(source)
                scope_changed = previous is not None and previous["scope_fingerprint"] != scope_hash
                reason = ("external_closed" if source["state"] != "open" else
                          "excluded_label" if set(source.get("labels", [])) & set(batch["spec"]["queue"]["exclude_labels"]) else
                          "observe_only" if metadata["intake"]["mode"] == "observe" else
                          "control_inactive" if not enabled else
                          "max_issues_reached" if len(rows) >= batch["spec"]["queue"]["max_issues"] else None)
                if previous is None or previous["fingerprint"] != source_hash:
                    observed.append(number)
                    store.event(db, "repository_source_observed", {
                        "number": number, "source_sha256": source_hash,
                        "previous_source_sha256": previous["fingerprint"] if previous else None,
                        "scope_changed": scope_changed})
                if number in rows:
                    row, item = rows[number], json.loads(rows[number]["data"])
                    if row["state"] not in INCOMPLETE_ISSUES | {"interrupted"}:
                        if (scope_fingerprint(item["source"]) != scope_hash
                                and item.get("pending_scope_sha256") != scope_hash):
                            item.update(reason="issue_changed_since_snapshot", previous_state=row["state"],
                                        pending_scope_sha256=scope_hash)
                            db.execute("UPDATE issues SET state='needs_decision',data=? WHERE number=?",
                                       (encoded(item), number))
                            changed.append(number)
                            store.event(db, "repository_scope_changed", {"number": number, "source_sha256": source_hash})
                        elif source["state"] == "closed" and row["state"] not in {"closed", "delivered", "external_closed"}:
                            item["reason"] = "closed_outside_batch"
                            db.execute("UPDATE issues SET state='external_closed',data=? WHERE number=?", (encoded(item), number))
                    reason = "issue_changed_since_snapshot" if scope_changed else None
                elif reason is None:
                    db.execute("INSERT INTO issues VALUES(?,'queued',?)", (number, encoded(_new_issue(source))))
                    rows[number] = {"number": number}
                    admitted.append(number)
                    store.event(db, "repository_issue_admitted", {"number": number, "source_sha256": source_hash})
                db.execute("INSERT INTO repository_sources VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(number) DO UPDATE SET "
                           "fingerprint=excluded.fingerprint,scope_fingerprint=excluded.scope_fingerprint,"
                           "source=excluded.source,admitted=excluded.admitted,reason=excluded.reason,last_changed=excluded.last_changed",
                           (number, source_hash, scope_hash, encoded(source), int(number in rows), reason,
                            previous["first_seen"] if previous else now,
                            now if previous is None or previous["fingerprint"] != source_hash else previous["last_changed"]))
            receipt = {"observed": observed, "changed": changed, "admitted": admitted, "duplicates": duplicates,
                       "numbers": sorted(found), "mode": metadata["intake"]["mode"]}
            # A stable unchanged poll advances no event cursor and creates no repeated receipt.
            if metadata["last_fingerprint"] != digest or admitted or changed:
                cursor = db.execute("INSERT INTO repository_receipts(at,fingerprint,data) VALUES(?,?,?)",
                                    (now, digest, encoded(receipt)))
                metadata.update(last_fingerprint=digest, last_receipt_id=cursor.lastrowid)
                store.event(db, "repository_reconciled", {"receipt_id": cursor.lastrowid, **receipt})
            metadata["last_reconciled_at"] = now
            metadata["source_error"] = None
            if metadata["coordinator_pid"] is not None:
                metadata["coordinator_heartbeat_at"] = now
            store.save_metadata(db, metadata)
            if admitted and batch["status"] == "completed" and enabled:
                batch.update(status="ready", reason="repository_work_available")
                IssueStore._save(db, batch)
    return status(state_dir)


def set_mode(state_dir, mode):
    """Explicitly change future intake authority without renewing any budget."""
    if mode not in {"observe", "auto"}:
        raise ValueError("Repository mode must be observe or auto")
    store = RepositoryStore(state_dir)
    with runner_lock(store.directory), store.issues.connection() as db:
        metadata = store.metadata(db)
        previous = metadata["intake"]["mode"]
        if mode != previous:
            metadata["intake"]["mode"] = mode
            metadata["policy_revision"] = metadata.get("policy_revision", 0) + 1
            store.save_metadata(db, metadata)
            store.event(db, "repository_mode_changed", {"previous": previous, "mode": mode,
                                                        "policy_revision": metadata["policy_revision"]})
    return status(state_dir)


def _work(snapshot):
    if backlog_runner.needs_assessment(snapshot):
        return True
    if snapshot["intake"]["mode"] == "observe":
        return False
    if any(row["state"] in {"accepted", "pull_request", "merged"}
           or row.get("pr_number") and row["state"] in {"queued", "planned", "waiting_dependency"}
           for row in snapshot["issues"]):
        return True
    planning = snapshot.get("planning") or {}
    if planning.get("status") == "ready" and planning.get("roadmap"):
        _, work, _ = backlog_runner.select_work(snapshot)
        return bool(work)
    return False


def _wait(store, interrupted, until):
    while time.time() < until and not interrupted.is_set():
        if store.issues.control()["stop_requested"]:
            return True
        interrupted.wait(min(.1, max(0, until - time.time())))
    return interrupted.is_set()


def _source_error(store, error, attempts):
    """Retain bounded structured transport evidence, without raw private output."""
    with store.issues.connection() as db:
        metadata = store.metadata(db)
        value = {"reason": "github_read_failed", "at": time.time(), "attempts": attempts,
                 "timed_out": error.timed_out, "cancelled": error.cancelled,
                 "cleanup_confirmed": error.cleanup_confirmed, "outcome_unknown": error.outcome_unknown}
        metadata["source_error"] = value
        store.save_metadata(db, metadata)
        store.event(db, "repository_source_error", value)
        if not error.cleanup_confirmed or error.outcome_unknown:
            batch = IssueStore._read(db)
            batch.update(status="blocked", reason="worker_cleanup_unconfirmed")
            IssueStore._save(db, batch)


def run(state_dir, *, driver=None, github=None):
    """Poll in the foreground until the shared finite limit, stop, or blocker."""
    store = RepositoryStore(state_dir)
    store.snapshot()  # Require the marker before creating a coordinator lock.
    lock_dir = store.directory / "coordinator"
    lock_dir.mkdir(exist_ok=True)
    interrupted, handlers = threading.Event(), {}
    with runner_lock(lock_dir):
        if not store.begin():
            return status(state_dir)
        with store.issues.connection() as db:
            metadata = store.metadata(db)
            if metadata["coordinator_pid"] is not None:
                # Acquiring this OS lock proves the previous coordinator no
                # longer owns it; the child issue ledger still controls recovery.
                store.event(db, "repository_coordinator_restarted", {"previous_pid": metadata["coordinator_pid"],
                                                                     "previous_started_at": metadata["coordinator_started_at"]})
            metadata.update(coordinator_pid=os.getpid(), coordinator_started_at=time.time(),
                            coordinator_heartbeat_at=time.time())
            store.save_metadata(db, metadata)
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, lambda *_: interrupted.set())
        source_failures = 0
        try:
            while not interrupted.is_set():
                current = store.issues.control()
                if current["stop_requested"] or current["status"] in {"limited", "blocked", "stopped"}:
                    break
                with runner_lock(store.directory):
                    if store.issues.has_unfinished_work():
                        store.issues.set_status("blocked", "unclean_interruption")
                        raise RuntimeError("Interrupted repository work requires explicit recovery; effects are never replayed")
                if not store.wake(needed=False) and store.issues.control()["status"] == "limited":
                    break
                try:
                    snapshot = reconcile(state_dir, github=_client(store, github, interrupted=interrupted))
                    source_failures = 0
                except GitHubError as error:
                    current = store.issues.control()
                    if error.cleanup_confirmed and not error.outcome_unknown:
                        if error.cancelled and (current["stop_requested"] or interrupted.is_set()):
                            break
                        if current["deadline"] is not None and time.time() >= current["deadline"]:
                            store.issues.set_status("limited", "wall_time_limit")
                            break
                    source_failures += 1
                    _source_error(store, error, source_failures)
                    # Retry only a confirmed-clean read timeout, at most twice;
                    # no task was dispatched during this failed intake pass.
                    if (source_failures < 3 and error.timed_out and error.cleanup_confirmed
                            and not error.outcome_unknown and not error.cancelled):
                        until = min(time.time() + store.snapshot()["intake"]["poll_seconds"], current["deadline"])
                        if _wait(store, interrupted, until):
                            break
                        continue
                    raise
                if interrupted.is_set():
                    break
                if store.wake(needed=_work(snapshot)):
                    issues.run(state_dir, driver=driver, github=github,
                               plan_only=snapshot["intake"]["mode"] == "observe")
                current = store.issues.control()
                if current["stop_requested"] or current["status"] in {"limited", "blocked", "stopped"}:
                    break
                # Short waits make stop responsive without polling GitHub repeatedly.
                until = min(time.time() + snapshot["intake"]["poll_seconds"], current["deadline"])
                if _wait(store, interrupted, until):
                    break
        finally:
            if interrupted.is_set():
                current = store.issues.control()
                store.issues.stop()
                if current["status"] == "blocked":
                    store.issues.set_status("blocked", current["reason"])
            with store.issues.connection() as db:
                metadata = store.metadata(db)
                metadata["coordinator_pid"] = None
                store.save_metadata(db, metadata)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return status(state_dir)


def stop(state_dir):
    RepositoryStore(state_dir).snapshot()
    issues.stop(state_dir)
    return status(state_dir)


def events(state_dir, *, after_id=0, limit=20):
    RepositoryStore(state_dir).snapshot()
    return issues.events(state_dir, after_id=after_id, limit=limit)


def recover(state_dir, *, acknowledge=False, github=None):
    store = RepositoryStore(state_dir)
    store.snapshot()
    lock_dir = store.directory / "coordinator"
    lock_dir.mkdir(exist_ok=True)
    with runner_lock(lock_dir):
        issues.recover(state_dir, acknowledge=acknowledge, github=github)
        with store.issues.connection() as db:
            metadata = store.metadata(db)
            if metadata["coordinator_pid"] is not None:
                store.event(db, "repository_coordinator_recovered", {"previous_pid": metadata["coordinator_pid"]})
                metadata["coordinator_pid"] = None
                store.save_metadata(db, metadata)
    return status(state_dir)


def resolve(state_dir, number, decision, *, github=None):
    if type(number) is not int or number < 1:
        raise ValueError("Issue number must be a positive integer")
    if not isinstance(decision, str) or not decision.strip() or len(decision) > 20000:
        raise ValueError("A decision of 1..20000 characters is required")
    store = RepositoryStore(state_dir)
    snapshot = store.snapshot()
    if any(row["number"] == number for row in snapshot["issues"]):
        issues.resolve(state_dir, number, decision, github=github)
        return status(state_dir)
    # Explicit admission remains plan-only under observe mode.
    with runner_lock(store.directory):
        source = next((row for row in snapshot["sources"] if row["number"] == number), None)
        if source is None:
            raise ValueError("Only an observed issue may be admitted")
        fresh = _client(store, github).get_issue(number)
        if fresh.get("number") != number or fresh.get("state") != "open":
            raise ValueError("Only an open observed issue may be admitted")
        if set(fresh.get("labels", [])) & set(snapshot["spec"]["queue"]["exclude_labels"]):
            raise ValueError("Remove excluded labels before admitting this issue")
        with store.issues.connection() as db:
            batch = IssueStore._read(db)
            if batch["stop_requested"] or batch["status"] in {"limited", "blocked", "stopped"} or IssueStore._limit(batch, time.time()):
                raise ValueError("Repository control must permit work before admitting an issue")
            count = db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
            if count >= batch["spec"]["queue"]["max_issues"]:
                raise ValueError("Repository max_issues has been reached")
            data = {**_new_issue(fresh), "decision": decision.strip()}
            db.execute("INSERT INTO issues VALUES(?,'queued',?)", (number, encoded(data)))
            db.execute("UPDATE repository_sources SET fingerprint=?,scope_fingerprint=?,source=?,admitted=1,reason=NULL WHERE number=?",
                       (fingerprint(fresh), scope_fingerprint(fresh), encoded(fresh), number))
            store.event(db, "operator_decision", {"number": number, "decision": decision.strip(), "admitted": True})
            if batch["status"] == "completed":
                batch.update(status="ready", reason="operator_decision")
                IssueStore._save(db, batch)
    return status(state_dir)


def _children(store):
    children = {}
    directory = store.directory / "missions"
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if not (path / "mission.sqlite3").is_file():
                continue
            child = Store(path).snapshot()
            match = re.fullmatch(r"work/issue-(\d+)-(?:plan|fix|feedback)-[0-9a-f]+", child.get("branch") or "")
            if match:
                children.setdefault(int(match[1]), []).append({
                    "id": path.name, "state_dir": str(path), "status": child["status"],
                    "reason": child.get("reason"), "tasks": child["tasks"], "calls": child["calls"]})
    return children


def _executor(store, snapshot):
    desktop = snapshot["spec"]["driver"]["name"] == "desktop"
    result = {"mode": "desktop" if desktop else "native", "host_driven": desktop,
              "status": "idle" if desktop else snapshot["status"],
              "cannot_wake_host": desktop, "cannot_terminate_external": desktop,
              "pending": 0, "claimed": 0, "oldest_pending_age_seconds": None,
              "oldest_claimed_age_seconds": None, "requests": []}
    path = store.directory / "desktop.sqlite3"
    if not desktop or not path.is_file():
        return result
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'").fetchone():
            return result
        for row in db.execute("SELECT request_id,phase,state,created_at,claimed_at,deadline,result FROM requests ORDER BY created_at,request_id"):
            now, state = time.time(), row["state"]
            age = max(0, now - (row["claimed_at"] if state == "claimed" else row["created_at"]))
            if state in {"pending", "claimed"}:
                result[state] += 1
                key = f"oldest_{state}_age_seconds"
                result[key] = max(result[key] or 0, age)
            receipt = json.loads(row["result"]) if row["result"] else None
            result["requests"].append({"id": row["request_id"], "phase": row["phase"], "state": state,
                                       "age_seconds": age, "deadline": row["deadline"],
                                       "overdue": now >= row["deadline"] and state in {"pending", "claimed"},
                                       "cleanup_unknown": bool(receipt and not receipt.get("cleanup_confirmed", False))})
    finally:
        db.close()
    result["status"] = ("external_work_claimed" if result["claimed"] else
                        "waiting_for_executor" if result["pending"] else "idle")
    return result


def status(state_dir):
    store = RepositoryStore(state_dir)
    snapshot = backlog_runner.present(store.snapshot())
    children = _children(store)
    snapshot["tasks"] = [{"id": f"issue:{row['number']}", "issue_number": row["number"],
                          "state": row["state"], "pr_number": row.get("pr_number"),
                          "attempts": children.get(row["number"], []),
                          "calls": [call for call in snapshot["calls"] if call["issue_number"] == row["number"]]}
                         for row in snapshot["issues"]]
    attention = []
    planning = snapshot["planning"]
    if planning["status"] in {"incomplete", "needs_decision", "stale"}:
        attention.append({"id": "repository:planning", "type": "planning", "reason": planning.get("reason") or planning["status"],
                          "coverage": planning.get("coverage"), "required_action": "inspect_backlog_assessment"})
    for item in (planning.get("roadmap") or {}).get("items", []):
        if item["disposition"] in {"needs_decision", "defer"}:
            attention.append({"id": f"issue:{item['number']}:roadmap", "type": "planning",
                              "issue_number": item["number"], "reason": item["reason"],
                              "question": item.get("question"), "required_action": "inspect_roadmap_disposition"})
    if planning.get("roadmap"):
        stage, _, gate = backlog_runner.select_work(snapshot)
        if stage and gate:
            attention.append({"id": f"repository:stage:{stage['id']}", "type": "planning",
                              "reason": gate, "issue_numbers": stage["issue_numbers"],
                              "question": "; ".join(stage["entry_conditions"]),
                              "required_action": "inspect_stage_and_resolve_issue_decision"})
    for row in snapshot["issues"]:
        if row["state"] in {"needs_decision", "failed", "waiting_dependency", "interrupted"}:
            attention.append({"id": f"issue:{row['number']}:{row['state']}", "type": row["state"],
                              "issue_number": row["number"], "pr_number": row.get("pr_number"),
                              "reason": row.get("reason"),
                              "question": row["plan"]["summary"] if row.get("plan", {})
                              and row["plan"].get("status") == "needs_decision" else None,
                              "required_action": "inspect_and_resolve"})
    for row in snapshot["sources"]:
        if not row["admitted"] and row["source"]["state"] == "open":
            attention.append({"id": f"issue:{row['number']}:intake", "type": "intake",
                              "issue_number": row["number"], "reason": row["reason"],
                              "required_action": "inspect_intake_policy_or_admit"})
    for call in snapshot["calls"]:
        if call["state"] == "unknown":
            attention.append({"id": f"call:{call['id']}:unknown", "type": "unknown_result",
                              "issue_number": call["issue_number"], "reason": "call_result_unknown",
                              "required_action": "inspect_retained_work_do_not_replay"})
        elif (call["state"] in {"running", "reserved"} and call.get("started_at")
              and time.time() - call["started_at"] >= snapshot["spec"]["call_timeout_seconds"]):
            attention.append({"id": f"call:{call['id']}:overdue", "type": "watchdog",
                              "issue_number": call["issue_number"], "reason": "recorded_call_timeout_elapsed",
                              "required_action": "inspect_retained_work_before_recovery", "started_at": call["started_at"]})
    executor = _executor(store, snapshot)
    for request in executor["requests"]:
        if request["state"] in {"pending", "claimed"} or request["cleanup_unknown"]:
            reason = ("external_cleanup_unknown" if request["cleanup_unknown"] else
                      "desktop_request_overdue" if request["overdue"] else f"desktop_request_{request['state']}")
            attention.append({"id": f"desktop:{request['id']}", "type": "executor", "reason": reason,
                              "request_id": request["id"], "required_action": "inspect_source_host_and_request",
                              "age_seconds": request["age_seconds"]})
    if snapshot["status"] in {"blocked", "limited", "stopped"}:
        attention.append({"id": f"repository:{snapshot['status']}", "type": "control",
                          "reason": snapshot["reason"], "required_action": "inspect_control_limits_or_recovery"})
    if snapshot["repository"].get("source_error"):
        attention.append({"id": "repository:source", "type": "source", **snapshot["repository"]["source_error"],
                          "required_action": "inspect_source_transport_before_restart"})
    heartbeat = max([snapshot["repository"].get("coordinator_heartbeat_at") or 0,
                     *[call.get("finished_at") or call.get("started_at") or 0 for call in snapshot["calls"]]])
    stale_after = snapshot["spec"]["call_timeout_seconds"] + snapshot["intake"]["poll_seconds"]
    if (snapshot["repository"]["coordinator_pid"] is not None and snapshot["runner_pid"] is None
            and heartbeat and time.time() - heartbeat > stale_after):
        attention.append({"id": "repository:heartbeat", "type": "watchdog", "reason": "coordinator_last_observation_old",
                          "required_action": "inspect_coordinator_and_retained_work", "last_heartbeat_at": heartbeat})
    snapshot.update(attention=attention, executor=executor,
                    health={"status": "attention" if attention else "idle" if not _work(snapshot) else "work_available",
                            "coordinator_recorded": snapshot["repository"]["coordinator_pid"] is not None,
                            "coordinator_liveness": "unverified",
                            "last_reconciled_at": snapshot["repository"]["last_reconciled_at"]})
    # A completed finite pass leaves the repository coordinator watching for
    # new intake. Preserve its stored result separately for host inspection.
    snapshot["pass_status"] = snapshot["status"]
    if snapshot["health"]["coordinator_recorded"] and snapshot["status"] in {"ready", "completed"}:
        snapshot["status"] = "watching"
    return snapshot
