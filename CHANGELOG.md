# Changelog

## Unreleased

- Add a finite GitHub issue workflow with backlog/comment snapshots, read-only
  planning, dependency-aware batches, per-issue branches, decision handling,
  persistent shared budgets, and explicit interruption recovery.
- Add opt-in PR and CI-gated merge delivery, head-bound merge requests,
  post-merge tree and command verification, and optional confirmed issue closure.
  Default issue delivery stays local; coverage uses offline fixtures, without an
  authenticated real-project batch.
- Add `dutyloop issues` CLI controls, a Claude Code configuration example, and
  an issue workflow guide. Existing MCP tools continue to control missions only.
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
