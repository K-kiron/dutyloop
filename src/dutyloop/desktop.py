"""Durable handoffs to a cooperating desktop agent, without launching a model CLI.

Context identities and work-stopped acknowledgements are self-reported. They
are audit evidence, not authentication, process containment, or write fencing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import plain_path
from .drivers import DriverResult
from .schemas import validate_payload


_MAX_BYTES = 1024 * 1024
_ACTIVE = {"pending", "claimed"}
_SCOPE = "host_declaration"


def _identity(value, label):
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > 200
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"{label} must be a nonempty, trimmed identity of at most 200 characters")
    return value


def _encoded(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Desktop input must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("Desktop input exceeds the 1 MiB size limit")
    return encoded


def _evidence(output_dir, filename, value):
    directory = plain_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = plain_path(directory / filename)
    temporary = plain_path(directory / f".{filename}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _instructions(phase):
    shared = (
        "Use only the supplied managed worktree and obey the phase prompt and schema. "
        "Do not commit, publish, or change integration refs. Before submitting or cancelling, "
        "stop and join every tool, shell, and subagent you started for this request. "
        "work_stopped is a cooperative declaration, not proof of process containment. "
        "Worker and context IDs are self-reported."
    )
    if phase == "review":
        return (
            "Use a fresh reviewer subagent or separate fresh host context. Do not reuse any "
            "implementation context. Inspect read-only; do not edit files. " + shared
        )
    if phase == "discover":
        return "Inspect read-only; do not edit files. " + shared
    return shared


class DesktopQueue:
    """Named requests retained after expiration; only the driver creates a mailbox."""

    def __init__(self, state_dir: Path):
        self.directory = plain_path(state_dir)
        self.path = plain_path(self.directory / "desktop.sqlite3")

    @contextmanager
    def _connection(self, *, create=False):
        plain_path(self.path)
        if not create and not self.path.is_file():
            yield None
            return
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(
            f"{self.path.as_uri()}?mode={'rwc' if create else 'rw'}", timeout=15, isolation_level=None, uri=True,
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            if create:
                db.execute("""CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, generation TEXT NOT NULL,
                    phase TEXT NOT NULL, output_dir TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL, deadline REAL NOT NULL,
                    state TEXT NOT NULL, request TEXT NOT NULL,
                    worker_id TEXT, context_id TEXT, token TEXT, claimed_at REAL, result TEXT
                )""")
            elif not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'").fetchone():
                yield None
                db.commit()
                return
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _job(row):
        return {
            **json.loads(row["request"]), "status": "claimed", "worker_id": row["worker_id"],
            "context_id": row["context_id"], "token": row["token"], "claimed_at": row["claimed_at"],
        }

    @staticmethod
    def _receipt(result):
        keys = ("request_id", "status", "finished_at", "payload_sha256", "cleanup_scope", "cleanup_evidence")
        return {key: result[key] for key in keys if key in result}

    @staticmethod
    def _settle(db, row, state, *, reason=None, payload=None, work_stopped=None, timed_out=False):
        finished_at = time.time()
        if work_stopped is True and finished_at >= row["deadline"]:
            raise ValueError("Desktop request expired before its acknowledgement was recorded")
        claimed = row["claimed_at"] is not None
        cleanup = not claimed or work_stopped is True
        evidence = ("never claimed by an external worker" if not claimed else
                    "cooperative work_stopped declaration; external processes are not contained" if cleanup else
                    "external worker termination is unknown; request retained and never reassigned")
        result = {
            "request_id": row["request_id"], "generation": row["generation"], "phase": row["phase"],
            "status": state, "reason": reason, "finished_at": finished_at, "deadline": row["deadline"],
            "worker_id": row["worker_id"], "context_id": row["context_id"], "claimed_at": row["claimed_at"],
            "context_identity": "self-reported", "work_stopped": work_stopped,
            "cleanup_scope": _SCOPE, "cleanup_evidence": evidence, "cleanup_confirmed": cleanup,
            "cancelled": state != "submitted" and not timed_out, "timed_out": timed_out,
        }
        if payload is not None:
            result["payload"] = payload
            result["payload_sha256"] = hashlib.sha256(_encoded(payload).encode("utf-8")).hexdigest()
        _evidence(row["output_dir"], "desktop.result.json", result)
        db.execute("UPDATE requests SET state=?,result=? WHERE request_id=?",
                   (state, json.dumps(result, allow_nan=False), row["request_id"]))
        return result

    def _expire(self, db):
        for row in db.execute(
            "SELECT * FROM requests WHERE state IN ('pending','claimed') AND deadline<=?", (time.time(),),
        ).fetchall():
            self._settle(db, row, "quarantined" if row["state"] == "claimed" else "expired",
                         reason="Desktop request deadline exceeded", timed_out=True)

    def claim(self, worker_id: str, context_id: str) -> dict:
        _identity(worker_id, "worker_id")
        _identity(context_id, "context_id")
        with self._connection() as db:
            if db is None:
                return {"status": "waiting"}
            self._expire(db)
            owned = db.execute(
                "SELECT * FROM requests WHERE state='claimed' AND (worker_id=? OR context_id=?)",
                (worker_id, context_id),
            ).fetchone()
            if owned:
                if owned["worker_id"] != worker_id or owned["context_id"] != context_id:
                    raise ValueError("Worker or context already owns active desktop work")
                return self._job(owned)
            row = db.execute("SELECT * FROM requests WHERE state='pending' ORDER BY created_at,request_id LIMIT 1").fetchone()
            if row is None:
                return {"status": "waiting"}
            if row["phase"] == "review" and db.execute(
                "SELECT 1 FROM requests WHERE phase='implement' AND context_id=? LIMIT 1", (context_id,),
            ).fetchone():
                raise ValueError("Review requires a fresh context different from every prior implementation context")
            db.execute(
                "UPDATE requests SET state='claimed',worker_id=?,context_id=?,token=?,claimed_at=? WHERE request_id=?",
                (worker_id, context_id, uuid.uuid4().hex, time.time(), row["request_id"]),
            )
            return self._job(db.execute("SELECT * FROM requests WHERE request_id=?", (row["request_id"],)).fetchone())

    @staticmethod
    def _owner(db, request_id, token, worker_id, context_id):
        if db is None:
            raise ValueError("No desktop mailbox request exists")
        row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if (row is None or row["worker_id"] != worker_id or row["context_id"] != context_id
                or not isinstance(row["token"], str)
                or not hmac.compare_digest(row["token"].encode("utf-8"), token.encode("utf-8"))):
            raise ValueError("Desktop request owner or token does not match")
        return row

    @staticmethod
    def _submission_inputs(request_id, token, worker_id, context_id, work_stopped):
        for label, value in (("request_id", request_id), ("token", token),
                             ("worker_id", worker_id), ("context_id", context_id)):
            _identity(value, label)
        if work_stopped is not True:
            raise ValueError("work_stopped must be true after all external work has stopped")

    def submit(self, request_id, token, worker_id, context_id, payload: dict, work_stopped: bool,
               *, accept_new=True) -> dict:
        self._submission_inputs(request_id, token, worker_id, context_id, work_stopped)
        if not isinstance(payload, dict):
            raise ValueError("Desktop payload must be an object")
        digest = hashlib.sha256(_encoded(payload).encode("utf-8")).hexdigest()
        with self._connection() as db:
            row = self._owner(db, request_id, token, worker_id, context_id)
            if row["state"] == "submitted":
                result = json.loads(row["result"])
                if result["payload_sha256"] != digest:
                    raise ValueError("Conflicting payload for an already submitted desktop request")
                return self._receipt(result)
            if not accept_new:
                raise ValueError("Batch stopped or exhausted; cannot accept new desktop work")
            if row["state"] != "claimed" or row["deadline"] <= time.time():
                raise ValueError("Desktop request expired, was quarantined, or no longer accepts results")
            validate_payload(payload, json.loads(row["request"])["schema"])
            return self._receipt(self._settle(db, row, "submitted", payload=payload, work_stopped=True))

    def cancel(self, request_id, token, worker_id, context_id, work_stopped: bool) -> dict:
        self._submission_inputs(request_id, token, worker_id, context_id, work_stopped)
        with self._connection() as db:
            row = self._owner(db, request_id, token, worker_id, context_id)
            if row["state"] == "cancelled":
                return self._receipt(json.loads(row["result"]))
            if row["state"] != "claimed" or row["deadline"] <= time.time():
                raise ValueError("Desktop request expired, was quarantined, or no longer accepts cancellation")
            return self._receipt(self._settle(db, row, "cancelled", reason="External worker cancelled request",
                                             work_stopped=True))

    def invalidate(self) -> dict:
        """Quarantine old coordinator requests after explicit recovery; never replay."""
        with self._connection() as db:
            if db is None:
                return {"quarantined": 0}
            rows = db.execute("SELECT * FROM requests WHERE state IN ('pending','claimed')").fetchall()
            for row in rows:
                self._settle(db, row, "quarantined", reason="Coordinator recovery invalidated this request")
            return {"quarantined": len(rows)}

    def _enqueue(self, request, generation):
        if request.phase not in {"discover", "implement", "review"}:
            raise ValueError("Desktop phase must be discover, implement, or review")
        if not isinstance(request.prompt, str) or not isinstance(request.schema, dict):
            raise ValueError("Desktop request needs a prompt string and schema object")
        timeout = request.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Desktop timeout must be a positive finite number")
        cwd, output_dir = plain_path(request.cwd), plain_path(request.output_dir)
        if not cwd.is_dir():
            raise ValueError("Desktop worktree must be an existing directory")
        now = time.time()
        envelope = {
            "request_id": uuid.uuid4().hex, "generation": generation, "phase": request.phase,
            "prompt": request.prompt, "cwd": str(cwd), "schema": request.schema, "output_dir": str(output_dir),
            "created_at": now, "deadline": now + timeout, "context_identity": "self-reported",
            "instructions": _instructions(request.phase),
        }
        encoded = _encoded(envelope)
        with self._connection(create=True) as db:
            if (db.execute("SELECT 1 FROM requests WHERE output_dir=?", (str(output_dir),)).fetchone()
                    or (output_dir / "desktop.request.json").exists()):
                raise ValueError("Desktop output directory was already used; requests are never replayed")
            _evidence(output_dir, "desktop.request.json", envelope)
            db.execute(
                "INSERT INTO requests(request_id,generation,phase,output_dir,created_at,deadline,state,request) "
                "VALUES(?,?,?,?,?,?,'pending',?)",
                (envelope["request_id"], generation, request.phase, str(output_dir), now, envelope["deadline"], encoded),
            )
        return envelope

    def _poll(self, request_id, reason=None, *, timed_out=False):
        with self._connection() as db:
            if db is None:
                raise ValueError("Desktop mailbox disappeared while awaiting a result")
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("Desktop request disappeared while awaiting a result")
            if row["state"] not in _ACTIVE:
                return json.loads(row["result"])
            if row["deadline"] <= time.time():
                reason, timed_out = "Desktop request deadline exceeded", True
            if reason:
                state = "quarantined" if row["state"] == "claimed" else "expired" if timed_out else "cancelled"
                return self._settle(db, row, state, reason=reason, timed_out=timed_out)
            return None


class DesktopDriver:
    """Wait for a durable host declaration while the engine retains acceptance."""

    def __init__(self, state_dir: Path):
        self.queue = DesktopQueue(state_dir)
        self.generation = uuid.uuid4().hex

    def run(self, request, should_stop) -> DriverResult:
        if should_stop():
            return DriverResult(cancelled=True, cleanup_scope=_SCOPE)
        try:
            envelope = self.queue._enqueue(request, self.generation)
        except (OSError, ValueError, TypeError, sqlite3.Error, RecursionError) as exc:
            return DriverResult(returncode=-1, error=f"Invalid desktop handoff configuration: {exc}",
                                error_kind="config", cleanup_scope=_SCOPE)
        monotonic_deadline = time.monotonic() + request.timeout_seconds
        while True:
            stopped = should_stop()
            timed_out = time.monotonic() >= monotonic_deadline
            reason = "Desktop handoff stopped" if stopped else "Desktop request deadline exceeded" if timed_out else None
            try:
                result = self.queue._poll(envelope["request_id"], reason, timed_out=timed_out and not stopped)
            except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
                return DriverResult(returncode=-1, error=f"Desktop result could not be confirmed: {exc}",
                                    error_kind="protocol", cleanup_confirmed=False, cleanup_scope=_SCOPE)
            if result is not None:
                return DriverResult(
                    payload=result.get("payload"), returncode=0 if result["status"] == "submitted" else -1,
                    error=result.get("reason"), cancelled=result["cancelled"], timed_out=result["timed_out"],
                    cleanup_confirmed=result["cleanup_confirmed"], cleanup_scope=_SCOPE,
                )
            time.sleep(.02)
