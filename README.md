# DutyLoop

**Give a coding agent an ongoing responsibility for your project.**

[English](README.md) · [简体中文](README.zh-CN.md) · [Getting started](docs/getting-started.md) · [Configuration](docs/configuration.md)

[![CI](https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml/badge.svg)](https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

DutyLoop runs a local coding agent against a repository mission: inspect current
evidence, decide what is worth doing, define a task, implement it, run your checks,
review the result, and look for the next useful improvement. Finishing a task does
not finish the mission. It continues until a limit, blocker, or your stop command.

For example, delegate “maintain the reliability of this library's parsing and
export paths” instead of repeatedly writing individual bug-fix prompts. You set
the scope, constraints, checks, and limits once. DutyLoop records its decisions
and delivers accepted changes on a separate local Git branch for you to inspect.

## What you can delegate

| Responsibility | Evidence it can use | Expected output |
| --- | --- | --- |
| Maintain a library or CLI | Current source, tests, documented behavior, committed issue reproductions | Focused fixes and regression tests |
| Improve a bounded product area | Project feedback files, reproducible user journeys, explicit product constraints | Changes with stated acceptance criteria |
| Keep documentation usable | Current code, examples, and documentation | Verified corrections and working examples |

The mission must supply enough product direction to judge useful work. When the
evidence is weak, waiting is a valid outcome. When a business decision is missing,
the agent should report a blocker. DutyLoop has no built-in customer-feedback
collector, competitor crawler, or market-analysis service; provide the evidence
and tools your chosen agent can actually access.

## Try the local demo

Requires **Python 3.11+** and **Git**. The demo also needs a configured Git author
identity. Installation currently uses the source repository:

```console
git clone https://github.com/K-kiron/dutyloop.git
cd dutyloop
python -m pip install -e .
dutyloop demo --directory ../dutyloop-demo
```

Use a new or empty demo directory. A **scripted driver** discovers two fixture
defects while the real supervisor creates worktrees, runs tests, accepts changes,
continues discovery, and stops after seven invocations. No model account or API
key is used. This demonstrates the workflow; it does not benchmark model judgment.

## Give it a real project

Install and authenticate a supported coding CLI through that CLI's own setup.
Choose a project with a committed `HEAD` and checks that run from a fresh checkout.
Uncommitted changes in your working copy are not copied into the mission.

1. Copy [examples/mission.json](examples/mission.json) outside the target repository.
2. Set the absolute project path, a specific ongoing mission, allowed paths, and
   verification commands suitable for that project.
3. Check the setup, then initialize and run:

```console
dutyloop doctor --driver codex --project /absolute/path/to/project
dutyloop validate --spec mission.json --state ../mission-state
dutyloop init --spec mission.json --state ../mission-state
dutyloop run --state ../mission-state
```

For a detached run and a quick progress check:

```console
dutyloop start --state ../mission-state
dutyloop status --state ../mission-state --human
dutyloop events --state ../mission-state --limit 20
dutyloop stop --state ../mission-state
```

`stop` requests cancellation; read status again to confirm the runner has exited.
State must stay outside the target repository. The [getting-started guide](docs/getting-started.md)
includes mission generation, inspecting the resulting branch, and troubleshooting.

## What is built in

- **Continuous discovery:** one to three discovery workers propose evidenced
  candidates with acceptance criteria; implementation stays serial.
- **A complete delivery loop:** separate implementation and review invocations,
  plus operator-supplied verification commands run by the supervisor.
- **Inspectable Git output:** managed task worktrees, accepted commits on a
  mission branch, and retained evidence for rejected or interrupted attempts.
- **Persistent control:** local SQLite state, event history, exclusive runner
  ownership, explicit interruption recovery, and background execution.
- **Enforced invocation and elapsed-time limits:** calls are reserved before
  dispatch; explicit stop requests are persisted and managed children cancelled.
- **CLI and optional MCP control:** use the same engine from a terminal or a
  configured MCP client.

The runtime does not automatically push, open PRs, merge into your working branch,
or deploy. Review and integrate the resulting commits through your own workflow.

## Integrations and maturity

| Integration | Current support | Verification boundary |
| --- | --- | --- |
| Codex CLI | Native structured-output adapter | Offline protocol tests and one authenticated fixture mission |
| Claude Code CLI | Native structured-output adapter | Offline protocol tests; no authenticated fixture mission recorded |
| OpenCode CLI **2.2.5** | Version-pinned native JSON-event adapter | Source-grounded launch policy and offline protocol tests; no authenticated fixture mission |
| DeepSeek Harness **0.2.2-rc.1** | Version-pinned native headless adapter | Source-grounded launch policy and offline protocol tests; no authenticated fixture mission |
| Custom command | Documented JSON wrapper protocol | You supply and verify the host-specific wrapper |
| MCP client | Optional local stdio control server | Official SDK protocol test; client configuration is manual |

Other hosts do not acquire native support merely by offering MCP. See the
[support matrix](docs/limits-and-hosts.md), [adapter protocol](docs/adapter-protocol.md),
and [verification record](docs/validation.md) for exact coverage.

OpenCode and DeepSeek Harness reject unrecognized versions before model work.
DeepSeek Harness uses its existing headless profile; omit `driver.model` for it.
OpenHands and other runtimes currently require an operator-owned custom wrapper.

DutyLoop is an early-stage project. Its supervisor behavior has automated tests;
long-term usefulness and reduced maintainer effort still need real-project
evaluation. [Existing alternatives](docs/alternatives.md) include AUTOPILOT,
OpenClaw, Paperclip, and Symphony. DutyLoop focuses on a small local mission
contract with inspectable state; it has no comparative effectiveness result.

## Read these boundaries before an unattended run

**Limits:** `max_invocations` counts driver calls, not the requests or subagents
inside a coding CLI. `max_wall_seconds` includes waiting and later downtime.
**USD hard caps are unsupported**; missing cost stays unknown.

**Authority:** allowed paths are checked before acceptance. Worktrees do not
sandbox the filesystem, network, credentials, or shared Git metadata. Children
run with the authority of their host environment. Use a separately restricted
account, container, or VM when that boundary is required.

**Verification:** your verification argv executes project code. A test the agent
can rewrite is not an independent acceptance oracle, and a separate model review
does not establish user value. Keep decisive checks outside writable paths where
practical. State and logs can contain private project data; keep them private.

See [operational limits](docs/limits-and-hosts.md) for the complete contract.

## Documentation and contributing

- [Getting started](docs/getting-started.md): first mission and branch review.
- [Configuration reference](docs/configuration.md): all fields, defaults, and examples.
- [Operations](docs/operations.md): status, events, limits, interruption, and recovery.
- [MCP setup](docs/mcp.md): manual client configuration and tool authority.
- [Architecture](docs/architecture.md): discovery, acceptance, persistence, and failure handling.
- [Contributing](CONTRIBUTING.md): development checks and integration expectations.

MIT licensed. Bug reports and concrete compatibility reports are welcome in
[GitHub Issues](https://github.com/K-kiron/dutyloop/issues).
