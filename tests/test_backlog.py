"""Pure backlog coverage, roadmap consistency, and execution-stage contracts."""

import copy

import pytest

from dutyloop import backlog


def entries(count=3):
    return [{"number": number, "source_sha256": f"{number:064x}",
             "source": {"number": number, "state": "open", "labels": [], "body": f"Requirement {number}"},
             "state": "queued", "reason": None, "decision": None, "admitted": True, "verified": False}
            for number in range(1, count + 1)]


def assessment(sources):
    return {"summary": "All requirements inspected", "current_stage": "Working prototype",
            "evidence": [{"source": "app.py", "observation": "The current implementation is present"}],
            "items": [{"number": source["number"], "source_sha256": source["source_sha256"],
                       "assessment": "Implement the stated requirement", "dependencies": []} for source in sources]}


def stage(identifier, numbers, *, human=False):
    return {"id": identifier, "title": identifier, "goal": "Establish the required capability",
            "rationale": "This capability enables the following work", "entry_conditions": ["Current API is preserved"],
            "exit_criteria": ["All assigned requirements pass operator checks"],
            "requires_human_acceptance": human, "issue_numbers": numbers}


def roadmap(sources, stages=None):
    stages = stages if stages is not None else [stage("foundation", [source["number"] for source in sources])]
    assigned = {number: item["id"] for item in stages for number in item["issue_numbers"]}
    return {"summary": "Complete staged roadmap", "current_stage": "Working prototype",
            "evidence": [{"source": "app.py", "observation": "The current implementation is present"}],
            "stages": stages,
            "items": [{"number": source["number"], "source_sha256": source["source_sha256"],
                       "disposition": "work", "reason": "Required by the stage goal", "question": "",
                       "dependencies": [], "stage_id": assigned.get(source["number"], "")} for source in sources]}


def accepted(number, *, delivery="local"):
    row = {"number": number, "state": "accepted_local", "verified_tree": "tree",
           "review": {"accepted": True}, "verification": [{"argv": ["check"], "returncode": 0}]}
    if delivery != "local":
        row.update(state="delivered", merge_sha="merge", post_merge_verified="merge",
                   post_merge_checks=[{"argv": ["check"], "returncode": 0}])
    return row


def test_complete_assessment_covers_every_source_without_mutating_it():
    sources = entries(93)
    payload = assessment(sources)
    original = copy.deepcopy(payload)
    assert backlog.validate_assessment(payload, sources) == original
    assert payload == original


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign", "stale", "self", "duplicate_dependency"])
def test_assessment_rejects_incomplete_or_inconsistent_coverage(change):
    sources = entries()
    payload = assessment(sources)
    if change == "missing":
        payload["items"].pop()
    elif change == "duplicate":
        payload["items"][1] = copy.deepcopy(payload["items"][0])
    elif change == "foreign":
        payload["items"][0]["number"] = 99
    elif change == "stale":
        payload["items"][0]["source_sha256"] = "f" * 64
    elif change == "self":
        payload["items"][0]["dependencies"] = [1]
    else:
        payload["items"][0]["dependencies"] = [2, 2]
    with pytest.raises(ValueError):
        backlog.validate_assessment(payload, sources)


def test_late_foundation_is_selected_before_the_first_92_issue_numbers():
    sources = entries(93)
    payload = roadmap(sources, [stage("foundation", [93]), stage("expansion", list(range(1, 93)))])
    backlog.validate_roadmap(payload, sources)
    current, work, gate = backlog.next_work(payload, sources, "local")
    assert current["id"] == "foundation"
    assert [item["number"] for item in work] == [93]
    assert gate is None


@pytest.mark.parametrize("change", ["stage_duplicate", "membership_duplicate", "membership_missing", "foreign_member",
                                    "wrong_stage", "nonwork_stage", "cycle", "backward", "blank_gate", "no_evidence"])
def test_roadmap_rejects_invalid_stage_or_dependency_contract(change):
    sources = entries()
    payload = roadmap(sources, [stage("foundation", [1, 2]), stage("expansion", [3])])
    if change == "stage_duplicate":
        payload["stages"][1]["id"] = "foundation"
    elif change == "membership_duplicate":
        payload["stages"][1]["issue_numbers"].append(1)
    elif change == "membership_missing":
        payload["stages"][0]["issue_numbers"].remove(1)
    elif change == "foreign_member":
        payload["stages"][0]["issue_numbers"].append(99)
    elif change == "wrong_stage":
        payload["items"][0]["stage_id"] = "expansion"
    elif change == "nonwork_stage":
        payload["items"][0].update(disposition="defer")
    elif change == "cycle":
        payload["items"][0]["dependencies"] = [2]
        payload["items"][1]["dependencies"] = [1]
    elif change == "backward":
        payload["items"][0]["dependencies"] = [3]
    elif change == "blank_gate":
        payload["stages"][0]["exit_criteria"] = ["  "]
    else:
        payload["evidence"] = []
    with pytest.raises(ValueError):
        backlog.validate_roadmap(payload, sources)


@pytest.mark.parametrize("change", ["unadmitted", "needs_decision", "excluded", "external_closed", "closed_source"])
def test_work_cannot_override_admission_or_existing_decisions(change):
    sources = entries(1)
    if change == "unadmitted":
        sources[0]["admitted"] = False
    elif change == "excluded":
        sources[0]["reason"] = "excluded_label"
    elif change == "closed_source":
        sources[0]["source"]["state"] = "closed"
    else:
        sources[0]["state"] = change
    with pytest.raises(ValueError):
        backlog.validate_roadmap(roadmap(sources), sources)


def test_nonwork_dispositions_require_decisions_reasons_and_verified_outcomes():
    sources = entries()
    payload = roadmap(sources, [])
    payload["items"][0].update(disposition="needs_decision", question="Which public behavior is intended?")
    payload["items"][1].update(disposition="defer", reason="Requires the next release")
    payload["items"][2].update(disposition="done")
    with pytest.raises(ValueError):
        backlog.validate_roadmap(payload, sources)
    sources[2]["verified"] = True
    backlog.validate_roadmap(payload, sources)
    payload["items"][0]["question"] = " "
    with pytest.raises(ValueError):
        backlog.validate_roadmap(payload, sources)
    payload["items"][0]["question"] = "Choose the behavior?"
    payload["items"][1]["reason"] = " "
    with pytest.raises(ValueError):
        backlog.validate_roadmap(payload, sources)


def test_external_dependencies_are_retained_for_live_validation():
    sources = entries(1)
    payload = roadmap(sources)
    payload["items"][0]["dependencies"] = [999]
    backlog.validate_roadmap(payload, sources)
    assert backlog.next_work(payload, sources, "local")[1] == payload["items"]


def test_current_stage_allows_unrelated_work_but_later_stage_waits():
    sources = entries()
    payload = roadmap(sources, [stage("foundation", [2, 1]), stage("expansion", [3])])
    sources[0]["state"] = "needs_decision"
    assert [item["number"] for item in backlog.next_work(payload, sources, "local")[1]] == [2]
    sources[1] = accepted(2)
    current, work, gate = backlog.next_work(payload, sources, "local")
    assert current["id"] == "foundation" and work == [] and gate == "stage_has_no_runnable_work"
    sources[0] = accepted(1)
    assert backlog.next_work(payload, sources, "local")[0]["id"] == "expansion"


def test_internal_dependencies_wait_for_verified_completion():
    sources = entries()
    payload = roadmap(sources)
    payload["items"][0]["dependencies"] = [2]
    assert [item["number"] for item in backlog.next_work(payload, sources, "local")[1]] == [2, 3]
    sources[1] = accepted(2)
    assert [item["number"] for item in backlog.next_work(payload, sources, "local")[1]] == [1, 3]


def test_human_stage_gate_prevents_automatic_work():
    sources = entries(1)
    payload = roadmap(sources, [stage("human-validation", [1], human=True)])
    assert backlog.next_work(payload, sources, "local")[1:] == ([], "stage_requires_human_acceptance")


def test_verified_code_does_not_satisfy_a_human_stage_gate():
    sources = entries(2)
    payload = roadmap(sources, [stage("human-validation", [1], human=True), stage("expansion", [2])])
    current, work, gate = backlog.next_work(payload, [accepted(1), sources[1]], "local")
    assert current["id"] == "human-validation" and work == [] and gate == "stage_requires_human_acceptance"


@pytest.mark.parametrize("kind", ["assessment", "roadmap"])
def test_planning_contracts_reject_undeclared_authority_fields(kind):
    sources = entries(1)
    payload = assessment(sources) if kind == "assessment" else roadmap(sources)
    payload["verification"] = [["replace-operator-checks"]]
    validator = backlog.validate_assessment if kind == "assessment" else backlog.validate_roadmap
    with pytest.raises(ValueError, match="unexpected properties"):
        validator(payload, sources)


@pytest.mark.parametrize("change", ["external_closed", "pull_request", "failed", "no_tree", "review_rejected",
                                    "no_checks", "failed_check", "no_merge_verification", "different_merge"])
def test_stage_completion_requires_verification_records(change):
    delivery = "pull_request" if change in {"no_merge_verification", "different_merge", "pull_request"} else "local"
    row = accepted(1, delivery=delivery)
    if change in {"external_closed", "pull_request", "failed"}:
        row["state"] = change
    elif change == "no_tree":
        row["verified_tree"] = ""
    elif change == "review_rejected":
        row["review"]["accepted"] = False
    elif change == "no_checks":
        row["verification"] = []
    elif change == "failed_check":
        row["verification"][0]["returncode"] = 1
    elif change == "no_merge_verification":
        row.pop("post_merge_verified")
    else:
        row["post_merge_verified"] = "other"
    assert backlog.verified(row, delivery) is False
    assert backlog.next_work(roadmap(entries(1)), [row], delivery)[0] is not None


@pytest.mark.parametrize("delivery", ["local", "pull_request", "merge"])
def test_verified_completion_exhausts_the_roadmap(delivery):
    row = accepted(1, delivery=delivery)
    assert backlog.verified(row, delivery) is True
    assert backlog.next_work(roadmap(entries(1)), [row], delivery) == (None, [], None)
