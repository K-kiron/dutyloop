"""A deterministic, explicitly simulated exercise of the real engine."""

from __future__ import annotations

from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
from typing import Callable

from .drivers import DriverRequest, DriverResult


_PRICING = """def discounted_price(amount, percent):
    return round(amount, 2)
"""
_LABELS = """def slugify(label):
    return label.lower().replace(" ", "-")
"""
_BASE_TESTS = """import unittest
from labels import slugify
from pricing import discounted_price


class ExistingBehaviorTests(unittest.TestCase):
    def test_price_without_discount(self):
        self.assertEqual(discounted_price(20, 0), 20)

    def test_space_in_label(self):
        self.assertEqual(slugify("Hello World"), "hello-world")
"""


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Demo Git operation failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _defects(project: Path) -> list[dict]:
    """Inspect executable fixture files anew; this is a scripted oracle, not an LLM."""
    candidates = []
    for module in sorted(project.glob("*.py")):
        namespace = runpy.run_path(str(module))
        price = namespace.get("discounted_price")
        slugify = namespace.get("slugify")
        if callable(price) and price(100, 20) != 80:
            candidates.append({
                "key": "apply-percentage-discount",
                "title": "Apply the supplied percentage discount",
                "kind": "bugfix",
                "why": "Discounted prices must reflect the supplied percentage.",
                "priority": 1,
                "evidence": [{
                    "source": module.name,
                    "observation": f"discounted_price(100, 20) returns {price(100, 20)!r}; expected 80.",
                }],
                "acceptance": ["A 20 percent discount on 100 returns 80.", "Existing pricing tests pass."],
            })
        if callable(slugify) and slugify("Hello_World") != "hello-world":
            candidates.append({
                "key": "normalize-label-underscores",
                "title": "Normalize underscores in label slugs",
                "kind": "bugfix",
                "why": "Underscores and spaces must produce the same URL separator.",
                "priority": 2,
                "evidence": [{
                    "source": module.name,
                    "observation": f"slugify('Hello_World') returns {slugify('Hello_World')!r}; expected 'hello-world'.",
                }],
                "acceptance": ["Hello_World becomes hello-world.", "Existing label tests pass."],
            })
    return candidates


class DemoDriver:
    """Scripted local driver for fixture integration tests; never calls a model."""

    def __init__(self) -> None:
        self.phases: list[str] = []
        self.discovery_results: list[str] = []
        self._active_key: str | None = None

    def run(self, request: DriverRequest, should_stop: Callable[[], bool]) -> DriverResult:
        if should_stop():
            return DriverResult(cancelled=True, error="Demonstration stopped.")
        phase = "discover" if request.phase == "discovery" else request.phase
        self.phases.append(phase)
        if phase == "discover":
            candidates = _defects(request.cwd)
            self._active_key = candidates[0]["key"] if candidates else None
            result_status = "work" if candidates else "wait"
            self.discovery_results.append(result_status)
            return DriverResult(payload={
                "status": result_status,
                "summary": "SIMULATED: inspected the fixture files for unmet behavior.",
                "candidates": candidates[:1],
            })
        if phase == "implement":
            if self._active_key == "apply-percentage-discount":
                (request.cwd / "pricing.py").write_text(
                    "def discounted_price(amount, percent):\n"
                    "    return round(amount * (1 - percent / 100), 2)\n",
                    encoding="utf-8",
                )
                (request.cwd / "tests" / "test_discount.py").write_text(
                    "import unittest\nfrom pricing import discounted_price\n\n"
                    "class DiscountTests(unittest.TestCase):\n"
                    "    def test_percentage_discount(self):\n"
                    "        self.assertEqual(discounted_price(100, 20), 80)\n",
                    encoding="utf-8",
                )
            elif self._active_key == "normalize-label-underscores":
                (request.cwd / "labels.py").write_text(
                    "def slugify(label):\n"
                    '    return label.lower().replace(" ", "-").replace("_", "-")\n',
                    encoding="utf-8",
                )
                (request.cwd / "tests" / "test_underscores.py").write_text(
                    "import unittest\nfrom labels import slugify\n\n"
                    "class UnderscoreTests(unittest.TestCase):\n"
                    "    def test_underscore_separator(self):\n"
                    '        self.assertEqual(slugify("Hello_World"), "hello-world")\n',
                    encoding="utf-8",
                )
            else:
                return DriverResult(error="No observed demonstration defect is selected.", returncode=1)
            return DriverResult(payload={
                "completed": True,
                "summary": "SIMULATED: fixed the observed defect and added a regression test.",
            })
        if phase == "review":
            remaining = {candidate["key"] for candidate in _defects(request.cwd)}
            accepted = self._active_key is not None and self._active_key not in remaining
            return DriverResult(payload={
                "accepted": accepted,
                "summary": "SIMULATED: checked the selected fixture behavior after implementation.",
                "findings": [] if accepted else ["The selected defect still reproduces."],
            })
        return DriverResult(returncode=1, error=f"Unsupported demonstration phase: {phase}")


def run_demo(directory: Path) -> dict:
    """Create a disposable fixture and run the actual engine with a fake driver.

    The directory must be absent or empty. Git uses the caller's existing
    identity, including process-local identity variables; no config is changed.
    """
    from .engine import initialize, run

    directory = Path(directory).absolute()
    for path in (directory, *directory.parents):
        try:
            information = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(information.st_mode) or getattr(information, "st_file_attributes", 0) & 0x400:
            raise ValueError("The demo directory cannot traverse a symlink or reparse point.")
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("The demo directory must be absent or empty; existing content is preserved.")
    if shutil.which("git") is None:
        raise RuntimeError("Git must already be installed to run the demonstration.")
    directory.mkdir(parents=True, exist_ok=True)
    project = directory / "project"
    project.mkdir()
    (project / "tests").mkdir()
    (project / "pricing.py").write_text(_PRICING, encoding="utf-8")
    (project / "labels.py").write_text(_LABELS, encoding="utf-8")
    (project / "tests" / "test_existing.py").write_text(_BASE_TESTS, encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "add", "--", "pricing.py", "labels.py", "tests")
    _git(project, "commit", "--quiet", "-m", "Add demonstration fixture")
    original_head = _git(project, "rev-parse", "HEAD")
    original_files = {
        str(path.relative_to(project)): path.read_bytes()
        for path in project.rglob("*.py")
    }
    verification = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
    state_dir = directory / "state"
    initialize({
        "version": 1,
        "mission": "Maintain correct prices and stable label URLs. Discover and fix defects, verify each change, and continue inspecting until a limit or explicit stop.",
        "project": str(project),
        "allowed_paths": ["pricing.py", "labels.py", "tests"],
        "verification": [verification],
        "limits": {"max_invocations": 7, "max_wall_seconds": 120},
        "poll_seconds": 0.05,
        "kinds": ["bugfix"],
        "constraints": ["Preserve existing behavior outside the selected defect."],
        "branch": "work/demonstration",
    }, state_dir)
    driver = DemoDriver()
    snapshot = run(state_dir, driver=driver)
    original_unchanged = (
        _git(project, "rev-parse", "HEAD") == original_head
        and _git(project, "status", "--porcelain") == ""
        and all((project / name).read_bytes() == contents for name, contents in original_files.items())
    )
    accepted_check = directory / "accepted-check"
    _git(project, "worktree", "add", "--quiet", "--detach", str(accepted_check), snapshot["accepted_head"])
    check = subprocess.run(verification, cwd=accepted_check, capture_output=True, text=True, check=False)
    (directory / "accepted-check.stdout.log").write_text(check.stdout, encoding="utf-8")
    (directory / "accepted-check.stderr.log").write_text(check.stderr, encoding="utf-8")
    return {
        "simulated": True,
        "notice": "The driver and its judgments are scripted. This demonstrates engine integration, not LLM effectiveness.",
        "project": str(project),
        "original_head": original_head,
        "original_unchanged": original_unchanged,
        "accepted_tests_passed": check.returncode == 0,
        "accepted_tasks": sum(task.get("state") == "accepted" for task in snapshot["tasks"]),
        "phases": driver.phases,
        "discovery_results": driver.discovery_results,
        "snapshot": snapshot,
    }
