# DutyLoop

An experimental, local supervisor for an ongoing project responsibility.

Give it a mission, a repository, permitted paths, your verification commands,
and invocation and wall-time limits. It asks a coding agent to inspect the
current project, propose useful work, implement one selected task, and review
the result. After an accepted change, it inspects the project again. Completing
one task or an initial list does not complete the mission.

DutyLoop is a small implementation of an existing pattern. It does not claim
to invent autonomous project work or outperform the alternatives below.

## Try the deterministic demo first

Requires Python 3.11+, Git, and an existing Git author identity. No model account
or API key is needed for this demo.

Install from the source repository:

```console
git clone https://github.com/K-kiron/dutyloop.git
cd dutyloop
python -m pip install -e .
dutyloop demo --directory ../dutyloop-demo
```

Use a new or empty demo directory. The demo creates a tiny repository with two
independent defects. Its **scripted fake driver** discovers defects by inspecting
the current files, adds fixes and regression tests, and uses the **real engine,
Git worktrees, and independent unittest commands**. After two accepted tasks,
another discovery returns `wait`; the seven-invocation limit stops the mission.
The original checkout is checked for changes, and the accepted branch is tested
again in a separate worktree.

The result includes `simulated: true`, the engine snapshot, and the verification
outcome. This demonstrates control flow and integration. It does **not** measure
LLM judgment, usefulness, or reliability on another project.

## Run with an existing coding CLI

Install and sign in to your chosen CLI using that CLI's own supported setup.
DutyLoop invokes the existing executable and its existing login. It does not
install coding agents, create credentials, or change global agent settings.

1. Copy [the example mission](examples/mission.json) to your working location.
2. Set `project` to your repository's absolute path. Choose allowed paths,
   constraints, and verification commands for that project.
3. Choose `codex` or `claude` under `driver.name`.
4. Keep mission state **outside the target repository**, then initialize and run:

```console
dutyloop doctor --driver codex
dutyloop init --spec examples/mission.json --state ../my-mission-state
dutyloop run --state ../my-mission-state
```

`doctor` checks executable availability only. It does not verify authentication,
model access, quota, or a successful live invocation.

For a detached local run:

```console
dutyloop start --state ../my-mission-state
dutyloop status --state ../my-mission-state
dutyloop stop --state ../my-mission-state
```

`start` launches a background Python process and stores its stdout/stderr in the
state directory. Keep that directory private: prompts, project details, child
logs, and generated artifacts may contain sensitive information.

## What the supervisor does

- Reserves each discovery, implementation, or review invocation before dispatch.
- Rechecks current project evidence and records candidate reasons and acceptance
  criteria. Discovery can use one to three workers; implementation stays serial.
- Runs changes in managed detached Git worktrees. Accepted commits advance the
  mission branch. The supervisor does not switch or modify the original checkout;
  child tools retain the host and shared-Git access described below.
- Runs operator-supplied verification commands outside the model's judgment,
  then requests a separate review invocation.
- Checks changed paths before accepting work. Rejected attempts remain available
  for inspection rather than being silently presented as completed work.
- Continues after acceptance or waits and inspects again until stopped, limited,
  blocked, or failed.

There is no automatic push, pull-request creation, merge into your current
branch, or deployment. Inspect the mission branch and evidence before integrating
its changes yourself.

## Limits and permissions

**No USD hard cap is supported.** Monetary limit fields are rejected instead of
being accepted as an unenforceable promise. Native CLI usage or cost may be
unknown. Reported cost is telemetry, not a billing guarantee.

`max_invocations` limits driver calls, including discovery and review; it does
not limit the model requests or subagents a coding CLI may make internally.
`max_wall_seconds` is elapsed time from the mission's first run, including waiting
and subsequent downtime. The supervisor controls dispatch, deadlines, stop requests, and
child process cleanup. It cannot undo effects already performed by a child or
promise that a provider will stop billing at an exact instant.

**Allowed paths are an acceptance rule, not an OS security boundary.** Worktrees
isolate Git work. They do not isolate the filesystem, network, credentials, Git
metadata, or tools available to the child. In particular, Claude implementation
uses shell tools under the operator's account. Use a separately restricted user,
container, or VM when that boundary is required.

Verification argv is trusted operator configuration and executes code. Review
project tests and commands before running them. Constraints and research-domain
instructions guide the agent; they do not create filesystem or network policy.
If an implementation can edit a test script, that script is not an independent
correctness oracle. Keep decisive acceptance checks outside writable paths when
possible, and assess actual user behavior before adopting the result.

See [limits and host support](docs/limits-and-hosts.md) for the exact boundaries
and [architecture](docs/architecture.md) for acceptance and recovery behavior.

## Host support

| Integration | Scope |
| --- | --- |
| Codex CLI | Native structured-output adapter; uses an already installed and authenticated CLI. |
| Claude Code CLI | Native structured-output adapter; uses an already installed and authenticated CLI. |
| Custom command | JSON stdin/stdout protocol for an operator-provided wrapper. |
| MCP client | Optional local control interface; the same mission engine runs underneath. |

Two native adapters plus a custom wrapper protocol do **not** mean native support
for every other coding host. The deterministic demo does not exercise live
authenticated Codex or Claude sessions. See [the adapter protocol](docs/adapter-protocol.md).
The [verification record](docs/validation.md) distinguishes offline tests, the
scripted demo, and the separately completed native Codex fixture check.

## Optional MCP control

```console
python -m pip install -e ".[mcp]"
dutyloop mcp --root ../mission-states --allow-project /absolute/path/to/repository
```

Configure your MCP client to launch this command with paths appropriate to your
machine. The tools are `mission_create`, `mission_start`, `mission_status`, and
`mission_stop`. Creation does not start work. Projects must match an explicitly
registered root, and mission creation through MCP accepts native drivers only.
The supplied verification commands authorize local command execution when a
mission runs. Treat MCP access to create/start as that execution authority.

## Existing alternatives

| Project | Existing capabilities and useful differences |
| --- | --- |
| [AUTOPILOT](https://github.com/M-A-S-T-E-R-M-I-N-D/AUTOPILOT) | A direct alternative with repository discovery, gates, budgets, fleets, and a dashboard. Its **auto-mode** can automatically admit agent proposals; it is not limited to a human-approved initial task list. |
| [OpenClaw](https://github.com/openclaw/openclaw) | Standing orders, automations, persistent goals, automatic continuation, and token-budget controls within a broader assistant gateway. |
| [Paperclip](https://github.com/paperclipai/paperclip) | Goals, organizational responsibilities, agent orchestration, heartbeat execution, budgets, and governance. |
| [Symphony](https://github.com/openai/symphony) | A long-running service that dispatches issue-tracker work into isolated coding workspaces. Discovery policy can be supplied above its runner. |

DutyLoop's experiment is a smaller, inspectable mission contract and supervisor,
with explicit evidence and limits. It has no comparative effectiveness results.
The [source-backed comparison](docs/alternatives.md) records the inspected
revisions and material budget limitations.

## Development

```console
python -m pip install -e ".[dev]"
python -m pytest
```

Tests use local fixtures and simulated drivers unless a test explicitly states
otherwise. They do not silently invoke paid models. The project is experimental;
state compatibility and unattended behavior need evaluation before operational use.

MIT licensed.
