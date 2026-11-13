"""Scoped Windows Git long-path support retains process controls and user config."""

from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from dutyloop import engine, workspace
from dutyloop.process import ProcessResult


def raw_git(project, *arguments, check=True):
    return subprocess.run(["git", "-C", str(project), *arguments],
                          stdin=subprocess.DEVNULL, capture_output=True, check=check, timeout=15)


@pytest.fixture
def long_path_repository():
    # Keep Git's own administrative paths short while the tracked checkout
    # crosses MAX_PATH, independently of pytest's generated directory names.
    with tempfile.TemporaryDirectory(prefix="dl-path-") as directory:
        root = Path(directory)
        project = root / "repo"
        project.mkdir()
        raw_git(project, "init", "-b", "main")
        raw_git(project, "config", "user.name", "Fixture Maintainer")
        raw_git(project, "config", "user.email", "fixture@example.invalid")
        raw_git(project, "config", "--local", "core.longpaths", "false")
        relative = Path("contracts") / ("section-" + "s" * 32) / ("clauses-" + "c" * 32) / "acceptance.txt"
        original = project / relative
        original.parent.mkdir(parents=True)
        original.write_text("Preserve every acceptance criterion.\n", encoding="utf-8")
        raw_git(project, "-c", "core.longpaths=true", "add", "--", relative.as_posix())
        raw_git(project, "commit", "-m", "Add nested acceptance fixture")
        head = raw_git(project, "rev-parse", "HEAD").stdout.decode().strip()
        managed = root / "state" / "bases"
        while len(str(managed)) < 170:
            managed /= "x" * min(24, max(1, 170 - len(str(managed)) - 1))
        managed.mkdir(parents=True)
        yield project, relative, managed, head


@pytest.mark.skipif(sys.platform != "win32", reason="Git for Windows MAX_PATH behavior")
@pytest.mark.parametrize("controlled", [False, True])
def test_managed_checkout_handles_long_paths_without_persisting_git_config(long_path_repository, tmp_path, controlled):
    project, relative, managed, head = long_path_repository
    config = project / ".git" / "config"
    before = config.read_bytes()
    baseline = managed / "without-support"
    assert len(str(baseline / relative)) > 260
    assert len(str(baseline / ".git")) < 250
    failed = raw_git(project, "-c", "core.longpaths=false", "worktree", "add", "--detach",
                     str(baseline), head, check=False)
    assert failed.returncode != 0 and b"Filename too long" in failed.stderr
    target = managed / "with-support"
    control = workspace.controlled_git(lambda: False, lambda: 15, tmp_path / "git-logs") if controlled else nullcontext()
    with control:
        created = workspace.make_worktree(project, target, head)
    assert created == target.resolve()
    assert (created / relative).read_text(encoding="utf-8") == "Preserve every acceptance criterion.\n"
    assert workspace.git(created, "rev-parse", "HEAD") == head
    assert workspace.git(created, "status", "--porcelain") == ""
    # Child mission initialization reuses this checkout and must also succeed.
    state = tmp_path / "child-mission"
    initialized = engine.initialize({
        "mission": "Inspect the complete nested checkout", "project": str(created),
        "allowed_paths": ["."], "verification": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "driver": {"name": "desktop"}, "limits": {"max_invocations": 1, "max_wall_seconds": 15},
    }, state)
    assert initialized["accepted_head"] == head
    assert config.read_bytes() == before
    assert raw_git(project, "config", "--local", "--get", "core.longpaths").stdout.strip() == b"false"


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("controlled", [False, True])
def test_git_longpath_option_precedes_command_without_rewriting_positional_arguments(tmp_path, monkeypatch,
                                                                                   platform, controlled):
    monkeypatch.setattr(workspace, "sys", SimpleNamespace(platform=platform), raising=False)
    calls = []
    arguments = ("show", "HEAD:folder/-c core.longpaths=false.txt")

    def stop():
        return False

    def remaining():
        return .25

    def run_controlled(argv, **options):
        calls.append((argv, options))
        options["output_dir"].mkdir(parents=True)
        return ProcessResult(returncode=0, stdout="complete fixture content\n")

    def run_ordinary(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(returncode=0, stdout=b"complete fixture content\n", stderr=b"")

    monkeypatch.setattr(workspace, "run_process", run_controlled)
    monkeypatch.setattr(workspace.subprocess, "run", run_ordinary)
    control = workspace.controlled_git(stop, remaining, tmp_path / "logs") if controlled else nullcontext()
    with control:
        assert workspace.git(tmp_path, *arguments) == "complete fixture content"
    argv, options = calls[0]
    expected = ["git", "-C", str(tmp_path),
                *(["-c", "core.longpaths=true"] if platform == "win32" else []), *arguments]
    assert argv == expected
    if controlled:
        assert options["should_stop"] is stop and options["timeout_seconds"] == .25
        assert options["cwd"] == tmp_path
        assert not options["output_dir"].exists()
    else:
        assert options["stdin"] == subprocess.DEVNULL and options["timeout"] == 60
    assert workspace._CONTROL.get() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Git for Windows long-path checkout")
def test_preexisting_stop_prevents_longpath_checkout(long_path_repository, tmp_path):
    project, _relative, managed, head = long_path_repository
    target = managed / "must-not-start"
    before = raw_git(project, "worktree", "list", "--porcelain").stdout
    with workspace.controlled_git(lambda: True, lambda: 15, tmp_path / "stopped-git"):
        with pytest.raises(RuntimeError, match="interrupted"):
            workspace.make_worktree(project, target, head)
    assert not target.exists()
    assert raw_git(project, "worktree", "list", "--porcelain").stdout == before


def test_failed_checkout_retains_partial_evidence_and_structured_receipt(tmp_path, monkeypatch):
    target = tmp_path / "managed" / "failed"
    head = "a" * 40

    def failed_git(_project, *_arguments):
        target.mkdir()
        (target / "partial.txt").write_text("Retained checkout evidence", encoding="utf-8")
        raise RuntimeError("Filename too long")

    monkeypatch.setattr(workspace, "git", failed_git)
    with pytest.raises(workspace.WorkspacePreparationError) as caught:
        workspace.make_worktree(tmp_path, target, head)
    error = caught.value
    assert error.path == target.resolve() and error.head == head and error.detail == "Filename too long"
    assert error.receipt() == {"operation": "worktree_add", "worktree": str(target.resolve()),
                               "head": head, "error": "Filename too long"}
    assert (target / "partial.txt").read_text(encoding="utf-8") == "Retained checkout evidence"


def test_unknown_git_cleanup_is_never_wrapped_as_known_preparation_failure(tmp_path, monkeypatch):
    target = tmp_path / "managed" / "unknown"
    failure = workspace.GitCleanupError("Git process cleanup remains unknown")

    def unknown_git(_project, *_arguments):
        target.mkdir()
        raise failure

    monkeypatch.setattr(workspace, "git", unknown_git)
    with pytest.raises(workspace.GitCleanupError) as caught:
        workspace.make_worktree(tmp_path, target, "a" * 40)
    assert caught.value is failure and not isinstance(caught.value, workspace.WorkspacePreparationError)
    assert target.is_dir()


def test_existing_worktree_guard_stays_a_value_error(tmp_path, monkeypatch):
    target = tmp_path / "existing"
    target.mkdir()
    monkeypatch.setattr(workspace, "git", lambda *_args: pytest.fail("An existing path must not launch Git"))
    with pytest.raises(ValueError, match="already exists"):
        workspace.make_worktree(tmp_path, target, "a" * 40)


def test_path_safety_guard_is_never_wrapped_as_preparation_failure(tmp_path, monkeypatch):
    failure = ValueError("Fixture symlink guard")

    def reject(_path):
        raise failure

    monkeypatch.setattr(workspace, "plain_path", reject)
    monkeypatch.setattr(workspace, "git", lambda *_args: pytest.fail("Unsafe paths must not launch Git"))
    with pytest.raises(ValueError) as caught:
        workspace.make_worktree(tmp_path, tmp_path / "unsafe", "a" * 40)
    assert caught.value is failure


@pytest.mark.parametrize("failure_location", ["mkdir", "launch"])
def test_clean_filesystem_or_launch_failure_has_preparation_receipt(tmp_path, monkeypatch, failure_location):
    target = tmp_path / "managed" / "failed"
    original_mkdir = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == target.parent and failure_location == "mkdir":
            raise PermissionError("Fixture parent directory denied")
        return original_mkdir(path, *args, **kwargs)

    def launch(*_args):
        raise FileNotFoundError("Fixture Git executable unavailable")

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(workspace, "git", launch)
    with pytest.raises(workspace.WorkspacePreparationError) as caught:
        workspace.make_worktree(tmp_path, target, "a" * 40)
    assert caught.value.receipt()["operation"] == "worktree_add"
    assert caught.value.path == target.resolve() and not target.exists()
    expected = PermissionError if failure_location == "mkdir" else FileNotFoundError
    assert isinstance(caught.value.__cause__, expected)
