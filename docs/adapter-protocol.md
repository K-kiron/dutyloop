# Driver protocol

DutyLoop supplies native adapters for Codex CLI and Claude Code CLI. Other hosts
need an operator-provided command wrapper. Implementing this protocol is not
evidence that the underlying host has been tested.

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

On Windows, the adapter can resolve the official installed npm Codex shim to
its Node entry point. Arbitrary `.cmd`, `.bat`, and `.ps1` wrappers are not
executed through a shell. An unsupported wrapper must be replaced with a
supported executable or a custom executable wrapper.

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
model-generated shell text. MCP mission creation deliberately permits only the
two native driver names.

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
Candidate keys are lowercase hyphenated slugs, and priority is an integer from
1 through 5. All shown payload properties are required; extra properties are
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
