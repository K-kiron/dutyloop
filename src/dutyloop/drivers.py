"""Adapters for installed host CLIs and the documented custom command protocol."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import time
from typing import Callable

from .adapters.extended import dsh_launch, opencode_launch, parse_dsh, parse_opencode, supported_version
from .adapters.protocol import HostFailure, events as _events, failure_kind, loads as _loads, money as _money
from .process import run_process
from .schemas import validate_payload


NATIVE_DRIVERS = ("codex", "claude", "opencode", "dsh")


@dataclass
class DriverRequest:
    phase: str
    prompt: str
    cwd: Path
    schema: dict
    output_dir: Path
    timeout_seconds: float
    model: str | None = None


@dataclass
class DriverResult:
    payload: dict | None = None
    returncode: int = 0
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    session_id: str | None = None
    error: str | None = None
    cancelled: bool = False
    timed_out: bool = False
    cleanup_confirmed: bool = True
    error_kind: str | None = None


def probe_driver(name: str) -> dict:
    """Report PATH availability only; do not run a CLI or inspect credentials."""
    if name not in NATIVE_DRIVERS:
        return {"name": name, "available": False, "executable": None}
    executable = shutil.which(name)
    return {"name": name, "available": executable is not None, "executable": executable}


def _native_argv(name):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"{name} executable is not available on PATH")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # Official npm shims have predictable published JS entry points.
        # Do not interpret a batch file or invoke cmd.exe on model-owned text.
        entrypoints = {
            "codex": ("@openai", "codex", "bin", "codex.js"),
            "opencode": ("@opencode", "cli", "bin", "opencode.cjs"),
            "dsh": ("@deepseek-ai", "dsh", "lib", "bin.js"),
        }
        entry = path.parent.joinpath("node_modules", *entrypoints.get(name, ("unsupported",)))
        node = shutil.which("node")
        if name in entrypoints and node and entry.is_file():
            return [node, str(entry)]
        raise ValueError(f"{name} uses an unsupported command wrapper; install its native executable")
    return [executable]


def _parse_codex(text, final_path):
    events = _events(text)
    session_id = None
    usage = {}
    final_text = None
    completed = False
    for event in events:
        kind = event.get("type")
        if kind == "thread.started":
            session_id = event.get("thread_id") if isinstance(event.get("thread_id"), str) else None
        elif kind in {"turn.failed", "error"}:
            error = event.get("error")
            code = error.get("code") if isinstance(error, dict) else event.get("code")
            raise HostFailure("Codex reported a failed turn; inspect the local invocation logs.", failure_kind(code))
        elif kind == "turn.completed":
            completed = True
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
        elif kind == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                final_text = item.get("text")
    if not completed:
        raise ValueError("Codex emitted no completed turn")
    if final_path.is_file():
        if final_path.stat().st_size > 1024 * 1024:
            raise ValueError("Codex final structured output exceeds size limit")
        final_text = final_path.read_text(encoding="utf-8")
    if not isinstance(final_text, str):
        raise ValueError("Codex emitted no final structured output")
    return _loads(final_text), usage, None, session_id


def _parse_claude(text):
    results = [event for event in _events(text) if event.get("type") == "result"]
    if not results:
        raise ValueError("Claude emitted no final result")
    final = results[-1]
    if final.get("subtype") != "success" or final.get("is_error") is True:
        raise HostFailure("Claude did not complete successfully; inspect the local invocation logs.",
                          failure_kind(final.get("subtype")))
    if not isinstance(final.get("structured_output"), dict):
        raise ValueError("Claude emitted no final structured output")
    usage = dict(final["usage"]) if isinstance(final.get("usage"), dict) else {}
    if isinstance(final.get("modelUsage"), dict):
        usage["model_usage"] = final["modelUsage"]
    session_id = final.get("session_id") if isinstance(final.get("session_id"), str) else None
    return final["structured_output"], usage, _money(final.get("total_cost_usd")), session_id


class _Driver:
    def __init__(self, config):
        self.config = dict(config)
        self.name = self.config.get("name", "codex")

    def run(self, request: DriverRequest, should_stop: Callable[[], bool]) -> DriverResult:
        output_dir = Path(request.output_dir)
        parsing = False
        try:
            if request.phase not in {"discover", "implement", "review"}:
                raise ValueError("Driver phase must be discover, implement, or review")
            output_dir.mkdir(parents=True, exist_ok=True)
            model = request.model or self.config.get("model")
            env = {}
            started = time.monotonic()
            if self.name == "command":
                argv = self.config["argv"]
                input_text = json.dumps({
                    "phase": request.phase, "prompt": request.prompt, "cwd": str(request.cwd),
                    "schema": request.schema, "model": model,
                }, ensure_ascii=False) + "\n"
            else:
                argv = _native_argv(self.name)
                input_text = request.prompt
                if self.name == "codex":
                    schema_path = output_dir / "response.schema.json"
                    final_path = output_dir / "final.json"
                    if final_path.exists():
                        raise ValueError("Invocation output directory already contains a final response")
                    schema_path.write_text(json.dumps(request.schema), encoding="utf-8")
                    sandbox = "workspace-write" if request.phase == "implement" else "read-only"
                    argv += ["exec", "--json", "--ephemeral", "-C", str(request.cwd), "-s", sandbox,
                             "-c", 'approval_policy="never"', "--output-schema", str(schema_path),
                             "--output-last-message", str(final_path)]
                    if model:
                        argv += ["--model", model]
                    argv += ["-"]
                elif self.name == "claude":
                    tools = "Read,Glob,Grep,Edit,Write,Bash" if request.phase == "implement" else "Read,Glob,Grep"
                    argv += ["-p", "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--json-schema",
                             json.dumps(request.schema), "--permission-mode", "dontAsk", "--tools", tools,
                             "--allowedTools", tools, "--disallowedTools", "mcp__*"]
                    if model:
                        argv += ["--model", model]
                else:
                    # No model work before validating the exact known protocol.
                    version = run_process(argv + ["--version"], cwd=request.cwd, output_dir=output_dir / "version",
                                          timeout_seconds=min(10, request.timeout_seconds), should_stop=should_stop,
                                          env={"OPENCODE_DISABLE_AUTOUPDATE": "1"} if self.name == "opencode" else {})
                    failure = _process_failure(version)
                    if failure:
                        failure.error = "Host version check failed; " + failure.error
                        if not failure.cancelled and not failure.timed_out:
                            failure.error_kind = "config"
                        return failure
                    supported_version(self.name, version.stdout)
                    launcher = opencode_launch if self.name == "opencode" else dsh_launch
                    arguments, input_text, env = launcher(request, model)
                    argv += arguments
            remaining = request.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return DriverResult(returncode=-1, timed_out=True, error="Driver deadline exceeded during setup")
            process = run_process(argv, cwd=request.cwd, input_text=input_text, output_dir=output_dir,
                                  timeout_seconds=remaining, should_stop=should_stop, env=env)
            result = DriverResult(returncode=process.returncode, cancelled=process.cancelled,
                                  timed_out=process.timed_out, cleanup_confirmed=process.cleanup_confirmed)
            failure = _process_failure(process)
            if failure:
                if process.cleanup_confirmed and not process.cancelled and not process.timed_out:
                    failure.error_kind = _reported_failure_kind(self.name, process.stdout) or failure.error_kind
                return failure
            parsing = True
            if self.name == "codex":
                values = _parse_codex(process.stdout, final_path)
            elif self.name == "claude":
                values = _parse_claude(process.stdout)
            elif self.name == "opencode":
                values = parse_opencode(process.stdout)
            elif self.name == "dsh":
                values = parse_dsh(process.stdout)
            else:
                envelope = _loads(process.stdout)
                if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
                    raise ValueError("Command driver must emit one object containing an object payload")
                values = (envelope["payload"], envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {},
                          _money(envelope.get("cost_usd")), None)
            result.payload, result.usage, result.cost_usd, result.session_id = values
            if not isinstance(result.payload, dict):
                raise ValueError("Final structured output must be an object")
            validate_payload(result.payload, request.schema)
            return result
        except HostFailure as exc:
            return DriverResult(returncode=-1, error=str(exc), error_kind=exc.kind)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return DriverResult(returncode=-1, error=f"Invalid driver response or configuration: {exc}",
                                error_kind="protocol" if parsing else "config")


def _process_failure(process):
    result = DriverResult(returncode=process.returncode, cancelled=process.cancelled,
                          timed_out=process.timed_out, cleanup_confirmed=process.cleanup_confirmed)
    if process.error or result.cancelled or result.timed_out or not result.cleanup_confirmed or process.returncode != 0:
        result.error = process.error or ("Driver cancelled" if result.cancelled else
                       "Driver deadline exceeded" if result.timed_out else
                       "Process-tree cleanup could not be confirmed" if not result.cleanup_confirmed else
                       f"Driver exited with code {process.returncode}")
        return result
    if process.output_truncated:
        result.error = "Driver output exceeded capture limit; final protocol cannot be trusted"
        result.error_kind = "protocol"
        return result
    return None


def _reported_failure_kind(name, text):
    """Read only known machine error fields; diagnostic prose stays in local logs."""
    if name == "command":
        return None
    try:
        for event in _events(text):
            kind = event.get("type")
            if name == "claude" and kind == "result" and event.get("subtype") != "success":
                return failure_kind(event.get("subtype"))
            if kind in {"error", "turn.failed"}:
                error = event.get("error")
                code = (error.get("code") or error.get("type") or error.get("name")) if isinstance(error, dict) else event.get("code")
                found = failure_kind(code)
                if found:
                    return found
            if name == "dsh" and kind == "final":
                reason = event.get("reason")
                error = reason.get("error") if isinstance(reason, dict) else None
                if isinstance(error, dict):
                    return failure_kind(error.get("code"))
    except (ValueError, TypeError, RecursionError):
        pass
    return None


def get_driver(config: dict):
    config = dict(config)
    name = config.get("name", "codex")
    if name not in {*NATIVE_DRIVERS, "command"}:
        raise ValueError("driver name must be " + ", ".join(NATIVE_DRIVERS) + ", or command")
    if name == "command":
        argv = config.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError("command driver requires a nonempty argv array")
        if Path(argv[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("command driver requires an executable, not a shell script wrapper")
    return _Driver(config)
