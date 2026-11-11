# Verification record and evidence boundaries

This is an experimental implementation. The checks below establish specific
control-flow behavior, not long-term autonomous effectiveness or customer value.
Raw mission state, account metadata, and host transcripts are kept private and
are not included in this repository.

## Repository coordination evidence on 2026-11-10

Repository fixtures exercise incremental admission and observation, repeat-poll
deduplication, source changes and explicit decisions, shared limits across
passes, initialization interruption, coordinator ownership, source failures,
and read-only desktop executor status. Owned-PR fixtures cover CI repairs in
PR-only delivery, review/inline/conversation feedback, trusted-author rules,
atomic repair scheduling, retained-PR decisions and unknown-result recovery.
They use local Git repositories and simulated GitHub/model responses.

The complete local Windows suite passed 553 tests and 48 subtests, with one
skip, in 751.12 seconds. Six subsequent lifecycle-projection checks and 78
CLI/MCP interface checks also passed. Ruff and source compilation passed. The
CI job timeout was increased to allow the expanded real-Git regression suite
and package checks on each supported platform; configured jobs are not evidence
of a completed run.

A separately labeled authenticated native Codex CLI 0.156.1 / `gpt-6-sol`
fixture completed four calls in 283.76 seconds: issue planning, implementation,
separate review, then planning an issue introduced after the first fix. The
first issue was accepted locally after operator checks; the incremental issue
needed a product rounding decision and remained `needs_decision`. The original
checkout stayed unchanged, all call cleanup was confirmed, and an explicit stop
ended the coordinator. GitHub intake was simulated and delivery stayed local;
this does not establish authenticated PR repair or desktop execution. The run
used the existing host login and no external API-key file. Host dollar cost was
not reported and remains unknown.

An earlier native planning call exposed conventional `file.py:line-range`
evidence being rejected as a scope path. A focused regression reproduced the
failure before correction. Citation resolution now keeps the same path guards
and original evidence receipt; traversal and malformed ranges remain rejected.

A separate authenticated GitHub CLI check initialized repository mode against
`K-kiron/dutyloop` in observation/local delivery and reconciled it twice. It
observed zero open issues, made zero model calls, and retained a stable receipt
on the repeated unchanged poll. It made no GitHub writes. This establishes the
actual empty-intake transport path, not live PR repair or backlog effectiveness.

Source and wheel distributions built using the declared isolated build
toolchain. A fresh installed wheel exposed the repository CLI and imported its
coordinator outside the source checkout; its scripted demo accepted two fixes
and preserved the original fixture checkout without model calls.

Reproduce the focused offline checks with:

```console
python -m pytest -q tests/test_repository_workflow.py tests/test_repository_cli.py tests/test_repository_mcp.py tests/test_pr_maintenance.py tests/test_evidence_paths.py
```

## Desktop integration evidence boundary

On 2026-11-10, an offline fixture used the official MCP SDK over real stdio to
create a desktop mission, start its separate coordinator, claim a discovery
phase, submit a schema-checked result, and reach the invocation limit. The
original checkout stayed unchanged. Separate issue fixtures accepted a verified
fix and rejected one that failed operator checks. Source/wheel builds and a
fresh installed-wheel smoke check also passed, including desktop imports and
CLI discovery. These fixtures used simulated host results, not paid model calls.

The desktop driver has a local MCP phase contract and example configuration for
an existing coding conversation. Official
[OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
and [Claude Code Desktop documentation](https://code.claude.com/docs/en/desktop#mcp-servers-from-the-claude-desktop-chat-app)
establish host configuration mechanisms. They do not establish that DutyLoop
has run successfully in those applications.

No actual Codex or Claude desktop end-to-end phase execution, interruption,
review isolation, or unattended run is recorded for this integration. Offline
mailbox/schema tests and generated configuration can establish only their local
contracts; they cannot verify app installation, account access, permissions,
host memory, or model behavior. Native CLI evidence below remains separately
scoped and does not validate the desktop route.

Desktop result cleanup relies on a host declaration that all work has stopped;
it is not managed-process termination evidence. Different context identifiers
are not proof that a reviewer has an independent context. Model usage and cost
from the current desktop conversation remain unknown. The
[desktop guide](desktop.md) documents setup, handoff, stop, and recovery.

## Issue workflow evidence on 2026-11-10

The issue tests use real temporary Git repositories, local bare remotes, and
operator verification processes, with simulated host responses and GitHub APIs.
They exercise backlog snapshots, dependencies, per-issue decisions, persistent
limits, CI repairs, PR metadata readback, merge/closure refusal boundaries, and
explicit recovery without replaying uncertain effects. One complete simulated
merge/closure path also retains the production Git process-containment layer;
the queue-policy tests use synchronous Git while retaining contained operator
checks. Process and mission tests cover cancellation, hooks, and deadlines
separately. This is not an authenticated coding or GitHub delivery experiment.

A read-only CLI initialization against `K-kiron/dutyloop` succeeded on this date.
It captured zero open issues, made zero model calls, and used local delivery.
This verifies repository access and an empty-backlog response through the actual
GitHub CLI, not live issue/comment pagination, coding, CI, merging, or closure.

Reproduce the offline issue checks with:

```console
python -m pytest -q tests/test_github.py tests/test_issue_config.py tests/test_issue_store.py tests/test_issue_cli.py tests/test_issue_workflow.py tests/test_issue_safety.py tests/test_selected_task.py
```

The installed-wheel smoke check also imports the issue controller and checks
the issue CLI outside the source checkout. Live host and delivery claims remain
limited to the separately labeled historical records below.

## Readiness verification on 2026-11-05

The version-0.2.0 work was checked locally on Windows/Python 3.11:

| Check | Observed result | Scope |
| --- | --- | --- |
| Complete offline suite | 139 tests and 48 subtests passed. | Includes real temporary Git repositories, process cleanup, positive-control Git hooks, cursor events, configuration, and official MCP stdio. No authenticated model calls. |
| Ruff and syntax | Passed for source, tests, and distribution verification utility. | Static checks, not correctness or security certification. |
| Source and wheel distributions | Both built. | Includes native adapter modules, bilingual documentation, examples, and maintainer tooling. |
| Fresh installed wheel | Version 0.2.0; scripted demo accepted two fixes, continued discovery, reached its limit, and preserved the original fixture checkout. | Fresh virtual environment outside source; no source-path override and no model calls. |
| New native protocols | OpenCode 2.2.5 and DSH 0.2.2-rc.1 offline fixtures passed. | Fixed-source launch and event contracts, not authenticated live executions. |

The read-only preflight regression first proves that a configured repository
filesystem-monitor hook can execute, then verifies that configuration validation
and project diagnostics do not execute it. Runtime hook regressions likewise
check actual execution before asserting cancellation or commit-history rejection.

## Historical verification on 2026-10-29

Recorded on 2026-10-29 (America/Toronto), using Windows and Python 3.11.5:

| Check | Observed result | Scope |
| --- | --- | --- |
| Full offline suite | 88 tests and 21 subtests passed. | Behavior, protocols, stop and limit handling, recovery, managed process trees, and official MCP SDK stdio communication. No paid model calls. |
| Source and wheel build | Both distributions built; source syntax compiled. | Packaging, not model behavior. |
| Fresh wheel installation | Deterministic demo accepted two fixes, continued discovery, and stopped after seven calls. | Real supervisor/worktrees/checks with an explicitly scripted fake driver; original fixture checkout unchanged. |
| Native Codex CLI 0.156.1, `gpt-6-sol` | Discovery, implementation, operator verification, separate review, then another discovery returning `wait`; one accepted fix in four calls, 172.44 seconds, invocation limit reached. | One tiny local pricing fixture and one account/environment. All child cleanups were confirmed. |
| Claude Code CLI 2.1.287 | Executable/version checks and offline adapter tests. | No authenticated live mission was run. |

The live fixture initially returned the unmodified amount from
`discounted_price(amount, percent)`. The accepted implementation applied the
percentage before rounding. The operator's fixed assertion command failed
against the initial version and passed against the accepted change. Only
`app.py` changed; the original fixture's branch, HEAD, files, and Git status
were unchanged. The test supplied an ongoing reliability mission, not a task
list. Its deliberately obvious defect is not representative of product research.

A preceding native attempt failed because its configured model was unavailable
to the account. That failure is not counted as a successful mission. The live
check used a per-mission model selection without changing global host settings.
Executable detection and model-directory entries alone do not attest model access.

The MCP check used the official Python SDK 1.30.0 client/server over stdio.
This does not certify installation or operation in every MCP client.

## Reproduction and platform coverage

Run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md) for offline verification,
or the README's deterministic demo for the installed-package path. Test fixtures
do not silently invoke authenticated models. A live check requires the operator's
own CLI, account, explicit mission, verification commands, and limits.

The [CI workflow](../.github/workflows/ci.yml) is configured to run offline tests,
lint, syntax checks, distribution builds, and a fresh installed-wheel demo on
Windows, Ubuntu, and macOS with Python 3.11 and 3.13.
Read the [actual runs](https://github.com/K-kiron/dutyloop/actions) for platform
results; a configured matrix is not a completed or passing run.

## Native adapter expansion on 2026-11-05

OpenCode 2.2.5 and DeepSeek Harness 0.2.2-rc.1 adapters were added against fixed
official source revisions. Offline fixtures check phase launch settings, final
event/session validation, strict JSON/schema handling, error categories, usage
boundaries, and managed cancellation. Neither executable was installed in the
development environment for this work, and no authenticated mission was run
with either host. Their supported-version check fails before model work for
other versions.

Codex and Claude coverage also includes malformed terminal results, host errors,
and output/schema failures. New onboarding and control commands are exercised
without starting paid model work. These checks broaden reproducible contract
coverage; they do not turn the historical Codex fixture into a four-host live
validation result. See [the protocol/source record](adapter-protocol.md).

## Not established

- More useful improvements or less human work than an assistant plus ordinary
  automation, AUTOPILOT, OpenClaw, or another supervisor.
- Reliable market judgment, business prioritization, or acceptance by real users
  and maintainers. A separate same-model review is not independent user evidence.
- Long-term unattended stability, a security sandbox, absence of vulnerabilities,
  hard monetary enforcement, or exact termination at a deadline.
- Authenticated Claude, DeepSeek Harness, or OpenCode operation, or a native
  OpenHands integration. The implemented pinned adapters have offline coverage;
  a custom wrapper is only a protocol extension point for other hosts.
- A benefit from multiple discovery workers over a single worker at equal cost.

See [limits and host support](limits-and-hosts.md) and the
[source comparison](alternatives.md) before choosing a deployment scope.
