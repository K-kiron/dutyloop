# Driver protocol

DutyLoop supplies native adapters for Codex CLI, Claude Code CLI, OpenCode 2.2.5,
and DeepSeek Harness 0.2.2-rc.1. Other hosts need an operator-provided command
wrapper. Implementing this protocol is not evidence that the underlying host
has completed an authenticated mission.

## Native adapters

The executable must already be installed, available on `PATH`, and authenticated
using the host's own supported setup. DutyLoop uses that existing state. It does
not log in, install runtimes, select a paid account, or write global host config.

Native responses must contain final structured output matching the requested
schema. A zero exit status, progress text, or a plausible completion message is
not enough. Missing final output, invalid JSON, schema violations, failed host
results, cancellation, and timeout are failures.

Codex usage is taken from its completed-turn events. Claude usage and cost are
taken from its successful final result when supplied. Missing usage is `{}`;
missing cost is `null`, never a fabricated zero. Cost is not a hard budget.

OpenCode and DSH return reported parent-session step usage when available; it
does not necessarily include retries, internal subagents, or every provider
request. Their adapters leave cost unknown. In particular, OpenCode may synthesize
a zero when no cost sample exists, which is not evidence of a free run.

On Windows, the adapter can resolve the official installed npm Codex shim to
its Node entry point, as well as the supported OpenCode and DSH npm layouts.
Arbitrary `.cmd`, `.bat`, and `.ps1` wrappers are not
executed through a shell. An unsupported wrapper must be replaced with a
supported executable or a custom executable wrapper.

### Pinned OpenCode and DeepSeek Harness protocols

These adapters first invoke `--version` inside the managed process boundary and
reject an unrecognized version before model work. They ask for a JSON final
answer in the prompt, then validate it locally against the phase schema. They
do not claim provider-enforced structured output.

| Contract | OpenCode 2.2.5 | DeepSeek Harness 0.2.2-rc.1 |
| --- | --- | --- |
| Launch | `run --standalone --format json --agent dutyloop` | `--profile headless --json --permission-mode <phase mode> --approval-policy never -` |
| Phase tools/mode | `read`, `glob`, `grep`; implementation also `edit`, `shell` | `read-only`; implementation `workspace-write` |
| Final success | Matching session/message identity and completed `step_finish` with `reason: stop`, carrying final JSON text | Opening session plus matching untruncated terminal `final`, `reason.kind: completed`, carrying final JSON text |
| Model | Optional per-call `driver.model` | Existing headless profile; nonempty `driver.model` rejected |
| Session handling | New standalone invocation; host session records can persist | New headless invocation; no conversation resume |

OpenCode uses per-child configuration and policy rules, disables MCP/skill tool
access and existing schedules/background compaction, suppresses auto-update for
the version probe/run, and never opts into `--auto`.
The standalone child still uses the installed host's trusted provider/config
environment. This is not an OS sandbox. DSH does not receive an extra grant to
shared Git metadata; its permission enforcement remains the host's behavior.

Source contracts were inspected at fixed revisions:

- OpenCode [`a316ae64c397cff9ad110e6b9277d5dba5002a2e`](https://github.com/anomalyco/opencode/tree/a316ae64c397cff9ad110e6b9277d5dba5002a2e):
  [standalone server selection](https://github.com/anomalyco/opencode/blob/a316ae64c397cff9ad110e6b9277d5dba5002a2e/packages/cli/src/services/server-connection.ts),
  [noninteractive event output](https://github.com/anomalyco/opencode/blob/a316ae64c397cff9ad110e6b9277d5dba5002a2e/packages/cli/src/run/noninteractive.ts),
  [permission rules](https://github.com/anomalyco/opencode/blob/a316ae64c397cff9ad110e6b9277d5dba5002a2e/packages/schema/src/permission.ts),
  and [environment flags](https://github.com/anomalyco/opencode/blob/a316ae64c397cff9ad110e6b9277d5dba5002a2e/packages/core/src/flag.ts).
- DeepSeek Harness [`fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`](https://github.com/deepseek-ai/deepseek-harness/tree/fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159):
  [CLI help](https://github.com/deepseek-ai/deepseek-harness/blob/fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159/docs/cli-help.md),
  [headless setup](https://github.com/deepseek-ai/deepseek-harness/blob/fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159/packages/bundle/headless/README.md),
  and [JSON stream](https://github.com/deepseek-ai/deepseek-harness/blob/fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159/packages/bundle/headless/src/json-stream.ts).

Offline fixtures validate launch, parsing, error handling, and cancellation at
the adapter boundary. They are not authenticated host executions. Other
versions need new source inspection and tests rather than a relaxed version
check based on similar branding.

## Custom command configuration

For local CLI-created missions, set:

```json
{
  "driver": {
    "name": "command",
    "argv": ["/absolute/path/to/python", "/absolute/path/to/your_wrapper.py"]
  }
}
```

This is only the driver fragment of a full mission spec. The supervisor passes
argv directly without `shell=True`. Arguments are operator configuration, not
model-generated shell text. MCP mission creation deliberately permits only
native driver names.

The process runs in the requested worktree and receives one UTF-8 JSON object
on stdin, followed by a newline:

```json
{
  "phase": "discover",
  "prompt": "The complete mission and phase-specific request...",
  "cwd": "/absolute/path/to/managed/worktree",
  "schema": {"type": "object", "properties": {}},
  "model": null
}
```

`phase` is `discover`, `implement`, or `review`. The example schema is abbreviated;
the actual request supplies the full closed response schema. The wrapper must
honor that schema and the requested phase. The supervisor applies process
deadlines and stop handling externally.

Write exactly one JSON object to stdout and put diagnostics on stderr:

```json
{
  "payload": {
    "status": "wait",
    "summary": "No actionable work found in this inspection.",
    "candidates": []
  },
  "usage": {},
  "cost_usd": null
}
```

`payload` is required and must be an object. `usage` and `cost_usd` are optional.
A nonzero exit status fails the invocation. Extra text on stdout is a protocol
error; captured output is bounded. The wrapper is responsible for launching its
host without interactive prompts and mapping its actual final result into the
response, rather than interpreting progress output as success.

## Phase payloads

Discovery:

```json
{
  "status": "work",
  "summary": "A documented behavior fails in the current implementation.",
  "candidates": [{
    "key": "normalize-label-underscores",
    "title": "Normalize underscores in label slugs",
    "kind": "bugfix",
    "why": "Underscores and spaces should produce the documented separator.",
    "priority": 1,
    "evidence": [{
      "source": "labels.py",
      "observation": "slugify('Hello_World') returns 'hello_world'; expected 'hello-world'."
    }],
    "acceptance": ["Hello_World becomes hello-world; existing label tests pass."]
  }]
}
```

Discovery status is `work`, `wait`, or `blocked`, with at most three candidates.
Kinds are `feature`, `bugfix`, `qa`, and `docs`; the mission may allow a subset.
Candidate keys use 2–80 lowercase letters, digits, underscores, or hyphens and
start with a letter or digit. Priority is an integer from 1 through 5, where 5
is highest. All shown payload properties are required; extra properties are
rejected. Evidence is a claim by the driver, not independently attested truth.

Implementation:

```json
{"summary": "Implemented the selected task and added its regression test.", "completed": true}
```

Review:

```json
{"accepted": true, "summary": "The selected acceptance criteria are met.", "findings": []}
```

The supervisor runs configured verification separately. Returning `completed`
or `accepted` does not bypass that verification or the changed-path check.
Review should inspect without changing files; see the host-specific boundaries
in [limits and host support](limits-and-hosts.md).
