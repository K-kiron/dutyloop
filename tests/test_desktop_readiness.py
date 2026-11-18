"""Offline host capability and MCP metadata checks; no model login required."""

import asyncio

import pytest

from dutyloop import mcp_server


def test_mcp_annotations_distinguish_reads_claims_and_execution(tmp_path):
    pytest.importorskip("mcp")
    project = tmp_path / "project"
    project.mkdir()
    server = mcp_server.create_server(tmp_path / "states", [project])
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    reads = {"desktop_readiness", "mission_status", "mission_events", "issues_status", "issues_events",
             "repository_status", "repository_events", "repository_attention"}
    for name in reads:
        hints = tools[name].annotations
        assert hints is not None, name
        assert hints.readOnlyHint is True, name
        assert hints.destructiveHint is False, name
        assert hints.idempotentHint is True, name
        assert hints.openWorldHint is False, name
    for name in {"mission_create", "mission_start", "mission_stop", "desktop_next",
                 "desktop_submit", "desktop_cancel", "repository_reconcile"}:
        assert tools[name].annotations is not None, name
        assert tools[name].annotations.readOnlyHint is False, name
    assert tools["mission_create"].annotations.destructiveHint is False
    assert tools["mission_create"].annotations.openWorldHint is False
    assert tools["mission_start"].annotations.destructiveHint is True
    assert tools["mission_start"].annotations.openWorldHint is True
    assert tools["repository_reconcile"].annotations.openWorldHint is True
    assert not (tmp_path / "states").exists()


def test_desktop_readiness_is_inert_and_does_not_assume_host_permission(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    report = controller.desktop_readiness()
    assert report["ready"] is False
    assert report["permission_status"] == "unknown"
    assert report["readiness_scope"] == "host_declaration"
    assert "mcp_mutations" in report["missing_capabilities"]
    assert "fresh_worker_contexts" in report["missing_capabilities"]
    assert not (tmp_path / "states").exists()


def test_desktop_readiness_reports_declared_capabilities_without_changing_policy(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    declared = {"mcp_mutations": True, "managed_worktree_access": True,
                "fresh_worker_contexts": True, "spawn_workers": True, "stop_and_join": True, "cancel_workers": True}
    report = controller.desktop_readiness(name="maintenance", kind="mission", capabilities=declared)
    assert report["ready"] is True
    assert report["permission_status"] == "declared_allowed"
    assert report["missing_capabilities"] == []
    prompt = report["host_prompt"]
    for phrase in ("maintenance", 'kind="mission"', "one phase", "actual fresh", "work_stopped=true",
                   "desktop_next", "desktop_submit", "desktop_cancel", "mission_start", "mission_status"):
        assert phrase in prompt
    assert "approval policy" in prompt
    assert declared == {"mcp_mutations": True, "managed_worktree_access": True,
                        "fresh_worker_contexts": True, "spawn_workers": True, "stop_and_join": True, "cancel_workers": True}
    assert not (tmp_path / "states").exists()
    declared["mcp_mutations"] = False
    assert controller.desktop_readiness(capabilities=declared)["permission_status"] == "declared_blocked"


@pytest.mark.parametrize("capabilities", [{"mcp_mutations": 1}, {"typo": True}, [], "yes"])
def test_desktop_readiness_rejects_invalid_capability_declarations(tmp_path, capabilities):
    project = tmp_path / "project"
    project.mkdir()
    controller = mcp_server.ControlPlane(tmp_path / "states", [project])
    with pytest.raises(ValueError, match="capabilities"):
        controller.desktop_readiness(capabilities=capabilities)
    assert not (tmp_path / "states").exists()
