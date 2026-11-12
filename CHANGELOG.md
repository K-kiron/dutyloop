# Changelog

## Unreleased

- Investigate large backlogs in bounded parallel waves, defaulting to three
  isolated workers sharing the original call and wall-time budgets. Retain all
  child receipts and completed chunks on interruption; synthesize only after
  complete coverage and join workers before returning.
- Reuse source investigations across runtime questions and failures, keeping
  live issue and stage gates. Reassess only changed chunks on the same code and
  authority; new bases or planning authority require fresh investigations.
- Write redirected CLI output and background logs as UTF-8, including structured
  error output, rather than failing on Unicode under legacy Windows encodings.

- Assess the complete captured backlog against the current repository stage
  before execution. Persist evidence, phased goals and acceptance gates,
  dependencies, deferred work and human decisions; assess large inputs in
  resumable chunks without treating partial coverage as an executable roadmap.
- Plan individual issues only when their stage is ready, against the execution
  base, leaving budget for implementation and review. Reassess at phase
  boundaries and source changes while retaining unfinished stage obligations,
  existing PR repairs, decisions and shared limits.

- Keep repository intake and planning responsive while merge checks are pending;
  retain finite issue-batch waiting. Classify clean read deadlines as limits,
  preserve blocked unknown results across stop signals, and support explicitly
  trusted GitHub App reviewer logins.

- Add repository coordination with durable incremental issue intake, observation
  and explicit automatic modes, task/attempt views, structured attention, and
  CLI/MCP controls. Preserve budgets, stop latches and uncertain results across
  passes and restarts; unchanged idle reconciliation makes no model call.
- Maintain owned PRs through CI and trusted review feedback independently of
  merge permission. Reassess feedback, deduplicate inputs, bound repair attempts,
  and keep the existing branch and PR. Retained deliveries can resume after an
  explicit operator decision and ownership checks.

- Add a desktop driver that hands phases to the current local coding conversation
  through MCP, without launching a separate model CLI or selecting its model.
  Keep acceptance checks and delivery in the existing supervisor.
- Add durable claims, schema-checked idempotent results, fresh-context review
  declarations, and explicit quarantine/recovery for uncertain desktop work.
  Desktop cancellation and usage are cooperative, not process containment or
  model-call accounting.
- Expose issue workflows over MCP, including planning-only startup, and print
  local desktop connection settings without editing host configuration. Isolate
  noninteractive Git stdin from the MCP protocol stream.
- Add a finite GitHub issue workflow with backlog/comment snapshots, read-only
  planning, dependency-aware batches, per-issue branches, decision handling,
  persistent shared budgets, and explicit interruption recovery.
- Add opt-in PR and CI-gated merge delivery, head-bound merge requests,
  post-merge tree and command verification, and optional confirmed issue closure.
  Default issue delivery stays local; coverage uses offline fixtures, without an
  authenticated real-project batch.
- Add `dutyloop issues` CLI controls, a Claude Code configuration example, and
  an issue workflow guide.
- Stage and materialize task files before verification, including Git filter
  conversions. Block verification commands that change tracked content or the
  staged tree, and record the verified tree in events and accepted tasks.
- Reject hidden assume-unchanged/skip-worktree index flags, detect file-mode
  changes during checks, and refuse to commit a staged tree or base that changed
  after review. Rejected attempts remain inspectable.

## 0.2.0 — Unreleased

- Add configuration generation and read-only validation, bounded installation
  diagnostics, readable mission status, and paginated CLI/MCP audit events.
- Add version-gated native OpenCode 2.2.5 and DeepSeek Harness 0.2.2-rc.1 adapters.
  These have pinned-source and offline protocol coverage; authenticated live
  missions are not yet verified for either adapter.
- Leave enough invocation budget for implementation and mandatory review before
  starting a task. Reduce discovery fanout when necessary.
- Preserve terminal stop/limit/failure states during recovery, stop repeated
  permanent host errors, and retain uncertain calls without refunding budget.
- Remove clean successful observation worktrees and successful Git capture logs,
  preserve failed or modified work, and check commit-hook ancestry before acceptance.
- Rewrite English and Chinese introductions around ongoing responsibility; add
  configuration, operation, troubleshooting, MCP, and contributor documentation.
- Add lint checks, installed-wheel smoke verification, and macOS CI alongside
  Windows and Ubuntu. CI runs no authenticated model calls.

## 0.1.0 — 2026-10-29

- Initial standing-mission supervisor, isolated task worktrees, persisted SQLite
  state, operator verification, separate review, stop/recovery controls, native
  Codex/Claude adapters, custom command protocol, and optional MCP control.
- A scripted two-fix demonstration and one bounded native Codex fixture run.
