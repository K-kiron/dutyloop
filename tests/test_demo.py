"""The scripted demonstration must exercise the real mission engine."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dutyloop.demo import run_demo
from dutyloop.demo import DemoDriver
from dutyloop.drivers import DriverRequest


@unittest.skipUnless(shutil.which("git"), "Git is required for the worktree demonstration")
class DemoTests(unittest.TestCase):
    def test_discovery_reads_files_instead_of_replaying_an_initial_task_list(self):
        with tempfile.TemporaryDirectory(prefix="dutyloop-demo-discover-") as temporary:
            project = Path(temporary)
            module = project / "pricing.py"
            module.write_text("def discounted_price(amount, percent):\n    return amount\n", encoding="utf-8")
            driver = DemoDriver()
            request = DriverRequest("discover", "Inspect current files.", project, {}, project / "output", 5)
            first = driver.run(request, lambda: False)
            self.assertEqual(first.payload["status"], "work")
            self.assertEqual(first.payload["candidates"][0]["evidence"][0]["source"], "pricing.py")
            # Change the evidence without running implementation: the next discovery must notice.
            module.write_text("def discounted_price(amount, percent):\n    return amount * (1 - percent / 100)\n", encoding="utf-8")
            second = driver.run(request, lambda: False)
            self.assertEqual(second.payload["status"], "wait")
            self.assertEqual(second.payload["candidates"], [])

    def test_discovers_two_defects_continues_and_stops_at_call_budget(self):
        # A fixture identity is process-local and cannot change the user's Git config.
        fixture_identity = {
            "GIT_AUTHOR_NAME": "DutyLoop fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "DutyLoop fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        with tempfile.TemporaryDirectory(prefix="dutyloop-demo-test-") as temporary:
            with patch.dict(os.environ, fixture_identity):
                result = run_demo(Path(temporary) / "demo")

            self.assertTrue(result["simulated"])
            self.assertTrue(result["original_unchanged"])
            self.assertTrue(result["accepted_tests_passed"])
            self.assertEqual(result["accepted_tasks"], 2)
            snapshot = result["snapshot"]
            self.assertEqual(snapshot["status"], "limited")
            self.assertEqual(snapshot["invocations"], 7)
            self.assertEqual(
                result["phases"],
                ["discover", "implement", "review"] * 2 + ["discover"],
            )
            self.assertEqual(result["discovery_results"], ["work", "work", "wait"])
            self.assertNotEqual(snapshot["accepted_head"], result["original_head"])

            # Fixes live on the accepted branch; the original checkout still has both bugs.
            project = Path(result["project"])
            checkout = subprocess.check_output(
                ["git", "-C", str(project), "status", "--porcelain"], text=True
            )
            self.assertEqual(checkout, "")
            original = (project / "pricing.py").read_text(encoding="utf-8")
            accepted = subprocess.check_output(
                ["git", "-C", str(project), "show", f"{snapshot['accepted_head']}:pricing.py"],
                text=True,
            )
            self.assertNotEqual(original, accepted)

    def test_refuses_to_overwrite_existing_demo_content(self):
        with tempfile.TemporaryDirectory(prefix="dutyloop-demo-preserve-") as temporary:
            directory = Path(temporary) / "demo"
            directory.mkdir()
            evidence = directory / "keep.txt"
            evidence.write_text("Keep this evidence.", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_demo(directory)
            self.assertEqual(evidence.read_text(encoding="utf-8"), "Keep this evidence.")


if __name__ == "__main__":
    unittest.main()
