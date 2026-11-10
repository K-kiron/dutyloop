# Mission configuration reference

DutyLoop accepts a version-1 JSON object. Unknown fields are rejected, including
unsupported monetary budget fields. Inspect normalization without starting work:

```console
dutyloop validate --spec mission.json --state /path/outside/project
```

The complete starter is [examples/mission.json](../examples/mission.json).

## Required fields

| Field | Type | Meaning |
| --- | --- | --- |
| `mission` | Nonempty string, at most 20,000 characters | The continuing responsibility and how to judge useful work. |
| `project` | String | Exact root of an existing Git repository with a committed `HEAD`. Prefer an absolute path. |
| `allowed_paths` | Nonempty array of strings | Repository-relative file/directory prefixes whose changes may be accepted. |
| `verification` | Nonempty array of argv arrays | Operator-supplied commands run for each materialized staged snapshot, in order. They must preserve tracked content and the staged tree. Every argv element must be a nonempty string. |
| `limits.max_invocations` | Integer, at least 1 | Total reserved discovery, implementation, and review calls. |
| `limits.max_wall_seconds` | Finite number, at least 0.1 | Elapsed window from first run, including waiting and subsequent downtime. |

An argv array is a direct process invocation, not a shell expression:

```json
"verification": [
  ["python", "-m", "pytest", "-q"],
  ["python", "/opt/project-checks/check_public_contract.py"]
]
```

This is a JSON fragment. The executable and its scripts must work from a fresh
task worktree. Shell operators such as `&&`, pipes, and redirection do not work
as separate argv entries. If a shell is necessary, explicitly choosing one makes
its entire script trusted operator configuration. On Windows, use an executable
or supported native entry point rather than relying on an arbitrary batch shim.

## Scope and decision policy

| Field | Default | Rules |
| --- | --- | --- |
| `version` | `1` | Only version 1 is supported. |
| `constraints` | `[]` | Array of behavioral instructions. These do not create an OS access policy. |
| `kinds` | `["bugfix", "docs", "feature", "qa"]` | Nonempty subset of these four work kinds. |
| `research_domains` | `[]` | Hostnames allowed in HTTPS evidence receipts; exact host matches, without scheme or path. Not a network firewall. |
| `max_changed_files` | `20` | Positive integer; maximum changed paths per implementation attempt. |
| `branch` | Unique `work/mission-<id>` | A new functional Git branch name. An existing branch is not overwritten. |

An allowed path is an exact file or a directory prefix: `src` accepts `src/a.py`
but not `src-old/a.py`. `.` accepts any project path except prohibited Git
metadata paths. Absolute paths, `..`, `.git`, symlinks, junctions, and reparse
points are rejected where these are checked. **Allowed paths are acceptance
criteria, not a sandbox for the child process.**

File evidence must reference an existing project file. The supervisor records
its hash; the hash establishes which bytes were referenced, not whether the
agent's observation is true. External evidence needs an HTTPS URL on an allowed
hostname. DutyLoop does not fetch and independently verify that page merely
because a URL was returned.

## Driver and team

| Field | Default | Rules |
| --- | --- | --- |
| `driver.name` | `"codex"` | A supported native adapter, `"desktop"` for your current local coding conversation, or `"command"` for a custom wrapper. See the [support matrix](limits-and-hosts.md). |
| `driver.model` | Not specified | Optional nonempty model identifier when the selected native adapter supports it. Forbidden for desktop, which uses the current host's model. No global host configuration is changed. |
| `driver.argv` | Not specified | Required nonempty argv array for `"command"`; forbidden for other drivers. |
| `team.discovery_workers` | `1` | Integer from 1 to 3. Discovery may run in parallel; implementation and acceptance stay serial. |

Native names are `codex`, `claude`, `opencode`, and `dsh`. OpenCode is pinned to
2.2.5 and DeepSeek Harness to 0.2.2-rc.1; other versions fail before a model turn.
DSH uses its existing headless profile and rejects a nonempty `driver.model`.
These pinned adapters have source/offline protocol coverage, not a recorded
authenticated mission. [Adapter details](adapter-protocol.md) explain the contract.

Desktop configuration is exactly `{"name": "desktop"}` with no other fields.
Use `configure --driver desktop` for a starter mission spec, or copy a
[desktop example](desktop.md). It does not select a model or launch another
model CLI. The host must claim and perform each phase through local MCP; running
the supervisor alone leaves it waiting for the host. Desktop phase handoffs
consume invocation reservations; model usage and cost remain unknown.

Use one discovery worker initially. Each worker consumes an invocation, including
when it returns no useful candidate. A larger discovery team is not an
implementation team, and no effectiveness gain has been established at equal
budget. A host may create subagents internally; the outer invocation budget does
not separately count those activities.

Custom command drivers are available through CLI mission initialization. MCP
creation/start accepts native adapters and desktop, excluding custom commands.
MCP mission creation defaults to Codex; MCP issue creation defaults to desktop.
Set `driver` explicitly in reusable specs. See [MCP control](mcp.md) and
[the wrapper protocol](adapter-protocol.md).

## Retry and timing controls

| Field | Default | Rules |
| --- | --- | --- |
| `poll_seconds` | `60` | Finite number at least 0.05; waiting interval before another discovery cycle. |
| `call_timeout_seconds` | `300` | Finite number at least 0.1; each driver call is also bounded by the mission's remaining wall time. |
| `max_task_attempts` | `2` | Positive integer; attempts for one selected task, with recorded feedback. |
| `max_failures` | `3` | Positive integer; repeated failed cycles/tasks block the mission. Accepted work resets the failure count. |

Each operator verification command is also bounded by `call_timeout_seconds`
and the remaining mission wall time. Unknown, authentication, quota,
configuration, and protocol failures block promptly rather than asking an
implementer to repair host access. Explicit transient errors and clean deadlines
can use the bounded retry policy for managed calls. An expired claimed desktop
request has unknown external cleanup and requires inspection and explicit
recovery; it is not automatically reassigned.

Budget exhaustion can interrupt a task before implementation, checking, or review
finishes. DutyLoop leaves room for an implementation and its review before
starting a new attempt. One discovery + one implementation + one review needs at least three
driver invocations, before retries or another discovery. Operator checks use
time but are not driver invocations. Uncertain and failed calls remain charged.

There is no supported `max_cost_usd`, token hard cap, automatic budget extension,
or automatic restart after an explicit stop. Reported native-host costs are
telemetry and can be missing.

## Where the files live

- The mission spec is operator input. Editing that file after `init` does not
  update the stored mission.
- The state directory must be outside the target project and must not contain
  the project. It contains private prompts, logs, evidence, and worktrees.
- Initialization fixes the base at committed `HEAD`; uncommitted files and local
  ignored assets are not copied into worktrees.
- Accepted changes advance only the mission branch. Git metadata is shared with
  the original repository even though task working files are separate.

To change responsibility, limits, or driver after initialization, inspect the
existing mission and explicitly initialize a new one with a new state directory
and branch. Do not edit its SQLite records to bypass controls.
