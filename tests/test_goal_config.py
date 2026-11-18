import copy
import subprocess

import pytest

from dutyloop.config import validate_spec
from dutyloop.schemas import (
    GOAL_INVESTIGATION_SCHEMA,
    GOAL_PLAN_SCHEMA,
    GOAL_REVIEW_SCHEMA,
    validate_payload,
)


@pytest.fixture
def spec(tmp_path):
    project = tmp_path / "project"
    subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(project), "-c", "user.name=Test Maintainer",
        "-c", "user.email=maintainer@example.invalid", "commit", "--allow-empty", "-m", "Add fixture",
    ], check=True, capture_output=True)
    return {
        "mission": "Finish the bounded reliability goal",
        "project": str(project), "allowed_paths": ["src"],
        "verification": [["python", "-m", "unittest"]],
        "limits": {"max_invocations": 1, "max_wall_seconds": 60},
    }


def test_old_standing_spec_defaults_and_round_trip(spec, tmp_path):
    normalized = validate_spec(spec, tmp_path / "state")
    assert normalized["mode"] == "standing"
    assert normalized["goal_acceptance"] == []
    assert normalized["max_plan_revisions"] is None
    assert normalized["team"] == {"discovery_workers": 1, "max_workers": None}
    assert validate_spec(normalized, tmp_path / "state") == normalized
    spec["team"] = {"discovery_workers": 2}
    assert validate_spec(spec, tmp_path / "state")["team"]["discovery_workers"] == 2


def test_goal_defaults_trim_criteria_and_keep_small_budget(spec, tmp_path):
    spec.update(mode="goal", goal_acceptance=["  Regression passes  ", "Documentation matches"])
    normalized = validate_spec(spec, tmp_path / "state")
    assert normalized["goal_acceptance"] == ["Regression passes", "Documentation matches"]
    assert normalized["max_plan_revisions"] == 2
    assert normalized["team"] == {"discovery_workers": 1, "max_workers": 3}
    assert normalized["limits"]["max_invocations"] == 1
    assert validate_spec(normalized, tmp_path / "state") == normalized


@pytest.mark.parametrize("mode", [None, "", "finite", True, [], {}])
def test_invalid_mode_is_rejected(spec, tmp_path, mode):
    spec["mode"] = mode
    with pytest.raises(ValueError, match="mode"):
        validate_spec(spec, tmp_path / "state")


@pytest.mark.parametrize("criteria", [None, [], "Done", [""], ["  "], [True], ["a\x00b"], ["Done", " Done "]])
def test_goal_requires_nonempty_unique_trimmed_criteria(spec, tmp_path, criteria):
    spec.update(mode="goal", goal_acceptance=criteria)
    with pytest.raises(ValueError, match="goal_acceptance"):
        validate_spec(spec, tmp_path / "state")


@pytest.mark.parametrize("field,value", [
    ("goal_acceptance", ["Done"]), ("goal_acceptance", False),
    ("max_plan_revisions", 2), ("max_plan_revisions", 0),
    ("team", {"max_workers": 3}), ("team", {"max_workers": False}),
])
def test_standing_rejects_meaningful_goal_fields(spec, tmp_path, field, value):
    spec[field] = value
    with pytest.raises(ValueError, match="goal mode"):
        validate_spec(spec, tmp_path / "state")


@pytest.mark.parametrize("value", [None, 0, -1, 6, 2.0, True, "2", float("inf")])
def test_plan_revision_cap_is_a_bounded_positive_integer(spec, tmp_path, value):
    spec.update(mode="goal", goal_acceptance=["Done"], max_plan_revisions=value)
    with pytest.raises(ValueError):
        validate_spec(spec, tmp_path / "state")


@pytest.mark.parametrize("value", [None, 0, -1, 4, 2.0, True, "2", float("inf")])
def test_goal_worker_cap_is_a_bounded_positive_integer(spec, tmp_path, value):
    spec.update(mode="goal", goal_acceptance=["Done"], team={"max_workers": value})
    with pytest.raises(ValueError):
        validate_spec(spec, tmp_path / "state")


@pytest.mark.parametrize("revisions,workers", [(1, 1), (5, 3)])
def test_goal_accepts_caps_and_existing_discovery_setting(spec, tmp_path, revisions, workers):
    spec.update(mode="goal", goal_acceptance=["Done"], max_plan_revisions=revisions,
                team={"discovery_workers": 2, "max_workers": workers})
    normalized = validate_spec(spec, tmp_path / "state")
    assert normalized["max_plan_revisions"] == revisions
    assert normalized["team"] == {"discovery_workers": 2, "max_workers": workers}


def candidate():
    return {
        "key": "fix-regression", "title": "Fix regression", "kind": "bugfix", "why": "A test fails",
        "priority": 1, "evidence": [{"source": "tests/test_unit.py:1", "observation": "Fails locally"}],
        "acceptance": ["Regression passes"],
    }


def test_goal_plan_is_finite_and_allows_no_tasks():
    plan = {"status": "work", "summary": "Already satisfied", "investigations": [], "tasks": []}
    validate_payload(plan, GOAL_PLAN_SCHEMA)
    validate_payload(plan | {"status": "blocked"}, GOAL_PLAN_SCHEMA)
    for status in ("wait", "done", "completed"):
        with pytest.raises(ValueError, match="enum"):
            validate_payload(plan | {"status": status}, GOAL_PLAN_SCHEMA)
    investigation = {"key": "inspect-tests", "role": "qa", "question": "What fails?", "paths": ["tests"]}
    task = {"candidate": candidate(), "depends_on": [], "blocked_reason": ""}
    validate_payload(plan | {"investigations": [investigation] * 3, "tasks": [task] * 24}, GOAL_PLAN_SCHEMA)
    for change in ({"investigations": [investigation] * 4}, {"tasks": [task] * 25}):
        with pytest.raises(ValueError, match="array length"):
            validate_payload(plan | change, GOAL_PLAN_SCHEMA)
    for change in ({"paths": []}, {"role": ""}, {"question": ""}, {"key": "../escape"}):
        with pytest.raises(ValueError):
            validate_payload(plan | {"investigations": [investigation | change]}, GOAL_PLAN_SCHEMA)
    with pytest.raises(ValueError):
        validate_payload(plan | {"tasks": [task | {"candidate": candidate() | {"acceptance": []}}]}, GOAL_PLAN_SCHEMA)


def test_goal_review_and_investigation_require_structured_evidence():
    evidence = [{"source": "verification/receipt.txt", "observation": "Regression passes"}]
    investigation = {"summary": "Inspected regression", "evidence": evidence, "recommendations": ["Fix it"]}
    validate_payload(investigation, GOAL_INVESTIGATION_SCHEMA)
    review = {
        "accepted": True, "summary": "Verified", "findings": [], "goal_completed": True,
        "criteria": [{"criterion": 1, "met": True, "evidence": evidence}],
    }
    validate_payload(review, GOAL_REVIEW_SCHEMA)
    for criterion in (0, True, "1"):
        with pytest.raises(ValueError):
            validate_payload(review | {"criteria": [{"criterion": criterion, "met": True, "evidence": evidence}]},
                             GOAL_REVIEW_SCHEMA)
    with pytest.raises(ValueError):
        validate_payload(investigation | {"evidence": [{"source": "test.txt"}]}, GOAL_INVESTIGATION_SCHEMA)


@pytest.mark.parametrize("schema", [GOAL_PLAN_SCHEMA, GOAL_INVESTIGATION_SCHEMA, GOAL_REVIEW_SCHEMA])
def test_goal_contracts_are_closed_with_every_key_required(schema):
    def check_closed(current):
        if current.get("type") == "object":
            assert current["additionalProperties"] is False
            assert set(current["required"]) == set(current["properties"])
            for child in current["properties"].values():
                check_closed(child)
        elif current.get("type") == "array":
            check_closed(current["items"])

    check_closed(schema)
    payload = {"status": "work", "summary": "Done", "investigations": [], "tasks": []}
    if schema is GOAL_PLAN_SCHEMA:
        for change in ({"unexpected": True},):
            with pytest.raises(ValueError, match="unexpected"):
                validate_payload(payload | change, schema)
        missing = copy.deepcopy(payload)
        del missing["tasks"]
        with pytest.raises(ValueError, match="missing required"):
            validate_payload(missing, schema)
