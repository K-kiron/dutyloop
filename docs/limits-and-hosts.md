# Operational limits and host support

DutyLoop runs local coding tools under an explicit mission. Its deterministic
tests validate supervisor behavior; they do not establish general agent safety
or model effectiveness.

## Budget meanings

| Control | What is enforced | What is not promised |
| --- | --- | --- |
| `max_invocations` | A driver-call reservation before dispatch; discovery, implementation, and review each count. | A token count, provider-request count, or cap on child agent/subagent activity. |
| `max_wall_seconds` | Elapsed time from the first run; dispatch stops at the deadline and active managed processes are cancelled. | CPU time, active-work-only time, or an exact provider billing boundary. Waiting and subsequent downtime consume this limit. |
| Explicit stop | A persisted stop latch, dispatch checks, and cancellation of managed processes. | Reversal of a file write, API call, charge, or other effect that has already happened. |
| Reported usage/cost | Telemetry when the host supplies it; unknown values remain unknown. | A dollar spending guarantee. USD hard-cap configuration is rejected. |

An interrupted or failed invocation remains charged against the invocation
limit. Recovery does not silently erase that charge or replay unknown work.
Deadlines trigger cancellation on the next supervisor poll. Process cleanup can
extend elapsed runtime beyond the configured window; this is not a real-time
termination guarantee.

## Scope and execution authority

`allowed_paths` describes repository-relative paths whose changes may be
accepted. It does not constrain what a child can read or do before acceptance.
Post-run Git checks cannot roll back effects outside the worktree.

- Git worktrees share repository metadata and object storage.
- A child can inherit host permissions, environment, CLI configuration, and
  existing authentication.
- Claude's implementation tools include shell execution. Tool allowlists do not
  sandbox commands launched by that shell.
- Project verification commands execute operator-approved argv. Their scripts,
  imports, tests, build tools, and hooks are executable project code.
- A fixed verification command can still run files changed by the implementer.
  Such a check is not an independent correctness oracle. Keep decisive checks
  outside writable paths where possible and verify real user behavior.
- `constraints` and `research_domains` are instructions to the agent, not an OS
  policy or a network-domain firewall.

Use a dedicated restricted account or separately managed container/VM for a
real filesystem, credential, and network boundary. DutyLoop does not provision
one automatically.

## Native adapter behavior

| Adapter | Discovery and review | Implementation |
| --- | --- | --- |
| Codex CLI | Structured output and the host's `read-only` sandbox setting. | Structured output with `workspace-write`; approval policy is `never`. |
| Claude Code CLI | Structured output with `Read`, `Glob`, and `Grep` tools. | Structured output with `Read`, `Glob`, `Grep`, `Edit`, `Write`, and `Bash`; noninteractive permission handling. |
| Custom command | Receives the phase and schema in JSON. | The wrapper owns all host-specific permission and output behavior. |

These are adapter launch settings. A custom command does not acquire native
support or sandbox guarantees simply by implementing the JSON protocol. Neither
the demo nor executable detection attests a successful authenticated model run.

## Managed child processes

On Windows, the runner creates a suspended process, assigns it to a Job Object
with kill-on-close, then resumes it. If assignment fails, it does not proceed
with an unmanaged child. On POSIX it uses a separate process group. Deadlines
and stop requests use those lifecycle boundaries; results include whether
cleanup was confirmed.

Process-tree lifecycle control is distinct from a filesystem or network sandbox.
An escaped or externally hosted operation may have effects beyond process
termination. Treat unconfirmed cleanup and unknown outcomes as requiring
inspection.

## Support and verification tiers

1. **Implemented native integrations:** Codex CLI and Claude Code CLI.
2. **Wrapper compatibility:** custom commands that implement the documented
   protocol; no host-specific compatibility is implied.
3. **Control integration:** optional MCP over the same local engine.

The local fake-driver demo covers real engine calls, separate Git worktrees,
verification, acceptance, continuation, and invocation exhaustion. It does not
test external service availability, authentication, quota, model selection,
useful prioritization, or performance on other repositories. Operating-system
coverage must be read from the actual test environment and CI results.

## State and recovery

Keep state outside the project and private to the operator. State and call
artifacts can include prompts, paths, project contents, and child logs. Do not
publish a state directory as a demonstration transcript.

If an in-flight call has no confirmed outcome, inspect its artifacts and
worktree before using:

```console
dutyloop recover --state /path/to/mission-state --acknowledge
```

Acknowledgment records operator awareness of uncertainty. It is not proof that
the previous process made no changes. A limited mission needs a new, explicitly
initialized mission with appropriate limits; recovery does not grant more budget.
