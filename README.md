<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/hero-light.svg">
    <img src="docs/assets/hero-light.svg" alt="DutyLoop — a repository super-agent for your coding tools" width="1200">
  </picture>
</p>

<p align="center">
  <strong>A repository super-agent for your coding tools.</strong><br>
  Continuously plan, implement, verify, and follow up on repository work<br>
  with explicit budgets and human control.
</p>

<p align="center">
  <a href="https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml"><img src="https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11+"></a>
  <a href="#project-status"><img src="https://img.shields.io/badge/status-alpha-D7A34B" alt="Status: alpha"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3B8175" alt="License: MIT"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a><br>
  <a href="#quick-start">Quick start</a> ·
  <a href="#use-your-current-desktop-agent">Desktop agent</a> ·
  <a href="#run-a-project-mission">Run a mission</a> ·
  <a href="#process-github-issues">Process issues</a> ·
  <a href="#maintain-a-repository">Repository maintenance</a> ·
  <a href="docs/getting-started.md">Documentation</a> ·
  <a href="https://github.com/K-kiron/dutyloop/issues/new/choose">Report an issue</a>
</p>

---

DutyLoop is a **local, persistent super-agent for repository development**, built around your existing coding tools. Give it a repository, a standing responsibility, and acceptance commands. It inspects the project, plans work supported by evidence, and coordinates implementation, verification, and separate review within your configured scope and limits.

Accepted changes accumulate on a **separate local Git branch**, with task rationale, verification results, and review records. Discovery continues after each accepted task, until a limit, blocker, or your stop command ends the run.

Already have a backlog? [Issue mode](#process-github-issues) assesses the repository's current stage and captured open issues, builds a phased roadmap, then detail-plans and implements the next eligible item. Delivery defaults to local branches; you can explicitly enable PR creation, CI-gated merging, and verified issue closure.

For ongoing ownership, [repository mode](#maintain-a-repository) reconciles new
issues and keeps following PRs created by DutyLoop through CI and review. Begin in observation
mode, inspect the plans and attention list, then explicitly enable execution
within the same persistent budget.

## How it works

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/assets/workflow-mobile-dark.svg">
  <source media="(max-width: 600px) and (prefers-color-scheme: light)" srcset="docs/assets/workflow-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/workflow-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/workflow-light.svg">
  <img src="docs/assets/workflow-light.svg" alt="Discover evidenced work, implement in a worktree, run operator checks, request a separate review, then commit accepted work and discover again" width="1200">
</picture>

**A coordinated multi-agent workflow:** one to three discovery workers can inspect the project in parallel. Implementation, operator checks, and a separate review then run in sequence, one change at a time. After acceptance, discovery continues with the results preserved in durable state.

You define the responsibility and acceptance commands once. DutyLoop manages the worktrees, phase reservations, staged-snapshot checks, review, and durable state. Use your current local desktop coding agent through MCP, or select a native coding CLI adapter.

| Capability | What it gives you |
| --- | --- |
| **Ongoing discovery** | One to three workers inspect current evidence and propose tasks with acceptance criteria. |
| **Repository coordination** | Incremental issue intake, durable input receipts, owned-PR maintenance and a consolidated attention list. |
| **Focused delivery** | Implementation and acceptance stay serial, with managed worktrees for each attempt. |
| **Checks you control** | Your commands run against materialized staged files; changes during verification prevent acceptance. |
| **Inspectable results** | Accepted commits, verified tree IDs, task feedback, and retained rejected or interrupted attempts. |
| **Persistent control** | SQLite state, audit events, background execution, explicit stop, and acknowledged recovery. |
| **Bounded execution** | Invocation and elapsed-time limits, with room reserved for implementation and its mandatory review. |

Standing missions produce local branches without pushing, opening PRs, merging, or deploying. Issue mode also defaults to local output and offers separately configured GitHub delivery.

## Quick start

**Requirements:** Python 3.11+, Git, and a configured Git author identity. Use a virtual environment if possible; [setup instructions](docs/getting-started.md#prerequisites) cover Windows, macOS, and Linux. Installation currently uses the source repository.

```console
git clone https://github.com/K-kiron/dutyloop.git
cd dutyloop
python -m pip install -e .
dutyloop demo --directory ../dutyloop-demo
```

Use a new or empty demo directory. No model account or API key is needed. The **scripted demo** exercises the real supervisor, Git worktrees, and test commands: two fixture fixes are accepted, discovery continues, and the run stops at seven invocations. The driver and its judgments are simulated.

The returned JSON includes:

```json
{
  "simulated": true,
  "accepted_tasks": 2,
  "original_unchanged": true,
  "accepted_tests_passed": true
}
```

This is an excerpt of the demo result. Full output also contains the mission snapshot and artifact locations. See the [verification record](docs/validation.md) for the distinction between offline checks and authenticated host runs.

## Use your current desktop agent

Choose `driver: {"name": "desktop"}` to let the current local Codex or Claude Code desktop conversation do the work. DutyLoop supplies phase prompts through MCP and retains verification, commits, and configured issue delivery. It does not select a model or start another model CLI login.

```console
python -m pip install -e ".[mcp]"
python -m dutyloop desktop-config --client codex --root /absolute/path/to/dutyloop-states --allow-project /absolute/path/to/project
```

Use `--client claude` for Claude's JSON configuration. The command prints configuration; merge it into your host's local MCP settings. It does not write host settings or start work. Copy [the desktop issue spec](examples/desktop-issues.json) or [mission spec](examples/desktop-mission.json) outside the target repository, replace its paths and checks, then follow the [desktop handoff guide](docs/desktop.md) and its ready-to-paste host prompt.

The host claims each phase, executes it in DutyLoop's exact managed checkout, and submits schema-checked results. Review needs a fresh subagent or context. DutyLoop cannot stop desktop-owned shells or subagents; finish or stop them before submitting or cancelling. Actual Codex/Claude desktop execution has not yet been verified. Connecting MCP alone does not wake an idle conversation.

## Run a project mission

A useful mission names a responsibility and the behavior to preserve:

> Maintain CSV import/export reliability. Fix reproducible failures against
> documented behavior with regression coverage. Preserve the public API and
> file format. Wait when there is no evidenced work.

For native CLI execution, install and authenticate a [supported coding CLI](#host-adapters) using its own setup. Choose a repository with a committed `HEAD` and checks that work from a fresh worktree. Save this as `mission.json` **outside the target repository**:

```json
{
  "mission": "Maintain CSV import/export reliability. Fix evidenced failures with regression tests. Preserve the public API and file format. Wait when there is no supported work.",
  "project": "/absolute/path/to/project",
  "allowed_paths": ["src", "tests"],
  "verification": [
    ["python", "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
  ],
  "driver": {"name": "codex"},
  "limits": {"max_invocations": 12, "max_wall_seconds": 1800}
}
```

Replace the project path, allowed paths, and check command for your project. Windows paths can use forward slashes, such as `C:/work/my-project`. The [full example](examples/mission.json) adds constraints, work kinds, and retry settings; the [configuration reference](docs/configuration.md) covers every field.

Check the setup, initialize, and run:

```console
dutyloop doctor --driver codex --project /absolute/path/to/project
dutyloop validate --spec mission.json --state ../mission-state
dutyloop init --spec mission.json --state ../mission-state
dutyloop run --state ../mission-state
```

`doctor` checks installation and version, without authenticating or calling a model. `init` creates the mission branch and persisted state from committed `HEAD`; uncommitted edits are not copied into the mission. Keep state outside the target repository, and make dependencies available to its fresh worktrees.

On Windows, DutyLoop passes `-c core.longpaths=true` to its managed Git commands without changing repository or global Git configuration. Very deep Git metadata paths may still require a shorter state root. See [checkout failures and recovery](docs/operations.md#windows-checkout-paths).

### Monitor and control

Use `start` **instead of `run`** to launch a detached process:

```console
dutyloop start --state ../mission-state
dutyloop status --state ../mission-state --human
dutyloop events --state ../mission-state --limit 20
dutyloop stop --state ../mission-state
```

Status shows the mission branch, accepted commit, call usage, and task outcomes. Stopping is asynchronous; check status again to confirm completion. A latched stop cannot be cleared by restarting. [Operations and troubleshooting](docs/operations.md) cover blockers, limits, and interrupted work.

## Process GitHub issues

Use an existing codebase and authenticated GitHub CLI (`gh`). The [desktop route](docs/desktop.md) uses your current coding conversation through MCP. For native CLI execution, authenticate your coding CLI, copy [the issue configuration](examples/issues.json) outside the target repository, set the project, repository, allowed paths, and checks, and keep `github.delivery` set to `local` for local results:

```console
dutyloop issues init --spec issues.json --state ../issue-state
dutyloop issues run --state ../issue-state --plan-only
dutyloop issues status --state ../issue-state --human
dutyloop issues run --state ../issue-state
```

Initialization captures the current open issues and their comments without model calls or GitHub writes. `--plan-only` assesses the whole captured backlog and produces a global phased roadmap, without detailed per-issue plans. Put the current milestone or release objective in `queue.planning_context`; `queue.planning_max_bytes` bounds each request. Large backlogs use complete bounded assessment chunks with up to `queue.planning_workers` concurrent workers (default `3`, range `1`–`8`; `1` is serial). A backlog fitting one prompt still uses one roadmap call. All workers inspect the same base and share the existing budget; synthesis requires complete coverage and implementation stays serial. Inspect worker progress, coverage, stage goals, entry and exit criteria, human gates, questions and deferrals in status.

Execution requires a complete current roadmap, follows stage order and dependencies, and detail-plans only the next eligible issue on the current base before implementation, operator checks and separate review. An incomplete earlier stage blocks later stages. `queue.batch_size` bounds each run; assessment, planning, retries and execution share persistent call and time limits.

Runtime questions and failed attempts keep the assessment cached while live issue states and stage barriers gate execution, preserving calls for eligible fixes. Changed chunks are reassessed when sources or operator intent change on the same base and planning authority. At a phase boundary, complete historical investigations can survive a chain of this batch's verified changes with unchanged inputs; a fresh roadmap still inspects the current code, and the next issue gets a current-base detailed plan. External base changes or changed planning authority require fresh investigation. See the [planning reuse rules](docs/issues.md#prepare-and-inspect-plans).

A proven checkout preparation failure before dispatch can be [explicitly recovered in the same batch](docs/issues.md#workspace-preparation-failures). Inspect retained work, use `dutyloop issues recover --state ../issue-state --acknowledge`, then inspect status before `dutyloop issues start` or `dutyloop issues run` with that same state. Valid completed assessments and operator decisions remain recorded; recovery does not renew the budget or deadline, clear a stop, or restart arbitrary blocked work.

Set `github.delivery` to `pull_request` to publish PRs, or `merge` with explicit `required_checks` to wait for CI and merge. With `close_issues: true`, merge mode checks the merged tree and reruns your checks before closing an unchanged issue. Running or starting a batch authorizes its configured delivery operations. Uncertain effects require inspection, and a completed pass can still contain unresolved issues.

Automatic merge also requires readable, existing branch protection that enforces up-to-date required checks for administrators. DutyLoop does not change that policy; local and PR modes work without it.

See [the issue workflow guide](docs/issues.md) for configuration, background execution, decisions, recovery, and delivery boundaries. Coverage is currently offline; an authenticated real-project issue batch has not been verified.

## Maintain a repository

Repository mode keeps accepting incremental issues and following owned pull
requests, with a task/attempt view and structured human attention. Copy
[repository.json](examples/repository.json) outside the target repository and
replace its paths and checks. It defaults to observation and local delivery:

```console
dutyloop repository init --spec repository.json --state ../repository-state
dutyloop repository run --state ../repository-state
dutyloop repository status --state ../repository-state --human
dutyloop repository attention --state ../repository-state
```

After inspecting the roadmap, explicitly enable execution with `repository
set-mode --state ../repository-state --mode auto`, then use `repository start`
to continue in the background. Observation assesses all captured sources and
creates a phased roadmap without implementing changes or admitting prospective
issues. An unchanged idle poll makes no model call. New inputs and decisions
invalidate the roadmap for new work; owned-PR follow-up can continue while its
replacement is incomplete. Waiting, retries and restarts share the original limits.

PR maintenance is separate from permission to merge. With `delivery:
pull_request`, CI failures and trusted review feedback can return the same owned
PR to planning and repair. Changed requirements and missing decisions remain
visible for you. Desktop execution still needs an active host conversation.
See [repository maintenance](docs/repository.md) for intake, decisions, stop,
recovery and MCP controls.

## Where it fits

| Responsibility | Useful inputs | Deliverable |
| --- | --- | --- |
| **Library or CLI maintenance** | Source, tests, documented behavior, reproducible defects | Focused fixes and regression tests |
| **A bounded product area** | Committed feedback, reproducible user journeys, explicit product constraints | Changes with stated acceptance criteria |
| **Documentation upkeep** | Current implementation, examples, and documentation | Checked corrections and working examples |

Supply the evidence and business direction your chosen agent needs. Weak evidence can lead to waiting; missing product decisions can lead to a blocker. For a source-backed comparison with other supervisors, see [alternatives](docs/alternatives.md).

## Host adapters

DutyLoop uses existing host installations and authentication. Choose the adapter through `driver.name`; optional MCP control uses the same mission engine.

| Integration | Adapter | Verification coverage |
| --- | --- | --- |
| **Current local desktop agent** | MCP phase handoff with `driver.name: desktop` | Offline contract checks; actual Codex/Claude desktop execution unverified |
| **Codex CLI** | Native structured output | Offline protocols + bounded authenticated mission and repository fixtures |
| **Claude Code CLI** | Native structured output | Offline protocols |
| **OpenCode 2.2.5** | Native, version-pinned JSON events | Pinned-source inspection + offline protocols |
| **DeepSeek Harness 0.2.2-rc.1** | Native, version-pinned headless execution | Pinned-source inspection + offline protocols |
| **Custom command** | Operator-owned JSON wrapper | You validate the wrapper and its host |
| **MCP client** | Optional local stdio control | Official SDK protocol tests; manual client setup |

OpenCode and DeepSeek Harness reject unsupported versions before model work. DeepSeek Harness uses its existing headless profile; omit `driver.model`. Authenticated missions are not yet recorded for Claude, OpenCode, or DeepSeek Harness. OpenHands and other hosts currently need a custom wrapper.

<details>
<summary><strong>Use optional MCP control</strong></summary>

```console
python -m pip install -e ".[mcp]"
dutyloop mcp --root ../mission-states --allow-project /absolute/path/to/project
```

Tools cover mission and issue creation, start, status, events, stop, and explicit recovery; issue decisions use `issues_resolve`. Desktop workers claim and return phases with `desktop_next`, `desktop_submit`, and `desktop_cancel`. Creation does not start work. Configure your client and project allowlist as described in [MCP setup](docs/mcp.md); use the [desktop guide](docs/desktop.md) for your current conversation. MCP connectivity alone does not certify host execution.

For an active desktop worker, use `desktop_next(..., wait_seconds=20)` to wait for work in one bounded call instead of repeated empty polls. Independent workers can wait concurrently; submissions and stop tools remain responsive. See the [measured efficiency limits](docs/validation.md#workflow-efficiency-evidence-on-2026-11-13) before comparing a complete backlog audit with a single coding instruction.

</details>

Repository coordination also exposes `repository_*` controls for reconciliation,
observation/automatic mode selection, attention and decisions, using the same
project allowlist and desktop phase mailbox.

## Project status

**Alpha, with explicit operating boundaries.** Offline behavior tests, a scripted end-to-end demo, and the recorded host checks cover specific control contracts. Long-term usefulness and reduced maintainer effort still need real-project evaluation; comparative effectiveness has not been established.

Offline fixtures cover repository coordination and GitHub delivery. A bounded authenticated Codex CLI fixture accepted a local issue fix and planned an issue introduced afterward, using simulated GitHub input. Live GitHub PR repair, merging, and issue closure remain unverified; see the [verification record](docs/validation.md) for the exact scope.

Native Claude Code phases use fresh processes and available committed project instructions. The desktop driver passes phases to an existing conversation; actual desktop execution, review isolation, and installation-specific memory behavior remain unverified.

- **Limits:** invocations count outer driver calls, not each host request or
  subagent. Desktop invocations count phase handoffs; desktop model usage remains
  unknown. Wall time includes waiting and downtime after the first run.
- **Cost:** USD hard caps are unsupported. Unknown usage or cost stays unknown.
- **Permissions:** allowed paths constrain acceptance. Worktrees share the
  host's filesystem, network, credentials, and Git access; they are not a sandbox.
- **Verification:** use trusted, non-mutating check commands. Tests an agent
  can rewrite and a separate model review do not establish independent correctness.

Keep mission state and transcripts private. Read [limits and host support](docs/limits-and-hosts.md) and the [verification record](docs/validation.md) before choosing an unattended scope.

## Documentation

| Guide | Start here when you want to… |
| --- | --- |
| [Getting started](docs/getting-started.md) | Prepare a project, run a mission, and inspect its branch |
| [Desktop agent](docs/desktop.md) | Use your current local coding conversation through MCP |
| [Configuration](docs/configuration.md) | Set paths, checks, constraints, drivers, and limits |
| [GitHub issues](docs/issues.md) | Plan a backlog, handle decisions, and configure PR/merge delivery |
| [Repository maintenance](docs/repository.md) | Reconcile new issues, maintain owned PRs, and handle human attention |
| [Examples](examples/README.md) | Adapt a maintenance or documentation mission |
| [Operations](docs/operations.md) | Monitor events, stop work, and handle recovery |
| [Adapter protocol](docs/adapter-protocol.md) | Build a host wrapper or understand native contracts |
| [Architecture](docs/architecture.md) | Understand discovery, acceptance, and persistent state |
| [Changelog](CHANGELOG.md) | Follow project changes |

## Contributing

Reproducible bug reports, adapter contract tests, and checked examples are particularly useful. [Open an issue](https://github.com/K-kiron/dutyloop/issues/new/choose) with a sanitized reproduction or a concrete workflow, or read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and required checks. Default tests use local fixtures and simulated drivers; authenticated host experiments should be labeled separately.

## License

[MIT](LICENSE) · Copyright © 2026 Wenhao XU.
