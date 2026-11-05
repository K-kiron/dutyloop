# Verification record and evidence boundaries

This is an experimental implementation. The checks below establish specific
control-flow behavior, not long-term autonomous effectiveness or customer value.
Raw mission state, account metadata, and host transcripts are kept private and
are not included in this repository.

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
