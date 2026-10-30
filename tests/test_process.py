import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from dutyloop.process import run_process


def alive(pid):
    if os.name == "nt":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.OpenProcess.restype = ctypes.c_void_p
        api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        status = Path(f"/proc/{pid}/stat")
        if status.exists() and status.read_text().split(")", 1)[1].split()[0] == "Z":
            return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_child(self, script, **kwargs):
        return run_process([sys.executable, "-c", script], cwd=self.root,
                           output_dir=self.root / "logs", timeout_seconds=kwargs.pop("timeout_seconds", 4),
                           **kwargs)

    def assert_dead(self, pid):
        end = time.monotonic() + 2
        while alive(pid) and time.monotonic() < end:
            time.sleep(0.02)
        self.assertFalse(alive(pid), f"descendant {pid} remains alive")

    def test_capture_stdin_and_working_directory_without_shell(self):
        result = self.run_child("import json,os,sys; print(json.dumps([os.getcwd(),sys.stdin.read()])); print('diagnostic',file=sys.stderr)",
                                input_text="literal ; $() & \"quotes\"\n")
        self.assertIsNone(result.error)
        self.assertEqual(result.returncode, 0)
        cwd, text = json.loads(result.stdout)
        self.assertEqual(Path(cwd), self.root)
        self.assertEqual(text, "literal ; $() & \"quotes\"\n")
        self.assertIn("diagnostic", result.stderr)
        self.assertTrue(result.cleanup_confirmed)

    def test_deadline_stops_child_and_grandchild(self):
        marker = self.root / "grandchild.pid"
        script = ("import subprocess,sys,time,pathlib; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)")
        result = self.run_child(script, timeout_seconds=0.7)
        self.assertTrue(result.timed_out)
        self.assertTrue(marker.is_file(), result.error)
        self.assert_dead(int(marker.read_text()))
        if os.name == "nt":
            self.assertTrue(result.cleanup_confirmed, result.error)

    def test_successful_root_cannot_leave_background_descendant(self):
        script = ("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',"
                  "'import time; time.sleep(60)']); print(p.pid,flush=True)")
        result = self.run_child(script)
        self.assertEqual(result.returncode, 0)
        self.assert_dead(int(result.stdout.strip()))
        if os.name == "nt":
            self.assertTrue(result.cleanup_confirmed, result.error)

    def test_stop_polling_and_preexisting_stop(self):
        start = time.monotonic()
        result = self.run_child("import time; time.sleep(60)",
                                should_stop=lambda: time.monotonic() - start > 0.2)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.timed_out)
        marker = self.root / "must-not-exist"
        result = self.run_child(f"from pathlib import Path; Path({str(marker)!r}).touch()",
                                should_stop=lambda: True)
        self.assertTrue(result.cancelled)
        self.assertFalse(marker.exists())

    def test_bounded_logs_are_drained_and_marked_truncated(self):
        result = self.run_child("import os; os.write(1,b'x'*200000); os.write(2,b'y'*200000)",
                                max_output_bytes=128)
        self.assertIsNone(result.error)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.output_truncated)
        self.assertEqual(len(result.stdout), 128)
        self.assertEqual(len(result.stderr), 128)
        self.assertEqual((self.root / "logs" / "stdout.log").stat().st_size, 128)
        self.assertEqual((self.root / "logs" / "stderr.log").stat().st_size, 128)

    def test_large_unread_stdin_does_not_prevent_deadline(self):
        result = self.run_child("import time; time.sleep(60)", input_text="x" * 2000000,
                                timeout_seconds=0.3)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.cleanup_confirmed, result.error)

    def test_missing_executable_is_an_explicit_error(self):
        result = run_process([str(self.root / "missing-executable")], cwd=self.root,
                             output_dir=self.root / "logs", timeout_seconds=1)
        self.assertIsNotNone(result.error)
        self.assertTrue(result.cleanup_confirmed)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object failure path")
    def test_failed_job_assignment_never_executes_suspended_child(self):
        marker = self.root / "must-not-run"
        with patch("dutyloop.process._WindowsJob.assign_and_resume", side_effect=OSError("assignment refused")):
            result = self.run_child(f"from pathlib import Path; Path({str(marker)!r}).touch()")
        self.assertIsNotNone(result.error)
        self.assertTrue(result.cleanup_confirmed)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
