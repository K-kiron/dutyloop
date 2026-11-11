"""Repository MCP uses the existing local path and executor authority boundary."""

import asyncio
import copy
import importlib.util

import pytest

from dutyloop import issues, mcp_server, repository


REPOSITORY_TOOLS = {
    "repository_create", "repository_start", "repository_status", "repository_reconcile", "repository_stop",
    "repository_events", "repository_recover", "repository_resolve", "repository_attention", "repository_set_mode",
}


@pytest.fixture
def control(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    snapshot = {"kind": "repository", "status": "waiting_for_executor", "invocations": 0,
                "stop_requested": False, "deadline": None,
                "executor": {"mode": "desktop", "host_driven": True, "cannot_wake_host": True},
                "spec": {"project": str(project), "driver": {"name": "desktop"}},
                "attention": [{"id": "issue:12", "type": "decision", "issue_number": 12,
                               "required_action": "Choose the export format"}]}
    calls = []
    monkeypatch.setattr(repository, "status", lambda state:
                        calls.append(("status", state)) or copy.deepcopy(snapshot))
    monkeypatch.setattr(repository, "is_repository", lambda _state: False)
    for action in ("initialize", "reconcile", "stop", "events", "recover", "resolve", "set_mode"):
        monkeypatch.setattr(repository, action, lambda *args, _action=action, **options:
                            calls.append((_action, args, options)) or {"status": "ready"})
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    return controller, project, calls, snapshot


def test_repository_create_defaults_to_desktop_without_starting(control, monkeypatch):
    controller, project, calls, _snapshot = control
    monkeypatch.setattr(repository, "run", lambda *_args: pytest.fail("create cannot run models"))
    supplied = {"mission": "Maintain the repository"}
    assert controller.repository_create("maintenance", supplied) == {"status": "ready"}
    assert calls == [("initialize", ({"mission": "Maintain the repository", "project": str(project),
                                      "driver": {"name": "desktop"}}, controller.root / "maintenance"), {})]
    assert supplied == {"mission": "Maintain the repository"}


@pytest.mark.parametrize("driver", [
    {"name": "command", "argv": ["python"]}, {"name": "codex", "argv": ["python"]}, {"name": "other"},
    {"name": "desktop", "model": "custom-model"},
])
def test_repository_create_rejects_custom_commands_and_driver_options(control, driver):
    controller, _project, calls, _snapshot = control
    with pytest.raises(ValueError, match="driver"):
        controller.repository_create("maintenance", {"driver": driver})
    assert calls == []
    assert not controller.root.exists()


def test_repository_create_requires_exact_allowlisted_project(control, tmp_path):
    controller, project, calls, _snapshot = control
    nested = project / "nested"
    nested.mkdir()
    for selected in (nested, tmp_path):
        with pytest.raises(ValueError, match="exactly"):
            controller.repository_create("maintenance", {"project": str(selected)})
    assert calls == []


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", ".", "", "CAPS", "a--b", "con", "a" * 65])
def test_repository_names_remain_bounded_slugs(control, name):
    controller, _project, calls, _snapshot = control
    with pytest.raises(ValueError, match="name"):
        controller.repository_status(name)
    assert calls == []


@pytest.mark.parametrize("existing", ["mission.sqlite3", "issues.sqlite3"])
def test_repository_create_does_not_repurpose_existing_state(control, existing):
    controller, _project, calls, _snapshot = control
    state = controller.root / "maintenance"
    state.mkdir(parents=True)
    (state / existing).touch()
    with pytest.raises(ValueError, match="already belongs"):
        controller.repository_create("maintenance", {})
    assert calls == []


@pytest.mark.parametrize("action", ["repository_status", "repository_start", "repository_reconcile", "repository_stop",
                                    "repository_events", "repository_recover", "repository_attention", "repository_set_mode"])
def test_repository_endpoints_recheck_stored_allowlist(control, action):
    controller, _project, calls, snapshot = control
    snapshot["spec"]["project"] = str(controller.root.parent)
    args = ("maintenance", "auto") if action == "repository_set_mode" else ("maintenance",)
    with pytest.raises(ValueError, match="allowlist"):
        getattr(controller, action)(*args)
    assert calls == [("status", controller.root / "maintenance")]


def test_repository_stored_custom_commands_cannot_start(control, monkeypatch):
    controller, _project, _calls, snapshot = control
    snapshot["spec"]["driver"] = {"name": "command", "argv": ["python"]}
    monkeypatch.setattr(mcp_server, "start_background", lambda *_args, **_kwargs: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="custom commands"):
        controller.repository_start("maintenance")


def test_repository_start_passes_only_repository_background_identity(control, monkeypatch):
    controller, _project, calls, _snapshot = control
    starts = []
    monkeypatch.setattr(mcp_server, "start_background", lambda *args, **options:
                        starts.append((args, options)) or {"pid": 123})
    assert controller.repository_start("maintenance") == {"pid": 123}
    assert starts == [((controller.root / "maintenance",), {"repository": True})]
    assert calls == [("status", controller.root / "maintenance")]


def test_repository_readonly_attention_and_reconcile_use_separate_apis(control, monkeypatch):
    controller, _project, calls, snapshot = control
    monkeypatch.setattr(repository, "run", lambda *_args: pytest.fail("must not run"))
    assert controller.repository_attention("maintenance") == {"attention": snapshot["attention"]}
    assert calls == [("status", controller.root / "maintenance")]
    assert controller.repository_reconcile("maintenance") == {"status": "ready"}
    assert calls[-1] == ("reconcile", (controller.root / "maintenance",), {})


def test_repository_operator_controls_preserve_cursor_acknowledgment_and_decision(control):
    controller, _project, calls, _snapshot = control
    state = controller.root / "maintenance"
    controller.repository_events("maintenance", after=17, limit=3)
    controller.repository_recover("maintenance")
    controller.repository_recover("maintenance", acknowledge=True)
    controller.repository_resolve("maintenance", 12, "Preserve CSV")
    controller.repository_stop("maintenance")
    controller.repository_set_mode("maintenance", "auto")
    assert [call for call in calls if call[0] != "status"] == [
        ("events", (state,), {"after_id": 17, "limit": 3}),
        ("recover", (state,), {"acknowledge": False}),
        ("recover", (state,), {"acknowledge": True}),
        ("resolve", (state, 12, "Preserve CSV"), {}),
        ("stop", (state,), {}),
        ("set_mode", (state, "auto"), {}),
    ]


@pytest.mark.parametrize("action", ["issues_status", "issues_start", "issues_stop", "issues_events", "issues_recover", "issues_resolve"])
def test_finite_issue_tools_cannot_bypass_repository_authority(control, monkeypatch, action):
    controller, _project, calls, _snapshot = control
    monkeypatch.setattr(repository, "is_repository", lambda _state: True)
    monkeypatch.setattr(issues, "status", lambda *_args: pytest.fail("must not use finite snapshot"))
    args = ("maintenance", 1, "Preserve behavior") if action == "issues_resolve" else ("maintenance",)
    with pytest.raises(ValueError, match="Repository maintenance"):
        getattr(controller, action)(*args)
    assert calls == []


@pytest.mark.parametrize("kind", ["issues", "repository"])
def test_repository_desktop_kind_preserves_same_database_and_waiting_execution(control, monkeypatch, kind):
    from dutyloop.desktop import DesktopQueue
    controller, _project, calls, _snapshot = control
    monkeypatch.setattr(repository, "is_repository", lambda _state: True)
    claims = []
    monkeypatch.setattr(DesktopQueue, "claim", lambda self, worker, context:
                        claims.append((self.directory, worker, context)) or {"status": "waiting"})
    assert controller.desktop_next("maintenance", "host", "context", kind) == {"status": "waiting"}
    assert claims == [(controller.root / "maintenance", "host", "context")]
    assert calls == [("status", controller.root / "maintenance")]


@pytest.mark.parametrize("status", ["stopped", "limited", "blocked"])
def test_repository_desktop_cannot_claim_after_stop_or_limits(control, monkeypatch, status):
    controller, _project, _calls, snapshot = control
    snapshot["status"] = status
    monkeypatch.setattr(repository, "is_repository", lambda _state: True)
    result = controller.desktop_next("maintenance", "host", "context")
    assert result["status"] == status
    assert result["work_available"] is False
    assert not controller.root.exists()


def test_repository_tools_reject_ambiguous_mission_and_issue_databases(control):
    controller, _project, calls, _snapshot = control
    state = controller.root / "maintenance"
    state.mkdir(parents=True)
    (state / "mission.sqlite3").touch()
    (state / "issues.sqlite3").touch()
    with pytest.raises(ValueError, match="Ambiguous"):
        controller.repository_status("maintenance")
    assert calls == []


def test_mission_endpoints_cannot_read_or_change_repository_state(control, monkeypatch):
    controller, _project, calls, _snapshot = control
    state = controller.root / "maintenance"
    state.mkdir(parents=True)
    (state / "issues.sqlite3").touch()
    monkeypatch.setattr(mcp_server, "_engine", lambda: pytest.fail("must reject issue state before reading mission"))
    for action in (controller.mission_status, controller.mission_start, controller.mission_stop,
                   controller.mission_events, controller.mission_recover):
        with pytest.raises(ValueError, match="issue batch or repository"):
            action("maintenance")
    assert calls == []


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="Optional MCP SDK is not installed")
def test_official_sdk_registers_repository_tools_with_inert_control_contract(control):
    controller, project, calls, _snapshot = control
    server = mcp_server.create_server(controller.root, [project])
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert REPOSITORY_TOOLS <= tools.keys()
    assert "GET" in tools["repository_reconcile"].description
    assert "cannot wake" in tools["repository_start"].description
    for name in REPOSITORY_TOOLS:
        assert not {"token", "api_key", "credentials"} & tools[name].inputSchema.get("properties", {}).keys()
    assert calls == []
