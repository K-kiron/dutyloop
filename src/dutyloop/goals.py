"""Finite goals: durable dependencies, scoped investigators, and checked completion."""

from __future__ import annotations

import concurrent.futures
import contextvars
import copy
import time
import uuid
from pathlib import Path

from .config import plain_path, relative_path
from .engine import Supervisor, _NoDispatch
from .process import run_process
from .schemas import GOAL_INVESTIGATION_SCHEMA, GOAL_PLAN_SCHEMA, GOAL_REVIEW_SCHEMA
from .workspace import GitCleanupError, WorkspacePreparationError, assert_identity, fingerprint, git, make_worktree


class GoalSupervisor(Supervisor):
    review_schema = GOAL_REVIEW_SCHEMA

    def stopped(self):
        return super().stopped() or self.store.control()["status"] == "blocked"

    def context(self):
        context = super().context()
        goal = self.store.control().get("goal", {})
        context.update(
            goal_acceptance=[{"criterion": index, "requirement": value}
                             for index, value in enumerate(self.spec["goal_acceptance"], 1)],
            remaining_invocations=self.store.available_calls(),
            max_investigators=self.spec["team"]["max_workers"],
            goal_plan={key: value for key, value in goal.items() if key not in {"history", "completion"}},
            operator_decisions={"goal": goal.get("operator_decision"),
                                "tasks": {node["candidate"]["key"]: node["decision"]
                                          for node in goal.get("tasks", []) if node.get("decision")}},
        )
        return context

    def prompt(self, instruction, **data):
        return super().prompt(
            instruction + " For reviews, independently assess EVERY numbered goal criterion against current files "
            "and the supplied operator checks. Cite concrete evidence for each met criterion. Set goal_completed "
            "only when accepted is true and every criterion is met; otherwise report the remaining gaps. "
            "Do not add optional cleanup or documentation beyond the goal. Planned tasks may prove redundant "
            "when current evidence already satisfies every goal criterion. operator_decisions records explicit "
            "operator resolutions; apply them within the unchanged goal, scope and verification policy. "
            "Each evidence source must name ONE existing project-relative file, optionally followed by :line "
            "or :start-end, or one explicitly authorized HTTPS URL. Use separate evidence objects for separate "
            "files. Never put prose, Markdown links, or multiple filenames in one source field.", **data)

    def evidence(self, items, cwd):
        if not items:
            return []
        proposal = {"key": "goal-evidence", "title": "Inspect goal evidence", "kind": self.spec["kinds"][0],
                    "why": "Bind observations to current sources", "priority": 1,
                    "acceptance": ["Inspect current files"], "evidence": items}
        return self.candidate(proposal, cwd)["source_receipts"]

    def validate_review(self, review, workspace):
        criteria = review["criteria"]
        expected = set(range(1, len(self.spec["goal_acceptance"]) + 1))
        if len(criteria) != len(expected) or {row["criterion"] for row in criteria} != expected:
            self.store.set_status("blocked", "goal_review_missing_criteria")
            raise ValueError("Whole-goal review must assess each operator criterion exactly once")
        for row in criteria:
            if row["met"] and not row["evidence"]:
                self.store.set_status("blocked", "goal_review_missing_evidence")
                raise ValueError("A met goal criterion needs source evidence")
            self.evidence(row["evidence"], workspace)
        if review["goal_completed"] != (review["accepted"] and all(row["met"] for row in criteria)):
            self.store.set_status("blocked", "goal_review_inconsistent")
            raise ValueError("Goal completion disagrees with the criterion assessments")

    def observe(self, instruction, schema, **data):
        """One fresh read-only context at the recorded accepted head."""
        snapshot = self.store.snapshot()
        cwd = make_worktree(Path(self.spec["project"]), self.store.directory / "observations" / uuid.uuid4().hex,
                            snapshot["accepted_head"])
        identity = (cwd / ".git").read_bytes()
        before = fingerprint(cwd)
        try:
            payload = self.invoke("discover", cwd, self.prompt(instruction, **data), schema)
            assert_identity(cwd, snapshot["accepted_head"], identity)
            if fingerprint(cwd) != before:
                self.store.set_status("blocked", "discovery_modified_workspace")
                raise RuntimeError("Goal investigation changed its worktree")
            return payload, cwd
        except BaseException:
            self.release_observation(cwd)
            raise

    def plan(self, feedback=None, *, validation_error=None, previous_plan=None):
        old = self.store.control().get("goal", {})
        revision = old.get("revision", 0)
        if revision >= self.spec["max_plan_revisions"]:
            self.store.set_status("blocked", "goal_plan_revisions_exhausted")
            return
        # Even an already-satisfied goal needs a separately checked review.
        if not self.store.require_calls(2):
            return
        payload, cwd = self.observe(
            "Inspect the complete goal and current repository, then create a finite dependency plan. "
            "Use the smallest set of tasks needed for the numbered goal criteria. Keep a straightforward goal "
            "as one task when that avoids overhead. Preserve existing keys and accepted history. "
            "Choose zero investigators for clear work; otherwise assign distinct roles, questions and project "
            "paths only for material independent uncertainties. These investigators will run concurrently and "
            "their evidence will inform implementations. Respect max_investigators and leave enough calls for "
            "implementation plus independent review of each task. Do not create optional follow-up work. "
            "Return no tasks if the current code may already satisfy the goal; a separate acceptance review "
            "will decide. Use blocked_reason for tasks requiring a human decision, and allow unrelated tasks "
            "to proceed. depends_on names task candidate keys, never issue numbers. A priority of 5 runs first. "
            "If the whole goal requires a human decision before any work, return blocked. Do not edit files.",
            GOAL_PLAN_SCHEMA, acceptance_feedback=feedback,
            validation_error=validation_error, previous_plan=previous_plan)
        try:
            nodes = copy.deepcopy(old.get("tasks", []))
            by_key = {node["candidate"]["key"]: node for node in nodes}
            proposed = set()
            for node in payload["tasks"]:
                key = node["candidate"]["key"]
                if key in proposed:
                    raise ValueError("Goal task keys must be unique")
                proposed.add(key)
                node = {**node, "candidate": self.candidate(node["candidate"], cwd)}
                if key in by_key:
                    original = by_key[key]
                    a = {k: v for k, v in original["candidate"].items() if k != "source_receipts"}
                    b = {k: v for k, v in node["candidate"].items() if k != "source_receipts"}
                    if a != b or original["depends_on"] != node["depends_on"] or original["blocked_reason"] != node["blocked_reason"]:
                        raise ValueError("Replanning cannot rewrite existing task obligations")
                    continue
                by_key[key] = node
                nodes.append(node)
            visited, visiting = set(), set()

            def visit(key):
                if key in visiting:
                    raise ValueError("Goal dependency cycle")
                if key in visited:
                    return
                visiting.add(key)
                deps = by_key[key]["depends_on"]
                if len(deps) != len(set(deps)) or any(dep not in by_key for dep in deps):
                    raise ValueError("Goal dependencies must name unique existing task keys")
                for dep in deps:
                    visit(dep)
                visiting.remove(key)
                visited.add(key)

            for key in by_key:
                visit(key)
            assignments = payload["investigations"]
            if len(assignments) > self.spec["team"]["max_workers"]:
                raise ValueError("Goal investigation team exceeds the operator's worker limit")
            if len({item["key"] for item in assignments}) != len(assignments):
                raise ValueError("Investigation keys must be unique")
            for item in assignments:
                for path in item["paths"]:
                    target = plain_path(cwd / relative_path(path))
                    if cwd not in target.parents or not target.exists():
                        raise ValueError("Investigation scope must exist inside the project")
            goal = {"revision": revision + 1, "base_sha": self.store.control()["accepted_head"],
                    "plan_status": payload["status"], "operator_decision": old.get("operator_decision"),
                    "decisions_at": old.get("decisions_at"),
                    "summary": payload["summary"], "tasks": nodes,
                    "investigations": [{**item, "state": "pending"} for item in assignments],
                    "history": old.get("history", []) + ([{k: v for k, v in old.items() if k != "history"}] if old else []),
                    "completion": None}
            self.store.save_goal(goal, expected_revision=revision)
            self.store.event("goal_planned", {"revision": goal["revision"], "base_sha": goal["base_sha"],
                                              "tasks": len(nodes), "investigators": len(assignments)})
            if payload["status"] == "blocked":
                self.store.set_status("blocked", "goal_requires_operator_decision")
            elif feedback is not None and not old.get("replan_requested") and len(nodes) == len(old.get("tasks", [])):
                self.store.set_status("blocked", "goal_no_supported_remediation")
        except ValueError as exc:
            self.store.event("goal_plan_invalid", {"error": str(exc), "correction": validation_error is not None})
            if validation_error is not None:
                self.store.set_status("blocked", "goal_plan_invalid")
                raise
            if not self.store.require_calls(2):
                return
            # One fresh, charged correction of an invalid proposal. No invalid
            # plan is saved, no task runs, and no new budget or revision is granted.
            self.plan(feedback, validation_error=str(exc), previous_plan=payload)
        finally:
            self.release_observation(cwd)

    def investigate(self):
        goal = self.store.control()["goal"]
        if any(item["state"] not in {"pending", "finished"} for item in goal["investigations"]):
            self.store.set_status("blocked", "goal_investigation_unknown")
            return
        pending = [item for item in goal["investigations"] if item["state"] == "pending"]
        if not pending:
            return
        if not self.store.require_calls(len(pending) + (2 if goal["tasks"] else 1)):
            return
        for item in pending:
            item["state"] = "running"
        self.store.save_goal(goal, expected_revision=goal["revision"])

        def inspect(assignment):
            payload, cwd = self.observe(
                "Act as the assigned investigator. Answer only this role's question using current source evidence. "
                "Do not implement, replan the entire project, or invent new requirements. Return findings and "
                "recommendations for the existing goal tasks. Other investigators cover different concerns.",
                GOAL_INVESTIGATION_SCHEMA, assignment=assignment)
            try:
                receipts = self.evidence(payload["evidence"], cwd)
                return {"payload": payload, "source_receipts": receipts, "base_sha": goal["base_sha"]}
            finally:
                self.release_observation(cwd)

        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(pending)) as pool:
            futures = {pool.submit(contextvars.copy_context().run, inspect, item): item for item in pending}
            for future in concurrent.futures.as_completed(futures):
                item = futures[future]
                try:
                    item.update(state="finished", result=future.result())
                except BaseException as exc:
                    item.update(state="unknown", error=str(exc))
                    errors.append(exc)
                    control = self.store.control()
                    if (not self.interrupted.is_set() and not control["stop_requested"]
                            and control["status"] not in {"blocked", "limited", "stopped"}):
                        self.store.set_status("blocked", "goal_investigation_failed")
                self.store.save_goal(goal, expected_revision=goal["revision"])
        if errors:
            raise next((error for error in errors if isinstance(error, GitCleanupError)), errors[0])

    def complete(self, head, review, checks, tree, reviewed_at):
        goal = self.store.control()["goal"]
        if (not review["goal_completed"] or any(node["blocked_reason"] and not node.get("decision") for node in goal["tasks"])
                or any(item["state"] != "finished" for item in goal["investigations"])
                or goal.get("plan_status") == "blocked"
                or reviewed_at < (goal.get("decisions_at") or 0)):
            return False
        receipt = {"head": head, "tree": tree, "review": review, "checks": checks,
                   "criteria": self.spec["goal_acceptance"], "plan_revision": goal["revision"],
                   "reviewed_at": reviewed_at, "at": time.time()}

        def check_ref():
            if git(Path(self.spec["project"]), "rev-parse", self.store.control()["branch"]) != head:
                raise RuntimeError("Goal integration ref changed outside the supervisor")
            if self.stopped():
                raise _NoDispatch()

        completed = self.store.complete_goal(head, receipt, check_ref)
        if completed:
            self.store.event("goal_accepted", {"head": head, "tree": tree, "revision": goal["revision"]})
        return completed

    def assess_current(self):
        """Accept already-satisfied goals only after real checks and a fresh review."""
        snapshot = self.store.snapshot()
        if not self.store.require_calls(1):
            return None
        cwd = make_worktree(Path(self.spec["project"]), self.store.directory / "observations" / uuid.uuid4().hex,
                            snapshot["accepted_head"])
        identity = (cwd / ".git").read_bytes()
        before, tree = fingerprint(cwd), git(cwd, "write-tree")
        checks = []
        try:
            for index, argv in enumerate(self.spec["verification"]):
                gate = run_process(argv, cwd=cwd, input_text="",
                    output_dir=self.store.directory / "goal-checks" / f"{uuid.uuid4().hex}-{index}",
                    timeout_seconds=min(self.spec["call_timeout_seconds"], self.remaining()), should_stop=self.stopped)
                checks.append({"argv": argv, "returncode": gate.returncode, "stdout": gate.stdout[-6000:], "stderr": gate.stderr[-6000:]})
                if not gate.cleanup_confirmed:
                    self.store.set_status("blocked", "verification_cleanup_unconfirmed")
                    raise RuntimeError("Goal verification cleanup could not be confirmed")
                if gate.cancelled or self.stopped():
                    raise _NoDispatch()
                assert_identity(cwd, snapshot["accepted_head"], identity)
                if fingerprint(cwd) != before or git(cwd, "write-tree") != tree:
                    self.store.set_status("blocked", "verification_modified_workspace")
                    raise RuntimeError("Goal verification modified the accepted snapshot")
                if gate.returncode or gate.error or gate.timed_out or gate.output_truncated:
                    self.store.event("goal_verification", {"head": snapshot["accepted_head"], "passed": False, "checks": checks})
                    return {"summary": "Whole-goal operator verification failed", "checks": checks}
            review = self.invoke("review", cwd, self.prompt(
                "Independently assess whether the complete finite goal is already satisfied at this exact "
                "accepted snapshot. No staged change is required. Inspect all criteria; do not edit files.",
                operator_checks=checks), GOAL_REVIEW_SCHEMA)
            assert_identity(cwd, snapshot["accepted_head"], identity)
            if fingerprint(cwd) != before or git(cwd, "write-tree") != tree:
                self.store.set_status("blocked", "review_modified_workspace")
                raise RuntimeError("Goal reviewer modified the accepted snapshot")
            self.validate_review(review, cwd)
            self.store.event("goal_assessed", {"head": snapshot["accepted_head"], "review": review, "checks": checks})
            self.complete(snapshot["accepted_head"], review, checks, tree, self.store.snapshot()["calls"][-1]["finished_at"])
            return review
        finally:
            self.release_observation(cwd)

    def loop(self):
        try:
            while not self.stopped():
                snapshot = self.store.snapshot()
                if snapshot["status"] in {"completed", "limited", "blocked"}:
                    break
                if any(task["state"] in {"unknown", "rejected"} for task in snapshot["tasks"]) or any(
                        call["state"] == "unknown" for call in snapshot["calls"]):
                    self.store.set_status("blocked", "goal_unresolved_work")
                    break
                goal = snapshot.get("goal")
                if goal is None:
                    self.plan()
                    continue
                if goal.get("replan_requested"):
                    self.plan({"operator_decision": goal.get("operator_decision")})
                    continue
                if goal.get("plan_status") == "blocked":
                    self.store.set_status("blocked", "goal_requires_operator_decision")
                    break
                # A crash after integration must not lose the accepted review or replay work.
                tip = next((task for task in reversed(snapshot["tasks"])
                            if task["state"] == "accepted" and task["commit_sha"] == snapshot["accepted_head"]), None)
                tip_reviewed_at = max((call["finished_at"] for call in snapshot["calls"]
                                       if tip and call["task_id"] == tip["id"] and call["phase"] == "review"
                                       and call["state"] == "finished"), default=0)
                if tip and self.complete(snapshot["accepted_head"], tip["review"], tip["checks"], tip["verified_tree"], tip_reviewed_at):
                    break
                self.investigate()
                if self.stopped() or self.store.control()["status"] == "limited":
                    break
                accepted = {task["key"] for task in snapshot["tasks"] if task["state"] == "accepted"}
                remaining = [node for node in goal["tasks"] if node["candidate"]["key"] not in accepted]
                ready = [node for node in remaining if (not node["blocked_reason"] or node.get("decision"))
                         and set(node["depends_on"]) <= accepted]
                if ready:
                    node = sorted(ready, key=lambda item: (-item["candidate"]["priority"], item["candidate"]["key"]))[0]
                    self.store.event("goal_task_dispatched", {"key": node["candidate"]["key"], "revision": goal["revision"]})
                    self.execute(node["candidate"])
                    continue
                if remaining:
                    self.store.set_status("blocked", "goal_requires_operator_decision")
                    break
                # The last task review already assessed this exact accepted tree.
                feedback = tip["review"] if tip and tip_reviewed_at >= (goal.get("decisions_at") or 0) else self.assess_current()
                if self.store.control()["status"] == "completed" or self.stopped():
                    break
                self.plan(feedback)
        except _NoDispatch:
            pass
        except Exception as exc:
            self.store.event("goal_error", {"error": str(exc)})
            if isinstance(exc, WorkspacePreparationError):
                self.preparation_failed(exc)
            elif isinstance(exc, GitCleanupError):
                self.store.set_status("blocked", "git_cleanup_unconfirmed")
            elif not self.stopped():
                self.store.set_status("blocked", "goal_execution_failed")
        finally:
            snapshot = self.store.snapshot()
            if snapshot["stop_requested"] or self.interrupted.is_set():
                self.store.set_status("stopped", "operator_stop", runner_pid=None)
            elif snapshot["status"] in {"completed", "blocked", "limited"}:
                self.store.set_status(snapshot["status"], snapshot["reason"], runner_pid=None)
            else:
                self.store.set_status("limited", "wall_time_limit" if self.remaining() <= 0 else "invocation_limit", runner_pid=None)
