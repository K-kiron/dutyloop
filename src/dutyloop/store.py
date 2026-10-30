"""Transactional mission state. Unknown work remains charged and is never replayed."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import plain_path


class Store:
    def __init__(self, state_dir: Path):
        self.directory = plain_path(state_dir)
        self.path = self.directory / "mission.sqlite3"

    @contextmanager
    def connection(self):
        plain_path(self.path)
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
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
        row = db.execute("SELECT value FROM mission WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Mission has not been initialized")
        return json.loads(row[0])

    @staticmethod
    def _save(db, data):
        db.execute("UPDATE mission SET value=? WHERE id=1", (json.dumps(data),))

    def create(self, spec, head, branch):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS mission (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, phase TEXT, task_id INTEGER, state TEXT, data TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, task_key TEXT UNIQUE, state TEXT, data TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL, kind TEXT, data TEXT)")
            if db.execute("SELECT 1 FROM mission").fetchone():
                raise ValueError("State directory already contains a mission")
            data = {"spec": spec, "status": "ready", "stop_requested": False, "invocations": 0,
                    "opened_at": None, "deadline": None, "accepted_head": head, "branch": branch,
                    "reason": None, "failures": 0, "runner_pid": None}
            db.execute("INSERT INTO mission VALUES(1,?)", (json.dumps(data),))

    def snapshot(self):
        if not self.path.is_file():
            raise ValueError(f"No mission exists in {self.directory}")
        with self.connection() as db:
            data = self._read(db)
            result = {**data, **{key: data["spec"][key] for key in ("mission", "project", "limits", "driver")}}
            result["tasks"] = [{"id": row["id"], "key": row["task_key"], "state": row["state"], **json.loads(row["data"])} for row in db.execute("SELECT * FROM tasks ORDER BY id")]
            result["calls"] = [{"id": row["id"], "phase": row["phase"], "task_id": row["task_id"], "state": row["state"], **json.loads(row["data"])} for row in db.execute("SELECT * FROM calls ORDER BY id")]
            result["state_dir"] = str(self.directory)
            known = [call["cost_usd"] for call in result["calls"] if call.get("cost_usd") is not None]
            result["reported_cost_usd"] = sum(known) if known else None
            result["calls_without_cost"] = sum(call.get("cost_usd") is None for call in result["calls"])
            return result

    def control(self):
        """Small read-only poll; avoid loading the audit history for every stop check."""
        plain_path(self.path)
        db = sqlite3.connect(self.path, timeout=15)
        try:
            return self._read(db)
        finally:
            db.close()

    def event(self, kind, data):
        with self.connection() as db:
            db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)", (time.time(), kind, json.dumps(data)))

    def set_status(self, status, reason=None, **fields):
        with self.connection() as db:
            data = self._read(db)
            if data["stop_requested"] and status not in {"stopped", "blocked"}:
                status = "stopping"
            data.update(status=status, reason=reason, **fields)
            self._save(db, data)

    def begin(self):
        with self.connection() as db:
            data = self._read(db)
            if data["status"] in {"limited", "stopped", "blocked"} or data["stop_requested"]:
                return False
            now = time.time()
            if data["deadline"] is None:
                data.update(opened_at=now, deadline=now + data["spec"]["limits"]["max_wall_seconds"])
            data.update(status="running", runner_pid=os.getpid())
            self._save(db, data)
            return True

    def stop(self):
        with self.connection() as db:
            data = self._read(db)
            data.update(stop_requested=True, status="stopping" if data["runner_pid"] else "stopped", reason="operator_stop")
            self._save(db, data)

    def reserve_call(self, phase, task_id=None):
        with self.connection() as db:
            data = self._read(db)
            if data["stop_requested"] or data["status"] in {"limited", "blocked", "stopped"}:
                return None
            now = time.time()
            if data["deadline"] is None:
                data.update(opened_at=now, deadline=now + data["spec"]["limits"]["max_wall_seconds"])
            reason = "invocation_limit" if data["invocations"] >= data["spec"]["limits"]["max_invocations"] else "wall_time_limit" if now >= data["deadline"] else None
            if reason:
                data.update(status="limited", reason=reason)
                self._save(db, data)
                return None
            data["invocations"] += 1
            cursor = db.execute("INSERT INTO calls(phase,task_id,state,data) VALUES(?,?,?,?)", (phase, task_id, "reserved", json.dumps({"started_at": now})))
            self._save(db, data)
            return cursor.lastrowid

    def finish_call(self, call_id, result):
        with self.connection() as db:
            row = db.execute("SELECT state,data FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row["state"] not in {"reserved", "running"}:
                raise RuntimeError("Call was already settled or is unknown")
            data = {**json.loads(row["data"]), **result, "finished_at": time.time()}
            db.execute("UPDATE calls SET state='finished',data=? WHERE id=?", (json.dumps(data), call_id))

    def has_unfinished_calls(self):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM calls WHERE state IN ('reserved','running') LIMIT 1").fetchone() is not None

    def has_unfinished_work(self):
        with self.connection() as db:
            return bool(self._read(db)["runner_pid"] is not None
                        or db.execute("SELECT 1 FROM calls WHERE state IN ('reserved','running') LIMIT 1").fetchone()
                        or db.execute("SELECT 1 FROM tasks WHERE state IN ('running','verifying') LIMIT 1").fetchone())

    def recover_unknown(self):
        with self.connection() as db:
            db.execute("UPDATE calls SET state='unknown' WHERE state IN ('reserved','running')")
            db.execute("UPDATE tasks SET state='unknown' WHERE state IN ('running','verifying')")
            data = self._read(db)
            data.update(status="stopped" if data["stop_requested"] else "ready", runner_pid=None, reason="recovered_unknown_work")
            self._save(db, data)

    def add_task(self, candidate, workspace, base):
        with self.connection() as db:
            data = {"candidate": candidate, "worktree": str(workspace), "base_sha": base, "feedback": [], "commit_sha": None}
            cursor = db.execute("INSERT INTO tasks(task_key,state,data) VALUES(?,?,?)", (candidate["key"], "running", json.dumps(data)))
            return cursor.lastrowid

    def update_task(self, task_id, state, **fields):
        with self.connection() as db:
            row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown task")
            data = {**json.loads(row["data"]), **fields}
            db.execute("UPDATE tasks SET state=?,data=? WHERE id=?", (state, json.dumps(data), task_id))

    def integrate(self, task_id, head, previous, update_ref, **fields):
        """Linearize integration against stop. Git/SQLite crash mismatch is detected on restart."""
        with self.connection() as db:
            data = self._read(db)
            if data["stop_requested"] or data["status"] == "blocked" or time.time() >= data["deadline"]:
                return False
            if data["accepted_head"] != previous:
                raise RuntimeError("Recorded integration base changed")
            row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown task")
            update_ref()
            task = {**json.loads(row["data"]), **fields, "commit_sha": head}
            db.execute("UPDATE tasks SET state='accepted',data=? WHERE id=?", (json.dumps(task), task_id))
            data.update(accepted_head=head, status="running", failures=0, reason=None)
            self._save(db, data)
            return True

    def failure(self, reason):
        with self.connection() as db:
            data = self._read(db)
            data["failures"] += 1
            if data["failures"] >= data["spec"]["max_failures"]:
                data.update(status="blocked", reason=reason)
            self._save(db, data)


@contextmanager
def runner_lock(directory: Path):
    """An OS lock, automatically released on process death; never steal a PID lock."""
    path = plain_path(directory / "runner.lock")
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another supervisor owns this mission") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
