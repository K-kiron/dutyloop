"""Short commands should retain safety checks without fixed polling delays."""

import io
import subprocess

import pytest

from dutyloop import process, workspace


def fake_child(monkeypatch):
    class Child:
        returncode = None
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"completed\n")
        stderr = io.BytesIO()

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0
            return self.returncode

    class Job:
        def assign_and_resume(self, child):
            pass

        def close(self):
            pass

    child = Child()
    cleanups = []
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(process, "_WindowsJob", Job)
    monkeypatch.setattr(process, "_cleanup", lambda child, job: cleanups.append(child) or True)

    def no_sleep(seconds):
        raise AssertionError("A completed command must wake the supervisor immediately")

    monkeypatch.setattr(process.time, "sleep", no_sleep)
    return child, cleanups


def test_fast_exit_wakes_wait_and_still_cleans_up(monkeypatch, tmp_path):
    child, cleanups = fake_child(monkeypatch)
    result = process.run_process(["fixture"], cwd=tmp_path, output_dir=tmp_path / "logs",
                                 timeout_seconds=1)
    assert result.error is None
    assert result.returncode == 0
    assert result.stdout == "completed\n"
    assert result.cleanup_confirmed
    assert cleanups == [child]


def test_exit_wait_never_exceeds_remaining_deadline(monkeypatch, tmp_path):
    child, cleanups = fake_child(monkeypatch)
    clock = [0.0]
    waits = []
    monkeypatch.setattr(process.time, "monotonic", lambda: clock[0])

    def wait(timeout):
        if cleanups:
            child.returncode = -1
            return child.returncode
        waits.append(timeout)
        clock[0] += timeout
        raise subprocess.TimeoutExpired("fixture", timeout)

    monkeypatch.setattr(child, "wait", wait)
    result = process.run_process(["fixture"], cwd=tmp_path, output_dir=tmp_path / "logs",
                                 timeout_seconds=.02)
    assert result.error is None
    assert result.timed_out
    assert result.cleanup_confirmed
    assert waits == [.02]
    assert cleanups == [child]


@pytest.mark.parametrize("attached", [False, True])
def test_identity_checks_detached_head_in_one_git_call(monkeypatch, tmp_path, attached):
    project = tmp_path / "project"
    project.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.name", "Fixture Maintainer"),
                 ("config", "user.email", "fixture@example.invalid"),
                 ("commit", "--allow-empty", "-m", "Add fixture")):
        workspace.git(project, *args)
    head = workspace.git(project, "rev-parse", "HEAD")
    checkout = workspace.make_worktree(project, tmp_path / "detached", head)
    if attached:
        workspace.git(checkout, "switch", "-c", "fix/fixture-identity")
    pointer = (checkout / ".git").read_bytes()
    original = workspace.git
    calls = []

    def tracked(path, *args):
        calls.append(args)
        return original(path, *args)

    monkeypatch.setattr(workspace, "git", tracked)
    if attached:
        with pytest.raises(RuntimeError, match="changed or attached"):
            workspace.assert_identity(checkout, head, pointer)
    else:
        workspace.assert_identity(checkout, head, pointer)
    assert calls == [("rev-parse", "HEAD", "--symbolic-full-name", "HEAD")]


@pytest.mark.parametrize("response", ["another-sha\nHEAD", "expected-sha\nHEAD\nHEAD", "expected-sha"])
def test_combined_identity_rejects_changed_or_ambiguous_response(monkeypatch, tmp_path, response):
    pointer = b"gitdir: fixture\n"
    (tmp_path / ".git").write_bytes(pointer)
    monkeypatch.setattr(workspace, "git", lambda *args: response)
    with pytest.raises(RuntimeError, match="changed or attached"):
        workspace.assert_identity(tmp_path, "expected-sha", pointer)


def test_identity_pointer_change_is_rejected_before_git(monkeypatch, tmp_path):
    (tmp_path / ".git").write_bytes(b"changed")

    def no_git(*args):
        pytest.fail("Changed worktree identity must be rejected before invoking Git")

    monkeypatch.setattr(workspace, "git", no_git)
    with pytest.raises(RuntimeError, match="worktree identity"):
        workspace.assert_identity(tmp_path, "expected-sha", b"original")
