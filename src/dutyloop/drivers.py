"""Adapters for installed host CLIs and the documented custom command protocol."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import shutil
from typing import Callable

from .process import run_process
from .schemas import validate_payload


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


def probe_driver(name: str) -> dict:
    """Report PATH availability only; do not run a CLI or inspect credentials."""
    if name not in {"codex", "claude"}:
        return {"name": name, "available": False, "executable": None}
    executable = shutil.which(name)
    return {"name": name, "available": executable is not None, "executable": executable}


def _native_argv(name):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"{name} executable is not available on PATH")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # npm's published Codex shim has a predictable official JS entry point.
        # Do not interpret a batch file or invoke cmd.exe on model-owned text.
        entry = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if name == "codex" and node and entry.is_file():
            return [node, str(entry)]
        raise ValueError(f"{name} uses an unsupported command wrapper; install its native executable")
    return [executable]


def _money(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _loads(text):
    def invalid_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("JSON number exceeds finite range")
        return parsed
    return json.loads(text, parse_constant=invalid_constant, parse_float=finite_float)


def _events(text):
    events = []
    for line in text.splitlines():
        if line.strip():
            event = _loads(line)
            if not isinstance(event, dict):
                raise ValueError("Host event is not a JSON object")
            events.append(event)
    return events


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
            raise ValueError("Codex reported a failed turn")
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
        raise ValueError(f"Claude did not complete successfully ({final.get('subtype', 'unknown')})")
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
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            model = request.model or self.config.get("model")
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
                else:
                    tools = "Read,Glob,Grep,Edit,Write,Bash" if request.phase == "implement" else "Read,Glob,Grep"
                    argv += ["-p", "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--json-schema",
                             json.dumps(request.schema), "--permission-mode", "dontAsk", "--tools", tools,
                             "--allowedTools", tools, "--disallowedTools", "mcp__*"]
                    if model:
                        argv += ["--model", model]
            process = run_process(argv, cwd=request.cwd, input_text=input_text, output_dir=output_dir,
                                  timeout_seconds=request.timeout_seconds, should_stop=should_stop)
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
                return result
            if self.name == "codex":
                values = _parse_codex(process.stdout, final_path)
            elif self.name == "claude":
                values = _parse_claude(process.stdout)
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
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return DriverResult(returncode=-1, error=f"Invalid driver response or configuration: {exc}")


def get_driver(config: dict):
    config = dict(config)
    name = config.get("name", "codex")
    if name not in {"codex", "claude", "command"}:
        raise ValueError("driver name must be codex, claude, or command")
    if name == "command":
        argv = config.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError("command driver requires a nonempty argv array")
        if Path(argv[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("command driver requires an executable, not a shell script wrapper")
    return _Driver(config)
