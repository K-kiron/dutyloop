"""Repository intake metadata sharing the finite issue ledger and its budgets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from .issue_store import IssueStore


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(source):
    return hashlib.sha256(encoded(source).encode("utf-8")).hexdigest()


def scope_fingerprint(source):
    return fingerprint({key: source.get(key) for key in ("title", "body", "comments", "labels")})


class RepositoryStore:
    """Do not duplicate executable tasks, clocks, calls, stop flags, or events."""

    def __init__(self, state_dir):
        self.issues = IssueStore(state_dir)
        self.directory, self.path = self.issues.directory, self.issues.path

    def exists(self):
        if (self.directory / "repository.initializing").exists():
            return True
        if not self.path.is_file():
            return False
        db = self.issues._open(readonly=True)
        try:
            return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repository_meta'").fetchone() is not None
        finally:
            db.close()

    @staticmethod
    def metadata(db):
        try:
            row = db.execute("SELECT value FROM repository_meta WHERE id=1").fetchone()
        except sqlite3.OperationalError as exc:
            raise ValueError("No repository agent has been initialized") from exc
        if row is None:
            raise ValueError("No repository agent has been initialized")
        return json.loads(row[0])

    @staticmethod
    def save_metadata(db, value):
        db.execute("UPDATE repository_meta SET value=? WHERE id=1", (encoded(value),))

    @staticmethod
    def event(db, kind, value):
        db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)", (time.time(), kind, encoded(value)))

    def create(self, intake):
        with self.issues.connection() as db:
            snapshot = IssueStore._snapshot(db, self.directory)
            db.execute("CREATE TABLE repository_meta (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE repository_sources (number INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, "
                       "scope_fingerprint TEXT NOT NULL, source TEXT NOT NULL, admitted INTEGER NOT NULL, "
                       "reason TEXT, first_seen REAL NOT NULL, last_changed REAL NOT NULL)")
            db.execute("CREATE TABLE repository_receipts (id INTEGER PRIMARY KEY, at REAL NOT NULL, "
                       "fingerprint TEXT NOT NULL, data TEXT NOT NULL)")
            metadata = {"version": 1, "intake": intake, "last_reconciled_at": None,
                        "last_fingerprint": None, "last_receipt_id": 0,
                        "coordinator_pid": None, "coordinator_started_at": None}
            db.execute("INSERT INTO repository_meta VALUES(1,?)", (encoded(metadata),))
            now = time.time()
            for row in snapshot["issues"]:
                source = row["source"]
                db.execute("INSERT INTO repository_sources VALUES(?,?,?,?,?,?,?,?)",
                           (row["number"], fingerprint(source), scope_fingerprint(source), encoded(source),
                            1, row.get("reason"), now, now))
            self.event(db, "repository_initialized", {"intake": intake})

    @staticmethod
    def sources(db):
        return [{**dict(row), "source": json.loads(row["source"]), "admitted": bool(row["admitted"])}
                for row in db.execute("SELECT * FROM repository_sources ORDER BY number")]

    def snapshot(self):
        with self.issues.connection() as db:
            metadata = self.metadata(db)
            result = IssueStore._snapshot(db, self.directory)
            result.update(kind="repository", intake=metadata["intake"], repository=metadata,
                          sources=self.sources(db))
            result["receipts"] = [{**dict(row), "data": json.loads(row["data"])} for row in db.execute(
                "SELECT * FROM repository_receipts ORDER BY id DESC LIMIT 20")]
            return result

    def begin(self):
        """Start the shared finite clock even when an empty repository is idle."""
        with self.issues.connection() as db:
            self.metadata(db)
            data = IssueStore._read(db)
            if data["stop_requested"] or data["status"] in {"limited", "blocked", "stopped"}:
                return False
            now = time.time()
            IssueStore._open_clock(data, now)
            reason = IssueStore._limit(data, now)
            if reason:
                data.update(status="limited", reason=reason)
            IssueStore._save(db, data)
            return not reason

    def wake(self, *, needed):
        """Completed is a finite pass result; never revive control terminal states."""
        with self.issues.connection() as db:
            self.metadata(db)
            data = IssueStore._read(db)
            if data["stop_requested"] or data["status"] in {"limited", "blocked", "stopped"}:
                return False
            reason = IssueStore._limit(data, time.time())
            if reason:
                data.update(status="limited", reason=reason)
            elif needed and data["status"] == "completed":
                data.update(status="ready", reason="repository_work_available")
            IssueStore._save(db, data)
            return needed and reason is None
