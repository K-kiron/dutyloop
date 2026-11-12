"""Transactional issue batch state; interrupted work is retained, never replayed."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import plain_path

INCOMPLETE_ISSUES = {"planning", "implementing", "publishing", "merging", "closing"}
TERMINAL = {"limited", "blocked", "stopped", "completed", "complete", "failed"}
RECOVERABLE = {
    "unclean_interruption", "supervisor_interrupted", "worker_cleanup_unconfirmed",
    "verification_cleanup_unconfirmed", "git_cleanup_unconfirmed",
}


class IssueStore:
    def __init__(self, directory: Path):
        self.directory = plain_path(directory)
        self.path = self.directory / "issues.sqlite3"

    def _open(self, *, readonly=False):
        plain_path(self.path)
        if not self.path.is_file():
            raise ValueError(f"No issue batch exists in {self.directory}")
        db = sqlite3.connect(
            f"{self.path.as_uri()}?mode={'ro' if readonly else 'rw'}",
            timeout=15, isolation_level=None, uri=True,
        )
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def connection(self):
        """Serialize mutations and snapshots without creating a missing database."""
        db = self._open()
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read(db):
        try:
            row = db.execute("SELECT value FROM batch WHERE id=1").fetchone()
        except sqlite3.OperationalError as exc:
            raise ValueError("No issue batch has been initialized") from exc
        if row is None:
            raise ValueError("No issue batch has been initialized")
        return json.loads(row[0])

    @staticmethod
    def _save(db, data):
        db.execute("UPDATE batch SET value=? WHERE id=1", (json.dumps(data),))

    @staticmethod
    def _snapshot(db, directory):
        result = IssueStore._read(db)
        result["issues"] = [
            {"number": row["number"], "state": row["state"], **json.loads(row["data"])}
            for row in db.execute("SELECT * FROM issues ORDER BY number")
        ]
        result["calls"] = [
            {"id": row["id"], "phase": row["phase"], "issue_number": row["issue_number"],
             "state": row["state"], **json.loads(row["data"])}
            for row in db.execute("SELECT * FROM calls ORDER BY id")
        ]
        result["state_dir"] = str(directory)
        return result

    def create(self, spec, issues, base_sha):
        numbers = set()
        for issue in issues:
            number = issue.get("number") if isinstance(issue, dict) else None
            if type(number) is not int or number < 1 or number in numbers:
                raise ValueError("Issues must have unique positive integer issue numbers")
            numbers.add(number)
        # Serialize before touching disk so malformed caller data cannot leave a partial batch.
        data = {"spec": spec, "status": "ready", "reason": None, "stop_requested": False,
                "opened_at": None, "deadline": None, "invocations": 0, "base_sha": base_sha,
                "accepted_head": base_sha, "runner_pid": None}
        encoded = json.dumps(data)
        rows = [(issue["number"], "queued", json.dumps({
            "source": issue, "plan": None, "child_state": None, "branch": None,
            "head": None, "pr_number": None, "feedback": [],
        })) for issue in issues]
        self.directory.mkdir(parents=True, exist_ok=True)
        plain_path(self.path)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError("State directory already contains an issue batch") from exc
        os.close(descriptor)
        try:
            with self.connection() as db:
                db.execute("CREATE TABLE batch (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("CREATE TABLE issues (number INTEGER PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL)")
                db.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, phase TEXT, issue_number INTEGER, state TEXT, data TEXT)")
                db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, at REAL, kind TEXT, data TEXT)")
                db.execute("INSERT INTO batch VALUES(1,?)", (encoded,))
                db.executemany("INSERT INTO issues VALUES(?,?,?)", rows)
                return self._snapshot(db, self.directory)
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise

    def snapshot(self):
        with self.connection() as db:
            return self._snapshot(db, self.directory)

    def control(self):
        """Poll only control metadata, without reading issues or call history."""
        db = self._open(readonly=True)
        try:
            return self._read(db)
        finally:
            db.close()

    @staticmethod
    def _limit(data, now):
        if data["deadline"] is not None and now >= data["deadline"]:
            return "wall_time_limit"
        if data["invocations"] >= data["spec"]["limits"]["max_invocations"]:
            return "invocation_limit"
        return None

    @staticmethod
    def _open_clock(data, now):
        if data["deadline"] is None:
            data.update(opened_at=now, deadline=now + data["spec"]["limits"]["max_wall_seconds"])

    def begin(self):
        with self.connection() as db:
            data = self._read(db)
            if data["stop_requested"] or data["status"] in TERMINAL:
                return False
            now = time.time()
            self._open_clock(data, now)
            reason = self._limit(data, now)
            if reason:
                data.update(status="limited", reason=reason)
                self._save(db, data)
                return False
            data.update(status="running", reason=None, runner_pid=os.getpid())
            self._save(db, data)
            return True

    def set_status(self, status, reason=None, **fields):
        if {"spec", "stop_requested", "invocations", "opened_at", "deadline", "base_sha"} & fields.keys():
            raise ValueError("Cannot patch reserved batch control fields")
        with self.connection() as db:
            data = self._read(db)
            if data["stop_requested"] and status not in {"stopped", "blocked"}:
                status, reason = "stopping", "operator_stop"
            data.update(status=status, reason=reason, **fields)
            self._save(db, data)

    def stop(self):
        with self.connection() as db:
            data = self._read(db)
            data.update(stop_requested=True, status="stopping" if data["runner_pid"] else "stopped",
                        reason="operator_stop")
            self._save(db, data)

    def save_planning(self, planning):
        """Persist assessment evidence without changing authority or control state."""
        with self.connection() as db:
            data = self._read(db)
            data["planning"] = planning
            self._save(db, data)

    def update_issue(self, issue_number, state, **fields):
        if {"number", "state", "source"} & fields.keys():
            raise ValueError("Cannot patch reserved issue fields")
        with self.connection() as db:
            self._read(db)
            row = db.execute("SELECT data FROM issues WHERE number=?", (issue_number,)).fetchone()
            if row is None:
                raise ValueError("Unknown issue")
            data = {**json.loads(row["data"]), **fields}
            db.execute("UPDATE issues SET state=?,data=? WHERE number=?", (state, json.dumps(data), issue_number))

    def refresh_source(self, number, source):
        """Explicitly approve a new issue snapshot and atomically audit its identity."""
        if (type(number) is not int or number < 1 or not isinstance(source, dict)
                or type(source.get("number")) is not int or source["number"] != number):
            raise ValueError("Issue source must be an object with the same positive issue number")

        def digest(value):
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        new_digest = digest(source)
        with self.connection() as db:
            self._read(db)
            row = db.execute("SELECT data FROM issues WHERE number=?", (number,)).fetchone()
            if row is None:
                raise ValueError("Unknown issue")
            data = json.loads(row["data"])
            audit = {"number": number, "previous_source_sha256": digest(data["source"]),
                     "source_sha256": new_digest}
            data["source"] = source
            db.execute("UPDATE issues SET data=? WHERE number=?", (json.dumps(data), number))
            db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)",
                       (time.time(), "issue_source_refreshed", json.dumps(audit)))

    def reserve_call(self, phase, issue_number=None):
        with self.connection() as db:
            data = self._read(db)
            if data["stop_requested"] or data["status"] in TERMINAL:
                return None
            if issue_number is not None and not db.execute(
                "SELECT 1 FROM issues WHERE number=?", (issue_number,),
            ).fetchone():
                raise ValueError("Unknown issue")
            now = time.time()
            self._open_clock(data, now)
            reason = self._limit(data, now)
            if reason:
                data.update(status="limited", reason=reason)
                self._save(db, data)
                return None
            cursor = db.execute(
                "INSERT INTO calls(phase,issue_number,state,data) VALUES(?,?,?,?)",
                (phase, issue_number, "running", json.dumps({"started_at": now})),
            )
            data["invocations"] += 1
            self._save(db, data)
            return cursor.lastrowid

    def finish_call(self, call_id, result, *, unknown=False):
        if {"id", "phase", "issue_number", "state", "started_at", "finished_at"} & result.keys():
            raise ValueError("Cannot overwrite reserved call fields")
        with self.connection() as db:
            self._read(db)
            row = db.execute("SELECT state,data FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row["state"] not in {"running", "reserved"}:
                raise RuntimeError("Call was already settled or is unknown")
            data = {**json.loads(row["data"]), **result, "finished_at": time.time()}
            db.execute("UPDATE calls SET state=?,data=? WHERE id=?",
                       ("unknown" if unknown else "finished", json.dumps(data), call_id))

    def has_unfinished_work(self):
        with self.connection() as db:
            data = self._read(db)
            if data["runner_pid"] is not None or db.execute(
                "SELECT 1 FROM calls WHERE state IN ('reserved','running') LIMIT 1",
            ).fetchone():
                return True
            placeholders = ",".join("?" for _ in INCOMPLETE_ISSUES)
            return db.execute(
                f"SELECT 1 FROM issues WHERE state IN ({placeholders}) LIMIT 1", tuple(INCOMPLETE_ISSUES),
            ).fetchone() is not None

    def recover(self, acknowledge=False):
        """Caller must hold runner_lock; acknowledgement cannot replay work or renew limits."""
        if acknowledge is not True:
            raise ValueError("Recovery requires acknowledge=True for uncertain previous work")
        with self.connection() as db:
            data = self._read(db)
            now = time.time()
            for row in db.execute("SELECT id,data FROM calls WHERE state IN ('reserved','running')").fetchall():
                call = {**json.loads(row["data"]), "finished_at": now, "reason": "unclean_interruption"}
                db.execute("UPDATE calls SET state='unknown',data=? WHERE id=?", (json.dumps(call), row["id"]))
            for row in db.execute("SELECT number,state,data FROM issues").fetchall():
                if row["state"] in INCOMPLETE_ISSUES:
                    issue = {**json.loads(row["data"]), "reason": "unclean_interruption", "previous_state": row["state"]}
                    db.execute("UPDATE issues SET state='interrupted',data=? WHERE number=?",
                               (json.dumps(issue), row["number"]))
            if data["stop_requested"] or data["status"] == "stopped":
                data.update(status="stopped", reason="operator_stop")
            elif data["status"] in TERMINAL and not (
                data["status"] == "blocked" and data["reason"] in RECOVERABLE
            ):
                pass
            elif reason := self._limit(data, now):
                data.update(status="limited", reason=reason)
            else:
                data.update(status="ready", reason="recovered_unknown_work")
            data["runner_pid"] = None
            self._save(db, data)

    def event(self, kind, data):
        with self.connection() as db:
            self._read(db)
            db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)", (time.time(), kind, json.dumps(data)))

    def events(self, after_id=0, limit=20):
        if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("after_id must be a nonnegative integer and limit must be 1..1000")
        with self.connection() as db:
            self._read(db)
            return [
                {"id": row["id"], "at": row["at"], "kind": row["kind"], "data": json.loads(row["data"])}
                for row in db.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit))
            ]
