"""Pinned OpenCode 2 and DeepSeek Harness headless protocols.

OpenCode 2.2.5: anomalyco/opencode@a316ae64c397cff9ad110e6b9277d5dba5002a2e.
DSH 0.2.2-rc.1: deepseek-ai/deepseek-harness@fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159.
These are offline protocol contracts, not claims of authenticated live validation.
"""

import json
import re

from .protocol import HostFailure, events, failure_kind, loads


SUPPORTED_VERSIONS = {"opencode": ("2.2.5",), "dsh": ("0.2.2-rc.1",)}


def supported_version(name, text):
    # Do not guess across major versions, suffixes, or multiple version lines.
    value = text.strip()
    match = re.fullmatch(r"(?:(?:opencode|dsh)\s+)?v?([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?)", value)
    if not match or match.group(1) not in SUPPORTED_VERSIONS[name]:
        allowed = ", ".join(SUPPORTED_VERSIONS[name])
        raise HostFailure(f"Unsupported {name} version; this adapter supports exactly {allowed}. "
                          "Use a supported installation or the custom command protocol.", "config")
    return match.group(1)


def structured_prompt(prompt, schema):
    return (prompt + "\n\nReturn your final answer as one JSON object matching this JSON Schema. "
            "Return no Markdown fences, commentary, or additional objects around that final JSON.\n" +
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")))


def opencode_launch(request, model):
    # V2 renamed bash to shell and moved permission to an ordered permissions
    # array. Never feed it the V1 OPENCODE_PERMISSION shortcut. The private
    # standalone server is a descendant owned by the process lease.
    actions = ["read", "glob", "grep"]
    if request.phase == "implement":
        actions += ["edit", "shell"]
    rules = [{"action": "*", "resource": "*", "effect": "deny"}]
    rules += [{"action": action, "resource": "*", "effect": "allow"} for action in actions]
    policies = [{"action": "tool.use", "resource": "*", "effect": "deny"}]
    policies += [{"action": "tool.use", "resource": action + ":*", "effect": "allow"}
                 for action in actions]
    policies += [{"action": action, "resource": "*", "effect": "deny"}
                 for action in ("mcp.use", "skill.use")]
    config = {
        "update": "disable", "share": "disabled", "permissions": rules,
        "policies": policies, "agents": {"dutyloop": {
            "mode": "primary", "description": "Scoped project mission worker",
            "permissions": rules,
        }},
    }
    argv = ["run", "--standalone", "--format", "json", "--agent", "dutyloop"]
    if model:
        argv += ["--model", model]
    return argv, structured_prompt(request.prompt, request.schema), {
        "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
        # This inherited legacy variable must not select a different policy.
        "OPENCODE_PERMISSION": "",
        # A private server otherwise runs the existing host schedule table and
        # can start unrelated scheduled prompts. Environment overrides win over
        # all config layers in the pinned V2 flag resolver.
        "OPENCODE_FLAGS": "-schedules,-background_compaction",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    }


def dsh_launch(request, model):
    if model:
        raise HostFailure("DSH uses the model in its existing headless profile; omit driver.model.", "config")
    mode = "workspace-write" if request.phase == "implement" else "read-only"
    return ["--profile", "headless", "--json", "--permission-mode", mode,
            "--approval-policy", "never", "-"], structured_prompt(request.prompt, request.schema), {}


def parse_opencode(text):
    stream = events(text)
    session = None
    messages = {}
    finishes = []
    for event in stream:
        if event.get("type") == "error":
            error = event.get("error")
            code = error.get("type") or error.get("name") if isinstance(error, dict) else None
            raise HostFailure("OpenCode reported a failed run; inspect the local invocation logs.", failure_kind(code))
        if event.get("type") not in {"step_start", "step_finish", "text", "reasoning", "tool_use"}:
            raise ValueError("OpenCode emitted an unknown event type")
        current = event.get("sessionID")
        if not isinstance(current, str) or not current:
            raise ValueError("OpenCode event omitted session identity")
        if session is not None and current != session:
            raise ValueError("OpenCode mixed multiple sessions in one invocation")
        session = current
        part = event.get("part")
        if not isinstance(part, dict):
            raise ValueError("OpenCode event omitted its part")
        if part.get("sessionID") != session:
            raise ValueError("OpenCode part session identity does not match its event")
        message = part.get("messageID")
        if not isinstance(message, str) or not message:
            raise ValueError("OpenCode part omitted message identity")
        if event["type"] == "text":
            if not isinstance(part.get("text"), str):
                raise ValueError("OpenCode text part is not text")
            messages.setdefault(message, []).append(part["text"])
        elif event["type"] == "step_finish":
            finishes.append(part)
    if not finishes or finishes[-1].get("reason") != "stop" or stream[-1].get("type") != "step_finish":
        raise ValueError("OpenCode emitted no final completed stop step")
    final = finishes[-1]
    final_text = "".join(messages.get(final["messageID"], []))
    if not final_text:
        raise ValueError("OpenCode final completed step omitted structured text")
    # Reconciliation can synthesize cost=0 when the host has no cost sample.
    # Preserve per-step tokens without claiming that these cover every retry.
    samples = [part["tokens"] for part in finishes if isinstance(part.get("tokens"), dict)]
    usage = {"step_samples": samples, "scope": "reported_steps"} if samples else {}
    return loads(final_text), usage, None, session


def parse_dsh(text):
    stream = events(text)
    if not stream or stream[0].get("type") != "session":
        raise ValueError("DSH emitted no opening session event")
    session = stream[0].get("sessionId")
    if not isinstance(session, str) or not session or stream[0].get("truncated") is True:
        raise ValueError("DSH emitted an invalid or truncated session identity")
    final = None
    samples = []
    missing_usage = False
    for event in stream[1:]:
        kind = event.get("type")
        if final is not None:
            raise ValueError("DSH emitted records after its terminal final event")
        if kind == "error":
            raise HostFailure("DSH reported a failed run; inspect the local invocation logs.",
                              failure_kind(event.get("code")))
        if kind == "final":
            final = event
        elif kind == "status":
            if event.get("phase") == "step_end":
                sample = event.get("usage")
                if event.get("truncated") is True or not isinstance(sample, dict):
                    missing_usage = True
                else:
                    samples.append(sample)
        elif kind not in {"text", "thinking", "tool_call", "tool_result"}:
            raise ValueError("DSH emitted an unknown event type")
    if final is None or final.get("truncated") is True or final.get("sessionId") != session:
        raise ValueError("DSH emitted no matching complete final event")
    reason = final.get("reason")
    if not isinstance(reason, dict) or reason.get("kind") != "completed":
        error = reason.get("error") if isinstance(reason, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise HostFailure("DSH did not report a completed turn; inspect the local invocation logs.", failure_kind(code))
    if not isinstance(final.get("text"), str):
        raise ValueError("DSH final event omitted structured text")
    usage = {"step_samples": samples, "complete": not missing_usage,
             "scope": "parent_session_only"} if samples else {}
    return loads(final["text"]), usage, None, session
