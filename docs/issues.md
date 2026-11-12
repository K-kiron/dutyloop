# Process a GitHub issue backlog

For incremental intake and continued PR follow-up, use
[repository mode](repository.md). The commands below process a finite captured
backlog; repository mode coordinates repeated passes with the same limits.

Issue mode assesses a finite snapshot of open GitHub issues against the current
repository stage, creates a phased roadmap, then plans and implements the next
eligible issue. Delivery can remain local or use explicitly configured pull
requests and merges. It does not promise that every issue can be resolved
automatically.

## Prepare and inspect plans

Install and authenticate the GitHub CLI (`gh`) using its existing setup.
Choose a [current desktop agent](desktop.md) through MCP, or install and
authenticate a supported coding CLI for native execution. The CLI examples
below use native execution; desktop workers follow the handoff guide instead.
Configure a Git author identity. The target repository
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

`--plan-only` assesses the whole captured backlog and produces only a global
phased roadmap. It does not create detailed plans for every issue, implement, or
publish changes. It invokes the selected host in read-only discovery calls and
consumes the same persistent call and time budget as execution. Supply the
current milestone or release objective in the queue configuration, for example:

```json
{
  "queue": {
    "planning_context": "Stabilize CSV imports for the next release before adding export formats.",
    "planning_max_bytes": 131072,
    "planning_workers": 3
  }
}
```

Planning considers every captured issue body and comment. Inputs that fit in one
bounded request use a single roadmap call; larger inputs use complete bounded
assessment chunks before global synthesis. `queue.planning_workers` limits
concurrent chunk assessments to three by default; use `1` for serial assessment
or choose an integer up to `8`. It does not force extra calls when the backlog
fits one serialized prompt. Native adapters dispatch these assessments
automatically. Each wave uses the same frozen repository head and at most the
smaller of the worker ceiling and remaining shared call slots.

Chunks can finish out of order; successful results are retained and ordered
deterministically for synthesis. All required chunks must complete before the
single global synthesis call. Cancellation waits for in-flight coordinator work
to finish; desktop hosts must also stop their own external work as described in
the [handoff guide](desktop.md). Interrupted waves retain completed siblings but
cannot synthesize incomplete coverage. All assessments and synthesis share the
original invocation and wall-time limits; implementation remains serial.
Inputs are not silently truncated.
An oversized source leaves planning `needs_decision`; a partial assessment leaves
it `incomplete`. Both report unassessed issue numbers and cannot activate a
partial roadmap for new work.

Runtime outcomes such as a detailed planner's question, a failed attempt or a
dependency wait keep the existing repository assessment and stage structure.
Their live issue states still block affected work and later stages; independent
eligible work in the current stage can continue. A local outcome alone does not
spend the budget on another whole-backlog investigation.

Changed captured sources, admission or operator decisions require a fresh global
roadmap. In the chunked path, a completed assessment can be reused only when
every issue in that chunk has the same source, admission and decision bindings,
and the repository base, operator authority and milestone context are unchanged.
A changed member causes its whole chunk to be reassessed before global
synthesis. Base, authority or milestone changes require fresh assessments for
all chunks. Older records missing reuse bindings receive a fresh assessment;
reuse never approves execution or renews the budget.

Inspect `planning` in JSON status or the compact `--human` view. It records the
revision, source fingerprint, base commit, assessed and unassessed issue numbers,
current repository stage, roadmap evidence, stage goals and rationale, entry
conditions, exit criteria, human acceptance gates, and issue dispositions with
questions or deferral reasons. A complete durable roadmap is required before
selecting new work. `planning.workers` records the configured ceiling;
`planning.chunks` records each request's ID, operation, issue numbers, child
state and status (`prepared`, `running`, `completed` or `incomplete`). The human
view summarizes completed and active requests. `planning.child_states` retains
all child state paths for the revision; `child_state` remains available for
compatibility. `planning.reused_assessments` and `planning.reused_issues` identify
coverage carried from completed chunks into a new revision.

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

The runner follows roadmap stage order and dependencies. It detail-plans only
the next eligible issue against the current accepted base, then implements,
runs operator checks, and requests separate review. A later stage cannot skip
incomplete work in an earlier stage. Stages requiring human acceptance stop new
work for a concrete operator decision. Textual entry and exit criteria describe
evidence to assess; they do not establish automated proof of product acceptance.
Each implementation retains the mission engine's scope and acceptance checks.
Local issue branches are recorded in status. In local delivery, accepted commits
accumulate from the batch's `accepted_head`, so later issues can use earlier
fixes. The operator checkout is not switched or pulled, and its uncommitted
changes are not copied into worktrees.

`queue.batch_size` limits the distinct issues implemented in one `run`; run again
to process another group. Roadmap assessment covers the whole captured queue. The
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

Owned-PR maintenance also works with `delivery: pull_request`. It does not grant
merge or closure permission. `github.maintain_pull_requests` defaults to `true`.
Trusted review feedback is reassessed against the original issue before repair;
GitHub associations `OWNER`, `MEMBER`, `COLLABORATOR` and the explicit
`github.review_authors` list identify trusted reviewers. Repeated inputs are
deduplicated, and repairs use the same branch and PR. A finite run inspects
available feedback; repository mode continues checking in subsequent passes.

## Decisions, dependencies, and interruptions

Issues carrying an excluded label start as `needs_decision`. The planner also
uses that state for missing business intent, unverifiable acceptance criteria,
duplicates, or issues that appear already solved. These items are not closed
automatically. Other actionable issues can continue within the current eligible
stage; unmet dependencies remain `waiting_dependency`. A blocked earlier stage
does not authorize jumping to a later one.

Replanning retains unfinished active-stage obligations until verification or an
explicit operator decision. If such an issue was closed outside the batch, use
`resolve` to record whether independent work should proceed with it deferred.
It remains `external_closed`, with no verified outcome; issues still depending
on it remain blocked.

Read status and supply a concrete decision when you have one:

```console
dutyloop issues resolve --state ../issue-state --number 42 --decision "Preserve the existing API; return an empty list for an empty file."
dutyloop issues run --state ../issue-state
```

`resolve` fetches and explicitly approves the latest issue content, records the
snapshot change, and requeues a decision, failed, or dependency-waiting issue that
needs replanning. Read the current issue and provide the missing intent before
invoking it. An existing open owned PR keeps its branch, head, PR identity and
attempt count after ownership and metadata checks. A partially published or
uncertain delivery requires recovery first. Base drift still blocks subsequent
implementation; a decision does not automatically rebase it. Resolving does not
edit GitHub or renew the budget. During
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
issue is closed. Changed captured inputs, operator decisions, or admission
invalidate the roadmap for new work; base changes also require current-base
planning. Eligible unchanged chunks may be reused under the conditions above;
the updated backlog still receives global synthesis. Owned-PR delivery and
feedback follow-up can continue while a replacement roadmap is incomplete,
within their existing authority.

Existing states without a roadmap receive global assessment on their next
authorized runnable pass. Old detailed plans are replanned against the current
head before implementation; accepted work, owned PRs, operator decisions and
spent limits remain recorded. Upgrading does not revive stopped or exhausted
batches or replay unknown work. For an older batch that already exhausted its
20-call limit, inspect retained work and delivery effects before initializing a
separate new state. Use explicit recovery where applicable; recovery does not
reset limits or clear a recorded stop.

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
| `github.maintain_pull_requests` | `true` | Follow CI and trusted feedback on owned PRs, without widening merge permission. |
| `github.review_authors` | `[]` | Additional exact GitHub reviewer logins trusted as feedback sources. |
| `queue.numbers` | `[]` | Empty selects all captured open issues; otherwise select these open issue numbers. |
| `queue.exclude_labels` | `["needs-decision", "needs-human"]` | Put matching issues aside for a decision. |
| `queue.batch_size` | `10` | Maximum distinct issues implemented per run. |
| `queue.max_issues` | `500` | Refuse a larger selected backlog rather than silently truncate it. |
| `queue.max_delivery_attempts` | `2` | Maximum implementation deliveries per issue, including CI repairs. |
| `queue.planning_max_bytes` | `131072` | Maximum serialized planning prompt bytes, including authority and context; integer from `8192` to `1048576`. Host protocol and schema overhead are additional. Oversized inputs cannot activate a partial roadmap. |
| `queue.planning_workers` | `3` | Concurrent chunk assessment ceiling; integer from `1` to `8`. `1` is serial. A backlog fitting one prompt still uses one roadmap call. |
| `queue.planning_context` | `""` | Operator context for the current milestone or release objective; at most 20,000 characters. It grants no additional authority. |

## Claude Code and verification boundaries

Set `driver` to `{"name": "claude"}` to use the native Claude Code adapter.
Committed `CLAUDE.md` and other repository instructions are available in the
managed worktrees. Each phase launches a fresh host process; it does not resume
your existing interactive conversation. Loading your installation's automatic
memory has not been verified. Keep essential instructions and check commands
in committed project files and the operator configuration.

For your current local coding conversation, set `driver` to
`{"name": "desktop"}` and use the [desktop handoff guide](desktop.md).
`issues_create`, `issues_start`, `issues_status`, `issues_stop`, `issues_events`,
`issues_recover`, and `issues_resolve` control issue batches over MCP.
`desktop_next`, `desktop_submit`, and `desktop_cancel` transfer individual
phases; DutyLoop retains operator verification, commits, and configured delivery.
No separate model CLI login is required for desktop execution. The host must
stop its own phase work before submitting or cancelling, and review must be
claimed by an actual fresh context or subagent. Actual desktop app execution
and review isolation have not yet been verified.

Issue queue and delivery verification currently uses offline fixtures and
simulated hosts/GitHub responses. No authenticated real-project batch establishes
end-to-end Claude execution, real CI/merge behavior, or maintenance effectiveness.
Host processes retain their usual filesystem, network, and credential access;
worktree scope controls acceptance and is not a sandbox. See
[limits and hosts](limits-and-hosts.md) and [validation](validation.md).
