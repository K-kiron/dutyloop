# Existing alternatives and the experiment

This comparison records an inspection on 2026-10-29 using original repositories
and their documentation. It is a static source comparison, not an operational
benchmark. The projects may have changed after these revisions.

## AUTOPILOT

Inspected revision: `0d6cf5ad7945dfe027643f821422f26021a58778`.

AUTOPILOT already implements repository inspection, task proposals, gated
implementation, commits/reverts, repeated execution, multiple CLI adapters,
budgets, fleets, and a dashboard. Its empty-board prompt asks the model to emit
proposals. **Auto-mode can admit those proposals without per-task human approval**:
the [auto-approval implementation](https://github.com/M-A-S-T-E-R-M-I-N-D/AUTOPILOT/blob/0d6cf5ad7945dfe027643f821422f26021a58778/packages/store/src/auto-approve.ts)
records the operator's per-project setting and preserves exceptions for work
that needs a person. DutyLoop does not claim that autonomous fresh-task
discovery or cross-CLI operation is a missing category.

Its [engine budget behavior](https://github.com/M-A-S-T-E-R-M-I-N-D/AUTOPILOT/blob/0d6cf5ad7945dfe027643f821422f26021a58778/apps/dashboard/src/flight/firing-engine.ts#L271)
also makes an important distinction: Codex/Gemini lanes report no price, so the
per-firing and total dollar limits cannot stop them. Count, wall-clock, and
failure-demotion controls remain available.

## OpenClaw

Inspected revision: `87016c018d3e4a4c44e34de96bb3cea25b28548c`.

[Standing orders](https://github.com/openclaw/openclaw/blob/87016c018d3e4a4c44e34de96bb3cea25b28548c/docs/automation/standing-orders.md)
define ongoing responsibility, scope, triggers, and escalation. Automations
supply scheduled execution. [Goals](https://github.com/openclaw/openclaw/blob/87016c018d3e4a4c44e34de96bb3cea25b28548c/docs/tools/goal.md)
provide durable objectives and automatic continuation for supported built-in
chat and Codex sessions, with explicit stop and token-budget states. The token
guardrail is not a billing cap, and an active goal alone does not start a new
process after shutdown. These capabilities already express much of DutyLoop's
intended pattern inside a broader assistant gateway.

## Paperclip

Inspected revision: `a8e07500db68f187b19de83823eb7fabf1196021`.

[Paperclip](https://github.com/paperclipai/paperclip/blob/a8e07500db68f187b19de83823eb7fabf1196021/README.md)
provides organization goals, roles, agent coordination, scheduled heartbeats,
cost accounting, budgets, and governance. Its default assignment protocol
should not be mistaken for an inability to delegate or pursue a broader
mission. The main difference is the organizational control plane and setup,
not an absence of autonomous project work. Its documented budget enforcement
uses recorded spend; usage reporting and in-flight work can delay a stop.

## Symphony

Inspected revision: `be10a1b79df723d6d7612b5651c8522704dafb2e`.

The [Symphony specification](https://github.com/openai/symphony/blob/be10a1b79df723d6d7612b5651c8522704dafb2e/SPEC.md)
describes a long-running issue-tracker reader and coding-agent runner with
isolated workspaces, retries, and reconciliation. A separate policy can discover
work and create issues above it. DutyLoop keeps that discovery policy within
its mission loop rather than requiring an issue tracker as the queue.

## What DutyLoop is testing

The hypothesis is that a smaller local supervisor is useful for operators who
want a repository mission, explicit evidence, external invocation/time limits,
and inspectable worktrees without adopting a company platform or assistant
gateway. This is a packaging and control-contract experiment, not a claim of
new autonomous reasoning or demonstrated superiority.

The deterministic demo tests whether the actual engine continues from one
accepted change to another discovered change and then stops at its invocation
limit. It cannot establish that an LLM will discover useful work. A meaningful
future comparison would use the same repositories, permissions, verification
commands, and limits against AUTOPILOT auto-mode or an OpenClaw setup, measuring
accepted useful changes, required interventions, false completion, and stopped
processes. No such comparative result is reported here.
