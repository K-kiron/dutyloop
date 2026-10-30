import concurrent.futures
import subprocess
from pathlib import Path

import pytest

from dutyloop.config import validate_spec
from dutyloop.store import Store


@pytest.fixture
def spec(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
    return {
        "mission": "Improve the project for its users",
        "project": str(project),
        "allowed_paths": ["src", "test"],
        "verification": [["python", "-m", "unittest"]],
        "limits": {"max_invocations": 2, "max_wall_seconds": 60},
    }


def test_spec_rejects_unknown_monetary_budget(spec, tmp_path):
    spec["limits"]["max_cost_usd"] = 10
    with pytest.raises(ValueError, match="monetary|Unknown"):
        validate_spec(spec, tmp_path / "state")


@pytest.mark.parametrize("path", ["../other", "/absolute", "C:\\secrets", ".git/config", "src/../../secret"])
def test_scope_cannot_escape_project(spec, tmp_path, path):
    spec["allowed_paths"] = [path]
    with pytest.raises(ValueError):
        validate_spec(spec, tmp_path / "state")


def test_state_must_be_outside_repository(spec):
    with pytest.raises(ValueError, match="outside"):
        validate_spec(spec, Path(spec["project"]) / ".state")


def test_numeric_limits_reject_boolean_and_infinity(spec, tmp_path):
    spec["limits"]["max_invocations"] = True
    with pytest.raises(ValueError):
        validate_spec(spec, tmp_path / "state")
    spec["limits"]["max_invocations"] = 2
    spec["limits"]["max_wall_seconds"] = float("inf")
    with pytest.raises(ValueError):
        validate_spec(spec, tmp_path / "state")


def test_atomic_budget_reservations(spec, tmp_path):
    state = tmp_path / "state"
    normalized = validate_spec(spec, state)
    store = Store(state)
    store.create(normalized, "a" * 40, "work/test")

    def reserve(_):
        return Store(state).reserve_call("discover")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve, range(8)))
    assert sum(value is not None for value in reservations) == 2
    assert store.snapshot()["invocations"] == 2


def test_stop_is_latched_before_another_dispatch(spec, tmp_path):
    state = tmp_path / "state"
    store = Store(state)
    store.create(validate_spec(spec, state), "a" * 40, "work/test")
    assert store.reserve_call("discover") is not None
    store.stop()
    assert store.reserve_call("implement") is None
    store.set_status("running")
    assert store.snapshot()["stop_requested"] is True
    assert store.snapshot()["status"] != "running"


def test_unclean_calls_keep_their_budget_charge(spec, tmp_path):
    state = tmp_path / "state"
    store = Store(state)
    store.create(validate_spec(spec, state), "a" * 40, "work/test")
    store.reserve_call("implement")
    assert store.has_unfinished_calls()
    store.recover_unknown()
    snapshot = store.snapshot()
    assert snapshot["invocations"] == 1
    assert snapshot["calls"][0]["state"] == "unknown"
    assert not store.has_unfinished_calls()
