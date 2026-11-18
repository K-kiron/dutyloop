"""Record explicit operator decisions without resetting finite-goal authority."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import plain_path
from .store import Store, runner_lock
from .workspace import git


def resolve(state_dir: Path, key: str, decision: str) -> dict:
    """Resume only an operator-decision blocker; never infer the decision text.

    One SQLite transaction linearizes this action with concurrent stop requests.
    The runner lock excludes dispatch, and existing budgets and obligations remain
    unchanged. Host-owned process termination still needs explicit recovery.
    """
    if not isinstance(key, str) or not key or key != key.strip():
        raise ValueError("A task key or the whole-goal key '@goal' is required")
    if (not isinstance(decision, str) or not decision.strip() or len(decision) > 8000
            or any(ord(character) < 32 and character not in "\n\r\t" for character in decision)):
        raise ValueError("decision must be nonempty operator text of at most 8000 characters without control characters")
    decision = decision.strip()
    state = plain_path(state_dir)
    store = Store(state)
    if not store.path.is_file():
        raise ValueError(f"No mission exists in {state}")
    if (state / "issues.sqlite3").exists():
        raise ValueError("Ambiguous state contains both mission and issue databases")
    with runner_lock(state):
        with store.connection() as db:
            data = store._read(db)
            if data["spec"].get("mode", "standing") != "goal":
                raise ValueError("Operator task decisions require goal mode")
            goal = data.get("goal")
            if not goal or goal.get("completion"):
                raise ValueError("A pending finite goal plan is required")
            if (data["stop_requested"] or data["status"] != "blocked"
                    or data["reason"] != "goal_requires_operator_decision"):
                raise ValueError("Only a blocked goal requiring an operator decision can be resolved")
            if data["runner_pid"] is not None:
                raise RuntimeError("A recorded runner prevents resolving the goal; inspect and recover it first")
            if (db.execute("SELECT 1 FROM calls WHERE state!='finished' LIMIT 1").fetchone()
                    or db.execute("SELECT 1 FROM tasks WHERE state!='accepted' LIMIT 1").fetchone()
                    or any(item.get("state") not in {"pending", "finished"} for item in goal.get("investigations", []))):
                raise RuntimeError("Unsettled or rejected work prevents an operator decision; inspect retained work first")
            if goal.get("plan_status") == "blocked" and key != "@goal":
                raise ValueError("Resolve the whole-goal blocker with key '@goal' before recording task-specific decisions")
            node = next((item for item in goal["tasks"] if item["candidate"]["key"] == key), None)
            if node is None:
                if key != "@goal" or goal.get("plan_status") != "blocked":
                    raise ValueError("Select an existing blocked task key, or '@goal' for a whole-goal planning blocker")
                goal["operator_decision"] = decision
                goal["replan_requested"] = True
            else:
                if not node.get("blocked_reason"):
                    raise ValueError("The selected task does not require an operator decision")
                if db.execute("SELECT 1 FROM tasks WHERE task_key=? LIMIT 1", (key,)).fetchone():
                    raise ValueError("An already attempted task cannot be changed by an operator decision")
                node["decision"] = decision
            try:
                head = git(Path(data["spec"]["project"]), "rev-parse", data["branch"])
            except Exception as exc:
                raise RuntimeError("Integration ref could not be verified; inspect the retained mission") from exc
            if head != data["accepted_head"]:
                raise RuntimeError("Integration ref differs from the recorded accepted head")
            now = time.time()
            goal["decisions_at"] = now
            reason = ("invocation_limit" if data["invocations"] >= data["spec"]["limits"]["max_invocations"] else
                      "wall_time_limit" if data["deadline"] is not None and now >= data["deadline"] else None)
            data.update(status="limited" if reason else "ready", reason=reason or "goal_operator_decision_recorded")
            store._save(db, data)
            db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)", (
                now, "goal_decision_recorded", json.dumps({"key": key, "decision": decision,
                    "revision": goal["revision"], "status": data["status"]})))
    return store.snapshot()
