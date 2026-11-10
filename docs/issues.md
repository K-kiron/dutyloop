# Process a GitHub issue backlog

Issue mode turns a finite snapshot of open GitHub issues into plans, isolated
changes, and optionally pull requests and merged fixes. Items needing a decision
remain visible while other eligible issues continue. It does not promise that
every open issue can be resolved automatically.

## Prepare and inspect plans

Install and authenticate a supported coding CLI and the GitHub CLI (`gh`) using
their existing setup. Configure a Git author identity. The target repository
needs committed code and trusted verification commands that work in a fresh
worktree. Its configured fetch and push remote must both identify the selected
`github.com` repository; forks and GitHub Enterprise are not supported here.

Copy [the issue example](../examples/issues.json) outside the target repository.
Replace `project`, `github.repository`, allowed paths, and verification commands.
Keep `delivery: "local"` for a run that makes no GitHub writes:

```console
dutyloop issues init --spec issues.json --state ../issue-state
dutyloop issues run --state ../issue-state --plan-only
dutyloop issues status --state ../issue-state --human
```

`init` reads open issues and their comments, excludes pull requests, and captures
the backlog and committed local `HEAD` in SQLite. It does not call a model or
modify GitHub. New issues opened later are outside this batch; use a new state
directory for another snapshot. Issue bodies, comments, and transcripts can
contain private material, so keep the state directory private and outside the
target repository.

`--plan-only` invokes the selected host and consumes the same persistent call and
time budget as execution. It creates local state and observation worktrees,
without implementing or publishing changes. Inspect each issue's plan, candidate
acceptance criteria, dependencies, and reasons in JSON status output.

## Run and control a batch

```console
dutyloop issues run --state ../issue-state
dutyloop issues status --state ../issue-state --human
dutyloop issues events --state ../issue-state --after 0 --limit 20
dutyloop issues stop --state ../issue-state
```

Use `dutyloop issues start --state ../issue-state` instead of `run` for a detached
runner. Stop is asynchronous; inspect status again to confirm it has stopped.
A recorded stop cannot be cleared by restarting.

The runner plans captured issues, orders actionable work by priority and
dependencies, and implements one issue at a time. Each implementation uses the
mission engine's scope checks, operator verification, and separate review. Local
issue branches are recorded in status. In local delivery, accepted commits
accumulate from the batch's `accepted_head`, so later issues can use earlier
fixes. The operator checkout is not switched or pulled, and its uncommitted
changes are not copied into worktrees.

`queue.batch_size` limits the distinct issues implemented in one `run`; run again
to process another group. Planning can cover the whole captured queue. The
invocation budget and first deadline apply to the entire batch across runs,
including planning, retries, waiting, and downtime. Starting again does not renew
them. A successful local fix or open PR does not close its GitHub issue.

## Choose delivery explicitly

| `github.delivery` | Actions when you run or start |
| --- | --- |
| `local` (default) | Create local branches and verification records; make no GitHub writes. |
| `pull_request` | Push each accepted issue branch and create or update its owned PR; leave merging and closure to the maintainer. |
| `merge` | Deliver each issue through PR checks and merge, then verify the merged result before continuing. Optional issue closure follows verification. |

Selecting external delivery in the configuration authorizes those operations
when you invoke `run` or `start`. Review the configuration before starting.
`init` itself remains read-only on GitHub. GitHub permissions, branch protection,
and review requirements still apply; the runner does not bypass them.

Automatic merge additionally requires an existing base-branch protection rule
that enforces checks for administrators, requires the branch to be up to date
(`required_status_checks.strict`), and includes every configured required check
in its server-side check contexts. The authenticated GitHub account must be able
to read that protection. Missing protection or read access blocks the batch;
insufficient protection leaves the issue at `needs_decision` with reason
`strict_merge_protection_required`. DutyLoop does not change repository security
policy or provide an override. Local and PR delivery do not require this rule.

For automatic merge and closure, set these fields **before initialization**:

```json
{
  "delivery": "merge",
  "close_issues": true,
  "required_checks": ["your-required-check-name"],
  "merge_method": "merge"
}
```

These fields belong inside `github`. Replace the check name with the exact check
or commit-status contexts used by your repository. Merge delivery requires a
nonempty list. Only successful required checks satisfy it; absent or unfinished
checks cause waiting. `merge_method` accepts `merge` (default), `squash`, or
`rebase`. `close_issues: true` is only valid for merge delivery.

Before publication, the runner checks the owned branch, outgoing commit messages,
and intended PR metadata, then reads back the remote branch, commits, and PR.
Merge requests are bound to the verified head. A changed base requires inspection
rather than silently incorporating unrelated changes. After merge, the runner
confirms that the merged commit is in the target branch, checks that its tree
matches the verified tree, and reruns the operator checks in a fresh worktree.
It closes the issue only when enabled, its captured requirements remain current,
and closure can be confirmed by reading GitHub back.

Failed CI can trigger another implementation attempt on the same issue branch,
up to `queue.max_delivery_attempts`. Conflict, changed requirements, an unmatched
merged tree, failed post-merge checks, or pending repository approvals can leave
work unfinished. Deployment and production acceptance remain outside this flow.

## Decisions, dependencies, and interruptions

Issues carrying an excluded label start as `needs_decision`. The planner also
uses that state for missing business intent, unverifiable acceptance criteria,
duplicates, or issues that appear already solved. These items are not closed
automatically. Other actionable issues continue; unmet dependencies remain
`waiting_dependency`.

Read status and supply a concrete decision when you have one:

```console
dutyloop issues resolve --state ../issue-state --number 42 --decision "Preserve the existing API; return an empty list for an empty file."
dutyloop issues run --state ../issue-state
```

`resolve` fetches and explicitly approves the latest issue content, records the
snapshot change, and requeues a decision, failed, or dependency-waiting issue that
has no accepted head or PR. Read the current issue and provide the missing intent
before invoking it. It does not edit GitHub or renew the budget. An existing
delivery requires inspection and reconciliation before a new batch. During
normal runs, changes to an issue's title, description, comments, or labels since
capture require a decision rather than silently changing the accepted scope.

After an interruption, inspect local work and remote effects before acknowledging:

```console
dutyloop issues status --state ../issue-state
dutyloop issues recover --state ../issue-state --acknowledge
```

Recovery requires exclusive runner ownership. It marks uncertain calls as
unknown and keeps them charged. It uses GitHub reads to reconcile interrupted
publication, merge, or closure against the owned PR and observed issue state.
Confirmed results are recorded; other incomplete work is left for a decision.
Recovery performs no GitHub writes and never automatically replays
implementation, pushes, merges, or closure. Inspect the returned status before
running again, and reconcile retained work before starting a replacement batch.
Safety blockers and stop/limit states are not cleared by acknowledgement.
`completed` means this pass has no further eligible work, not that every captured
issue is closed.

## Configuration reference

Issue configuration uses the [mission fields](configuration.md) plus `github`
and `queue`; omit `branch` because issue branches are allocated separately.
Configuration is persisted at initialization; editing the source JSON afterward
does not change an existing batch.

| Field | Default | Meaning |
| --- | --- | --- |
| `github.repository` | Required | `owner/name` on `github.com`. |
| `github.remote` | `origin` | One named fetch/push remote for that repository. |
| `github.base_branch` | `main` | PR destination branch. Local delivery starts from captured local `HEAD`. |
| `github.delivery` | `local` | `local`, `pull_request`, or `merge`. |
| `github.close_issues` | `false` | Close only after verified merge delivery. |
| `github.required_checks` | `[]` | Exact check/status names; mandatory and nonempty for merge. |
| `github.merge_method` | `merge` | `merge`, `squash`, or `rebase`. |
| `queue.numbers` | `[]` | Empty selects all captured open issues; otherwise select these open issue numbers. |
| `queue.exclude_labels` | `["needs-decision", "needs-human"]` | Put matching issues aside for a decision. |
| `queue.batch_size` | `10` | Maximum distinct issues implemented per run. |
| `queue.max_issues` | `500` | Refuse a larger selected backlog rather than silently truncate it. |
| `queue.max_delivery_attempts` | `2` | Maximum implementation deliveries per issue, including CI repairs. |

## Claude Code and verification boundaries

Set `driver` to `{"name": "claude"}` to use the native Claude Code adapter.
Committed `CLAUDE.md` and other repository instructions are available in the
managed worktrees. Each phase launches a fresh host process; it does not resume
your existing interactive conversation. Loading your installation's automatic
memory has not been verified. Keep essential instructions and check commands
in committed project files and the operator configuration.

The optional MCP server currently controls standing missions only. A Claude Code
session can invoke the issue CLI through its shell tool when authorized; there
are no `issue_*` MCP tools.

Issue queue and delivery verification currently uses offline fixtures and
simulated hosts/GitHub responses. No authenticated real-project batch establishes
end-to-end Claude execution, real CI/merge behavior, or maintenance effectiveness.
Host processes retain their usual filesystem, network, and credential access;
worktree scope controls acceptance and is not a sandbox. See
[limits and hosts](limits-and-hosts.md) and [validation](validation.md).
