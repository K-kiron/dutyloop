"""Bounded subprocess capture and process-tree cleanup, without a shell.

Windows processes start suspended, enter a kill-on-close Job Object, then
resume. Failure to establish that containment fails before executing the child.
POSIX children get a new process group. Neither mechanism is a filesystem or
network sandbox, and neither undoes external effects already performed.
"""

from dataclasses import dataclass
from pathlib import Path
import math
import os
import signal
import subprocess
import threading
import time
from typing import Callable


@dataclass
class ProcessResult:
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    cancelled: bool = False
    timed_out: bool = False
    cleanup_confirmed: bool = True
    output_truncated: bool = False


class _WindowsJob:
    """Documented Windows Job Object and Toolhelp APIs, loaded only on Windows."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.assigned = False
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_limit", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                        ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IO),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ("user_time", "kernel_time", "period_user", "period_kernel")]
            _fields_ += [(name, w.DWORD) for name in
                         ("page_faults", "total", "active", "terminated")]
        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("tid", w.DWORD),
                        ("pid", w.DWORD), ("base_priority", w.LONG),
                        ("delta_priority", w.LONG), ("flags", w.DWORD)]
        self.Accounting, self.ThreadEntry = Accounting, ThreadEntry
        prototypes = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (args, restype) in prototypes.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, restype
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process):
        c, api = self.ctypes, self.api
        if not api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise c.WinError(c.get_last_error())
        self.assigned = True
        snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = self.ThreadEntry()
            entry.size = c.sizeof(entry)
            more = api.Thread32First(snapshot, c.byref(entry))
            while more:
                if entry.pid == process.pid:
                    thread = api.OpenThread(0x2, False, entry.tid)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                        return
                    finally:
                        api.CloseHandle(thread)
                more = api.Thread32Next(snapshot, c.byref(entry))
            raise OSError("Cannot find suspended child's initial thread")
        finally:
            api.CloseHandle(snapshot)

    def empty(self):
        info = self.Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(info),
                                                 self.ctypes.sizeof(info), None):
            return False
        return info.active == 0

    def terminate(self):
        return bool(self.api.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _group_empty(pid):
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def _cleanup(process, job):
    if job is not None:
        if not job.assigned and process.poll() is None:
            # Assignment failed while the root was still suspended: it has
            # never executed and cannot have spawned a descendant.
            process.kill()
            process.wait(timeout=3)
        job.terminate()
        end = time.monotonic() + 3
        while not job.empty() and time.monotonic() < end:
            time.sleep(0.02)
        return job.empty()
    for sig, grace in ((signal.SIGTERM, 0.3), (signal.SIGKILL, 1.0)):
        if _group_empty(process.pid):
            return True
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return True
        end = time.monotonic() + grace
        while time.monotonic() < end:
            process.poll()  # reap the root before checking its process group
            if _group_empty(process.pid):
                return True
            time.sleep(0.02)
    return _group_empty(process.pid)


def run_process(argv, *, cwd: Path, input_text: str = "", output_dir: Path,
                timeout_seconds: float, should_stop: Callable[[], bool] = lambda: False,
                max_output_bytes: int = 1024 * 1024, env: dict[str, str] | None = None) -> ProcessResult:
    """Run argv with bounded stdout/stderr (each cap includes its on-disk log).

    Output beyond each cap is drained and discarded, not retained in memory or
    logs. Cancellation is immediate tree termination, not a graceful host turn
    interruption. A successful cleanup does not mean remote work was undone.
    """
    result = ProcessResult()
    if not argv or not all(isinstance(arg, str) and arg for arg in argv):
        result.error = "argv must contain nonempty strings"
        return result
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or max_output_bytes < 1:
        result.error = "timeout and output cap must be positive"
        return result
    if env is not None and (not isinstance(env, dict) or any(not isinstance(key, str) or not key or "=" in key or "\x00" in key
                              or not isinstance(value, str) or "\x00" in value for key, value in env.items())):
        result.error = "env must contain valid string environment overrides"
        return result
    output_dir = Path(output_dir)
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    io_errors = []
    process = job = None
    readers = []
    writer = None
    started = time.monotonic()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if should_stop():
            result.cancelled = True
            return result
        options = {"cwd": str(cwd), "stdin": subprocess.PIPE,
                   "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                   "shell": False}
        if env is not None:
            options["env"] = {**os.environ, **env}
        if os.name == "nt":
            job = _WindowsJob()
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(argv, **options)
        if job:
            job.assign_and_resume(process)

        def capture(name, pipe):
            try:
                with (output_dir / f"{name}.log").open("wb") as log:
                    while chunk := pipe.read(65536):
                        remaining = max_output_bytes - len(streams[name])
                        accepted = chunk[:max(0, remaining)]
                        streams[name].extend(accepted)
                        log.write(accepted)
                        if len(accepted) != len(chunk):
                            result.output_truncated = True
            except (OSError, ValueError) as exc:
                io_errors.append(str(exc))
            finally:
                pipe.close()

        def write_input():
            try:
                process.stdin.write(input_text.encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

        for name in streams:
            thread = threading.Thread(target=capture, args=(name, getattr(process, name)), daemon=True)
            thread.start()
            readers.append(thread)
        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        while process.poll() is None:
            if should_stop():
                result.cancelled = True
                break
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                result.timed_out = True
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                pass
    except (OSError, ValueError, RuntimeError) as exc:
        result.error = f"Cannot run child process: {exc}"
    finally:
        if process is not None:
            # Also clean descendants left alive after a successful root exit.
            try:
                result.cleanup_confirmed = _cleanup(process, job)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result.cleanup_confirmed = False
                result.error = result.error or f"Process-tree cleanup failed: {exc}"
            try:
                result.returncode = process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                result.cleanup_confirmed = False
                try:
                    process.kill()
                    result.returncode = process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    result.error = result.error or "Child process could not be reaped"
            for thread in readers:
                thread.join(timeout=3)
            if writer:
                writer.join(timeout=3)
            if any(thread.is_alive() for thread in readers) or (writer and writer.is_alive()):
                result.cleanup_confirmed = False
                result.error = result.error or "Child pipe cleanup could not be confirmed"
            if not readers:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe:
                        pipe.close()
        if job:
            try:
                job.close()
            except OSError:
                result.cleanup_confirmed = False
        result.stdout = streams["stdout"].decode("utf-8", errors="replace")
        result.stderr = streams["stderr"].decode("utf-8", errors="replace")
        if io_errors:
            result.error = result.error or "Child log capture failed"
        if not result.cleanup_confirmed:
            result.error = result.error or "Process-tree cleanup could not be confirmed"
    return result
