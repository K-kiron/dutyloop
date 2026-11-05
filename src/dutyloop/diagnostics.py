"""Bounded installation diagnostics; never authenticate or invoke a model."""

from __future__ import annotations

import math
from pathlib import Path
import shutil
import sys
import tempfile

from .drivers import NATIVE_DRIVERS, _native_argv, probe_driver
from .adapters.extended import SUPPORTED_VERSIONS, supported_version
from .adapters.protocol import HostFailure
from .process import run_process


def doctor(*, driver: str | None = None, project: Path | None = None,
           timeout_seconds: float = 5) -> dict:
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ValueError("Diagnostic timeout must be greater than zero and at most 60 seconds")
    if driver is not None and driver not in NATIVE_DRIVERS:
        raise ValueError("Unknown native driver")
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="dutyloop-doctor-") as temporary:
        output_root = Path(temporary)
        counter = 0

        def inspect(argv, *, env=None):
            nonlocal counter
            counter += 1
            result = run_process(argv, cwd=cwd, timeout_seconds=timeout_seconds,
                                 output_dir=output_root / str(counter), max_output_bytes=4096, env=env)
            passed = not (result.error or result.returncode or result.cancelled or
                          result.timed_out or result.output_truncated or not result.cleanup_confirmed)
            return passed, result.stdout.strip()

        drivers = []
        for name in ([driver] if driver else NATIVE_DRIVERS):
            info = {**probe_driver(name), "authentication": "not_checked", "model_access": "not_checked"}
            if info["available"]:
                try:
                    environment = {"OPENCODE_DISABLE_AUTOUPDATE": "1"} if name == "opencode" else None
                    passed, output = inspect([*_native_argv(name), "--version"], env=environment)
                    info.update(status="detected" if passed else "version_probe_failed",
                                version=output.splitlines()[0] if passed and output else None)
                    if passed and name in SUPPORTED_VERSIONS:
                        try:
                            supported_version(name, output)
                            info["protocol_supported"] = True
                        except HostFailure as exc:
                            info.update(status="unsupported_version", protocol_supported=False, diagnostic=str(exc))
                except (OSError, ValueError) as exc:
                    info.update(status="unsupported_launcher", version=None, diagnostic=str(exc))
            else:
                info.update(status="not_installed", version=None)
            drivers.append(info)
        git_info = {"available": shutil.which("git") is not None}
        if git_info["available"]:
            passed, output = inspect(["git", "--version"])
            git_info["version"] = output if passed else None
        result = {"python": sys.version.split()[0], "drivers": drivers, "git": git_info,
                  "model_calls": 0, "note": "Version checks do not verify authentication, quota or a successful model turn."}
        if project is not None:
            project = Path(project).expanduser().resolve()
            prefix = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                      "-C", str(project)]
            root_ok, root = inspect([*prefix, "rev-parse", "--show-toplevel"])
            root_ok = root_ok and Path(root).resolve() == project
            head_ok, head = inspect([*prefix, "rev-parse", "--verify", "HEAD^{commit}"]) if root_ok else (False, "")
            name_ok, _ = inspect([*prefix, "var", "GIT_AUTHOR_IDENT"]) if root_ok else (False, "")
            clean_ok, changes = inspect([*prefix, "status", "--porcelain"]) if root_ok else (False, "")
            result["project"] = {"path": str(project), "git_root": root_ok, "committed_head": head if head_ok else None,
                                 "git_identity_configured": name_ok,
                                 "uncommitted_changes": bool(changes) if clean_ok else None}
        return result
