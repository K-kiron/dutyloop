# Mission configuration reference

DutyLoop accepts a version-1 JSON object. Unknown fields are rejected, including
unsupported monetary budget fields. Inspect normalization without starting work:

```console
dutyloop validate --spec mission.json --state /path/outside/project
```

The standing starter is [examples/mission.json](../examples/mission.json).
For a finite result, use [goal-mission.json](../examples/goal-mission.json) or
[desktop-goal.json](../examples/desktop-goal.json).

## Required fields

| Field | Type | Meaning |
| --- | --- | --- |
| `mission` | Nonempty string, at most 20,000 characters | The responsibility or finite objective and how to judge useful work. |
| `project` | String | Exact root of an existing Git repository with a committed `HEAD`. Prefer an absolute path. |
| `allowed_paths` | Nonempty array of strings | Repository-relative file/directory prefixes whose changes may be accepted. |
| `verification` | Nonempty array of argv arrays | Operator-supplied commands run for each materialized staged snapshot, in order. They must preserve tracked content and the staged tree. Every argv element must be a nonempty string. |
| `limits.max_invocations` | Integer, at least 1 | Total reserved planning, discovery/investigation, implementation, and review calls. |
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
| `mode` | `"standing"` | `"standing"` continues discovery; `"goal"` seeks an explicitly accepted finite result. |
| `goal_acceptance` | None in standing mode | Required nonempty string array for goal mode. Criteria are trimmed, nonempty, and unique after trimming. |
| `max_plan_revisions` | `2` in goal mode | Goal-only positive integer, maximum `5`; total plan revision cap, including the initial plan. |
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

## Finite-goal acceptance

Set `mode` to `"goal"` and supply observable `goal_acceptance` criteria and
operator-owned `verification` commands. The operator defines the actual tests
and requirements; DutyLoop does not derive an independent oracle from the
mission text. Meaningful `goal_acceptance`, `max_plan_revisions`, or
`team.max_workers` values are rejected in standing mode. Normalized standing
specs contain empty criteria and null goal-only caps.

The planner records durable tasks, dependencies, evidence, and blocked reasons.
It may request zero to three scoped read-only investigations, within
`team.max_workers`; eligible investigations may run concurrently. Implementation,
checks, separate review, and integration are serial. Dependency completion gates
task selection. An unresolved business choice blocks that task and its
dependents while unrelated ready tasks continue; a whole-plan decision blocker
stops all task execution. The planner does not guess missing decisions.

Each task review also assesses every whole-goal criterion against the checked
tree. Completion requires operator verification and evidence supporting all
criteria; an accepted task can therefore complete the goal without another
final model call. If the initial plan has no tasks because the goal already
holds, DutyLoop still runs the operator commands and obtains fresh review.
Empty plans, no eligible task, and a worker's completion declaration do not
establish whole-goal acceptance.

Failed whole-goal assessment or an explicit whole-plan operator decision can
request replanning, bounded by `max_plan_revisions`; ordinary task execution
does not continuously rediscover new work. Every plan, investigation, implementation, retry, and review consumes
the same persistent invocation/time budget. A small valid budget may reach
`limited` before goal completion. Unknown work retains its charge and quarantine;
acknowledged recovery does not renew limits, clear a stop, or accept uncertain work.
An invalid plan proposal may receive one format/validation correction charged to
the same invocation budget and revision; it is not saved or executed before
validation, and a second invalid proposal blocks rather than replanning indefinitely.

### Generate a goal configuration

The configurator writes a validated new spec without starting work or
overwriting an existing file. Keep its output outside the target project:

```console
dutyloop configure --project /absolute/path/to/project --output ../csv-goal.json --mission "Complete documented CSV round-trip coverage." --allow-path src --allow-path tests --verify-json '["python","-B","-m","unittest","discover","-s","tests","-v"]' --mode goal --acceptance "Empty fields and embedded commas round trip as documented." --acceptance "Regression tests pass without public API changes." --max-workers 3 --max-plan-revisions 2
```

Use `--driver desktop` for the current desktop host. `--mode` accepts
`standing` or `goal` and defaults to `standing`; repeat `--acceptance` for each
goal criterion. `--max-workers` accepts `1`–`3`, and `--max-plan-revisions`
accepts `1`–`5`. Meaningful goal-only arguments require `--mode goal`. Replace
the sample checks and criteria with the actual project contract, then validate
and initialize the generated spec as usual.

### Record an operator decision

For `goal_requires_operator_decision`, inspect status and the retained plan.
Select the exact blocked task key and provide your own decision:

```console
dutyloop status --state ../mission-state --human
dutyloop resolve --state ../mission-state --key csv-format --decision "Preserve empty fields as documented."
dutyloop run --state ../mission-state
```

Resolution records the decision on that task in the same plan; its dependencies,
acceptance obligations, and all other tasks remain. It does not reset the
invocation count or deadline. If the whole plan is blocked, the reserved key
`--key @goal` records a whole-plan decision and requests a bounded replan.
`@goal` cannot be a candidate task slug; plain `goal` always refers to a task.
When the complete plan is blocked, resolve `@goal` first; task-specific decisions
are rejected until the resulting plan permits task work.
Inspect the returned status before starting: exhausted limits remain `limited`.

Only an eligible operator-decision blocker can be resolved. Completed and
stopped goals, unknown or unsettled work, rejected attempts, and security
blockers cannot be reopened through this command. The MCP equivalent is
`mission_resolve(name, key, decision)`; see the [desktop flow](desktop.md#finish-a-finite-goal).

## Driver and team

| Field | Default | Rules |
| --- | --- | --- |
| `driver.name` | `"codex"` | A supported native adapter, `"desktop"` for your current local coding conversation, or `"command"` for a custom wrapper. See the [support matrix](limits-and-hosts.md). |
| `driver.model` | Not specified | Optional nonempty model identifier when the selected native adapter supports it. Forbidden for desktop, which uses the current host's model. No global host configuration is changed. |
| `driver.argv` | Not specified | Required nonempty argv array for `"command"`; forbidden for other drivers. |
| `team.discovery_workers` | `1` | Integer from 1 to 3; standing discovery concurrency. Existing team specs remain valid. |
| `team.max_workers` | `3` in goal mode | Goal-only integer from 1 to 3; ceiling for planner-selected read-only investigation concurrency. It does not parallelize implementation. |

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

For standing mode, use one discovery worker initially. Each worker consumes an invocation, including
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
| `poll_seconds` | `60` | Finite number at least 0.05; standing-mode waiting interval before another discovery cycle. |
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
