# Operations and troubleshooting

## Commands and output

| Command | Effect |
| --- | --- |
| `configure` | Write a validated starter spec; no model call, state, or branch. Existing output files are not overwritten. |
| `validate` | Read-only spec, path, and project checks; returns normalized spec and warnings. |
| `doctor` | Check executable/version and optional project metadata; no model/authentication call. |
| `init` | Create state and a new mission branch, without running an agent. |
| `run` | Run in the foreground until stopped, limited, or blocked. |
| `start` | Launch a detached runner and return launch information. |
| `status` | Read snapshot JSON; `--human` gives a compact terminal summary. |
| `events` | Read ordered lifecycle/evidence events with a cursor. |
| `stop` | Persist an asynchronous cancellation request. |
| `recover --acknowledge` | Explicitly record interrupted work as unknown; never replay it or refund calls. |
| `demo` | Run the offline scripted-driver demonstration. |
| `mcp` | Serve optional local control tools over stdio. |

Use `dutyloop COMMAND --help` for exact options. Commands return JSON by default,
except `status --human`, version/help output, and MCP protocol traffic. A returned
snapshot may describe `blocked` or `limited` even when the CLI exits 0: a
successful control operation is distinct from a successful mission. Invocation
or validation errors exit 1; malformed command-line arguments exit 2.

## Progress and events

```console
dutyloop status --state ../mission-state --human
dutyloop status --state ../mission-state
dutyloop events --state ../mission-state --after 0 --limit 20
```

The event response contains `events` and `next_cursor`. Each event contains an
integer `id`, a Unix timestamp `at`, a `kind`, and `data`. Save `next_cursor` and
pass it as `--after` to read later events. An empty page returns the same cursor.
`--after` must be a nonnegative integer and `--limit` must be between 1 and 1000.
Event data can include project evidence; avoid copying it into public reports
without reviewing it.

The full status includes the mission/driver, branch and accepted commit,
invocation usage, runner PID, task results, and per-call output directories.
`reported_cost_usd` sums only known reports. Check `calls_without_cost`; an
unknown call is not a zero-dollar call.

## Lifecycle states

| State | Interpretation | Usual next action |
| --- | --- | --- |
| `ready` | Initialized or explicitly recovered; not currently running. | Run/start if the stored authority remains appropriate. |
| `running` | Runner is discovering, implementing, checking, or reviewing. | Inspect progress or request stop. |
| `waiting` | No supported work in the last inspection. | Allow the next poll or stop; waiting still consumes wall time. |
| `stopping` | Stop requested while a runner was recorded. | Poll status for cleanup/completion. |
| `stopped` | Explicit stop or interruption was settled. | Inspect output; a latched stop cannot be cleared by start. |
| `limited` | Invocation/time limit reached, or too few calls remain for another implementation/review pair. | Inspect output; create a new mission if more work is authorized. |
| `blocked` | A decision, failed checks, uncertain cleanup, or inconsistent state needs attention. | Read `reason`, events, and artifacts before acting. |

The reason field narrows the diagnosis. Normal tasks do not require individual
approval; unresolved product direction or host access can still require the
operator to intervene. Repeated `wait` inspections are not errors and can consume
the remaining invocation budget without producing a commit.

## State and artifacts

The state directory currently contains:

| Location | Purpose |
| --- | --- |
| `mission.sqlite3` | Durable mission, call, task, and event records. |
| `runner.lock` | Exclusive process-level runner ownership. Its presence alone does not mean a process is alive. |
| `runner.stdout.log`, `runner.stderr.log` | Background-runner output when launched with `start`. |
| `calls/<id>/` | Driver request/output artifacts and bounded process logs. |
| `checks/` | Operator verification stdout/stderr for task attempts. |
| `worktrees/` | Implementation attempts, including rejected or interrupted work. |
| `observations/` | Discovery worktrees; clean completed observations are removed, uncertain ones are retained. |
| `git-operations/` | Managed Git command output. |

Use status for specific worktree/output paths rather than assuming directory
names are a stable API. State format compatibility across releases is not
guaranteed yet. Back up a stopped mission's state if needed; do not publish raw
state or transcripts. Cleanup/removal is manual: remove registered worktrees
through Git after inspection, then archive the private state. Deleting the state
directory alone leaves Git worktree registrations behind.

## Interruption and recovery

1. Read status and recent events. Inspect unfinished call/check logs and retained
   worktrees. Confirm no orphaned worker is still running.
2. If integration branch and recorded accepted commit agree, acknowledge the
   uncertainty explicitly:

   ```console
   dutyloop recover --state ../mission-state --acknowledge
   ```

3. Inspect the returned state before another `run`. Unknown tasks are quarantined;
   the supervisor discovers new work instead of blindly replaying the attempt.

Acknowledgment does not restore calls or wall time. It does not clear an explicit
stop request. A limited mission has no fresh budget to recover. If Git and
SQLite disagree about the accepted commit, automatic recovery refuses to repair
the mismatch. Inspect both histories and initialize a new mission from the
commit you choose.

## Common problems

| Symptom | Check | Resolution |
| --- | --- | --- |
| `dutyloop` command missing | The environment where the package was installed. | Activate that environment or use `python -m dutyloop`. |
| Driver unavailable or unsupported shim | `doctor`, `PATH`, executable type. | Install the host through its official setup or supply a supported executable wrapper. |
| Authentication/model error after doctor succeeds | The host's own login, account, model access, child stderr, and `driver_auth` / `driver_config` / `driver_quota` reason. | Fix host setup outside DutyLoop; doctor deliberately does not authenticate. Initialize a new mission after inspection; ordinary retries do not solve these blockers. |
| Verification cannot import dependencies | Fresh-worktree environment and executable path. | Prepare a reproducible environment and use the correct executable in verification argv. |
| Mission sees old code | Recorded base `HEAD` vs uncommitted operator edits. | Commit intended inputs and initialize a new mission. |
| `State directory must be outside...` | Relationship of resolved project/state paths. | Select a sibling/private state directory; neither may contain the other. |
| Branch already exists | `git branch --list` and spec `branch`. | Use a new branch or omit the field for a unique generated name. |
| No accepted changes | Candidate rejection, failed verification, rejected review, or no useful evidence. | Inspect events/task feedback. Improve the mission/checks only through a new explicit mission. |
| `unclean_interruption` | Unsettled runner, call, or verifying task. | Inspect artifacts and acknowledge recovery; do not delete the lock to steal ownership. |
| `integration_ref_changed` | Someone or a child changed the shared branch ref. | Inspect Git history; select a trustworthy commit and initialize a new mission. |
| Cleanup unconfirmed | Call/check result and platform process permissions. | Investigate surviving processes; do not automatically retry uncertain work. |
| Not enough calls to start another task | Every discovery/retry/review counts separately; an implementation needs room for its review. | Inspect the completed work and unstarted candidate; start a new appropriately budgeted mission only after inspection. |

Include sanitized `--version`, OS, Python/host versions, mission fields, status
reason, and a minimal reproduction in a bug report. Never attach credentials,
private transcripts, or a full state database.
