# Use your current desktop agent

Set `driver` to `{"name": "desktop"}` to have your current local coding
conversation perform DutyLoop's planning, discovery, implementation, and review
phases. DutyLoop supplies work through MCP and retains verification, acceptance,
commits, and configured issue delivery. It does not launch a model CLI, select a
model, or require a separate model CLI login. The desktop host retains its usual
account, permissions, context, and usage limits.

This is a host-driven handoff: your conversation calls DutyLoop tools and does
the work. Connecting the server, reading status, or creating state does not start
model work. A standard MCP server does not wake an idle desktop conversation or
borrow its model for a background process.

## Install and connect locally

Use Python 3.11+, Git, a configured Git author identity, and an existing local
coding host. Issue mode also needs an authenticated GitHub CLI (`gh`). From the
DutyLoop checkout, install into the Python environment the host can launch:

```console
python -m pip install -e ".[mcp]"
python -m dutyloop desktop-config --client codex --root /absolute/path/to/dutyloop-states --allow-project /absolute/path/to/project
python -m dutyloop desktop-config --client claude --root /absolute/path/to/dutyloop-states --allow-project /absolute/path/to/project
```

Run the command for your client. It prints configuration only; it does not edit
host settings, create credentials, start a batch, or call a model. It uses the
current Python executable and `-m dutyloop mcp`, so keep that environment
available. Use absolute paths; Windows paths can use `C:/work/project`. Repeat
`--allow-project` for additional exact repository roots. Keep the state root
outside all registered repositories.

For **Codex desktop**, merge the generated TOML into the host's MCP configuration
or use Settings → MCP servers → Add server and choose STDIO with the generated
command and arguments. Save and restart as prompted. The official
[MCP guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) describes shared
desktop, CLI, and IDE configuration. Installing or authenticating the standalone
model CLI is not part of this handoff setup.

For **Claude Desktop's local Code tab**, merge the generated `mcpServers` JSON
with existing entries in `claude_desktop_config.json`, or use the Code project's
`.mcp.json`. Do not replace unrelated configuration. Claude's
[desktop reference](https://code.claude.com/docs/en/desktop#mcp-servers-from-the-claude-desktop-chat-app)
documents both configuration sources for local Code sessions. The Code tab
[includes Claude Code](https://code.claude.com/docs/en/desktop-quickstart), without
a separate CLI installation. A packaged `.mcpb` desktop extension is another
[supported local installation route](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop);
DutyLoop's command emits JSON rather than building an extension.

Use a local coding session that can access both the managed checkout and state
directory. A cloud session or Claude's remote custom connector does not expose
your Windows filesystem: remote custom connectors originate from
[Anthropic's cloud](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp#network-requirements).
WSL and remote environments use their own executables and paths; configure and
verify those separately. If the host cannot open the returned `cwd`, stop before
claiming work in a different checkout.

## Start an issue batch

For ongoing intake, use the [repository workflow](repository.md) and
`repository_create`, `repository_start`, `repository_status` and
`repository_attention`. The same desktop phase tools can use `kind="repository"`;
`kind="issues"` remains compatible with repository-backed issue state. Check the
repository's observation/automatic mode before starting. An active host must
continue claiming phases; the coordinator cannot wake the conversation.

Copy [desktop-issues.json](../examples/desktop-issues.json) outside the target
repository. Replace its project, GitHub repository, scope, and verification
command. The repository needs a committed `HEAD`; fresh worktrees do not copy
uncommitted source or ignored local dependencies. Keep `driver.name` as
`desktop` and review `github.delivery` before starting.

Paste the following into the connected local coding conversation, replacing the
spec path and batch name. It authorizes the configured batch; the example spec
keeps delivery local.

```text
Use DutyLoop for the issue batch maintenance-issues. Read the JSON spec at
/absolute/path/to/desktop-issues.json and call issues_create(name="maintenance-issues",
spec=<that JSON object>), then issues_start(name="maintenance-issues").

Use a stable worker_id for this worker and a distinct context_id for each actual
host context. Call desktop_next(name="maintenance-issues", worker_id=<worker>,
context_id=<context>, kind="issues"). If no request is ready, read issues_status,
wait briefly, and poll again while the runner is active. Keep roadmap stage
gates and per-issue decisions visible. Continue only eligible work in the current
stage. Stop polling at a terminal
batch state or when the batch cannot progress without an operator decision.

For concurrent read-only assessment requests, use separate actual workers or
contexts, each with its own worker_id and context_id and its own claimed request.
A single worker can instead service requests serially within their deadlines.

For each request, follow its prompt and schema at its exact cwd. Respect its
deadline and scope. After implementation, have a fresh subagent or conversation
context call desktop_next with its own identity to claim review. Never claim
review using a context that has implemented work. Finish or stop every
shell, background task, and subagent for the request before submitting. Call
desktop_submit with name, kind="issues", request_id, token, the claiming
worker_id/context_id, a payload matching the request schema, and work_stopped=true.
If you cannot complete a request, stop all its work and call desktop_cancel with
the same identity and work_stopped=true. Let DutyLoop verify, commit, and deliver
accepted work. Continue until the batch is terminal or cannot progress without
my decision.
```

`issues_create` captures GitHub issue evidence and initializes local state.
`issues_start` starts the supervisor; with the desktop driver it waits for phase
results instead of starting another model. Start returns before the batch ends.
Use `issues_status` and `issues_events` to follow progress. Read the
[issue workflow](issues.md) for dependencies and human decisions.

For an initial planning pass, call `issues_start(name=..., plan_only=true)`.
The host receives read-only discovery requests with `planning_operation` set to
`roadmap` or `assess`: inspect the current repository stage and all supplied
backlog sources, then return the request's schema. Complete bounded chunks cover
captured issue bodies and comments before synthesis; report uncovered inputs
instead of silently truncating them. This creates a global phased roadmap only,
not detailed plans for every issue. Review coverage, stage goals, entry and exit
criteria, human gates, questions and deferrals in `issues_status`. Use
`queue.planning_context` for the operator's current milestone objective and
`queue.planning_max_bytes` for the request bound. These are shared with native
execution and do not expand authority.

Chunk assessments use a concurrency ceiling of `queue.planning_workers`
(default `3`, integer `1`–`8`; `1` is serial). A backlog fitting one prompt keeps
the single-call roadmap path. Multiple desktop workers need distinct
`worker_id` and `context_id` values representing their actual host contexts;
changing identity strings in one context does not create concurrent workers.
One host worker may service the queued requests serially, subject to the shared
wall deadline. Native adapters dispatch concurrent assessments automatically.
Every chunk uses the same frozen base. Out-of-order completions remain bound to
their source chunks; synthesis waits for complete coverage. Status exposes
`planning.workers`, `planning.chunks` and `planning.child_states` for progress.
Parallel assessment does not change the `work_stopped` host declaration or give
DutyLoop control of desktop-owned tools. Stop and join those tools before
submitting or cancelling each request.

A detailed planner's question, failed attempt or dependency wait does not by
itself restart whole-backlog assessment. DutyLoop keeps the roadmap and checks
live issue states and stage barriers before selecting work. For genuine source,
admission or operator-decision changes, completed chunks may be reused only when
all their bindings and the base, authority and milestone context still match;
the host receives assessment requests for changed chunks and a fresh global
synthesis request. Changed base, authority or milestone context requires fresh
chunk assessment.
Inspect `planning.reused_assessments` and `planning.reused_issues` for the reused
coverage. This preserves the original budget and does not waive human gates.

Implementation waits for a later `issues_start(name=...)` without that flag and
a complete current roadmap. Then only the next eligible issue receives a detailed
plan on the current base before implementation, checks and separate review.
An incomplete earlier stage blocks later stages. Existing owned-PR feedback and
delivery follow-up can continue while a replacement roadmap is incomplete.
The CLI equivalent for roadmap-only work is
`dutyloop issues start --state /absolute/path/to/state --plan-only`.

For a standing mission, use [desktop-mission.json](../examples/desktop-mission.json),
`mission_create` and `mission_start`, and pass `kind="mission"` to every desktop
tool. Monitor with `mission_status` and `mission_events`. Omitted `kind` means
`issues`; always supply it explicitly when switching between workflows.

## Phase contract

| Tool | Required handoff inputs | Effect |
| --- | --- | --- |
| `desktop_next` | `name`, `worker_id`, `context_id`; optional `kind` | Claim one pending phase, or report that none is ready. |
| `desktop_submit` | `name`, `request_id`, `token`, `worker_id`, `context_id`, `payload`, `work_stopped`; optional `kind` | Submit the claiming worker's result for validation. |
| `desktop_cancel` | The same identity and `work_stopped`, without `payload`; optional `kind` | Cancel the claimed phase after host work has stopped. |

A request supplies `request_id`, `token`, `phase`, `prompt`, `cwd`, `schema`,
`deadline`, and `output_dir`. Treat the token as private, temporary claim
authority. Keep the request's identity unchanged on submit/cancel. Do not submit
for another worker or put tokens in public artifacts. Follow the returned
schema rather than assuming all phases accept the same JSON. For example, an
implementation payload contains `summary` and `completed`; a review payload
contains `accepted`, `summary`, and `findings`.

Execute in the returned managed `cwd`, even if your conversation opened the
original project folder. Keep all phase work inside the authorized scope, and
leave commit creation and GitHub delivery to DutyLoop. The supervisor runs the
operator's verification commands and checks the staged snapshot before accepting
a change. A worker's `completed: true` does not establish acceptance.

Review needs an actual fresh subagent or new context to claim the phase, with a
different context identity from every prior implementation context. If your
host cannot provide that, stop and report
the limitation. Changing a `context_id` string in the same conversation does
not create isolation. Identity and `work_stopped` are host declarations, not
proof of independent review or process termination. Audit events label this
cleanup evidence `host_declaration`; native child cleanup uses
`managed_process` evidence.

## Stop and recover

Request `issues_stop(name=...)` or `mission_stop(name=...)`, then stop and join
all shells, background tasks, and subagents belonging to active requests in the
desktop host. DutyLoop can cancel its own runner and managed verification
processes; it cannot terminate processes owned by your desktop conversation.
Only declare `work_stopped: true` after that work has ended. Cancel any still
active claim, then inspect status, events, and the retained worktree.

If a claimed request expires or its result is uncertain, it is quarantined as
unknown rather than automatically handed to another worker. Do not keep editing
after expiry or retry an old submission. Inspect the retained work and ensure
all host work has stopped, then use the existing explicit recovery flow:
`issues_recover(name=..., acknowledge=true)` or
`mission_recover(name=..., acknowledge=true)`. Recovery invalidates old claims;
it does not renew limits, clear a stop latch, or replay uncertain work.
`issues_resolve` records an operator decision for an eligible issue; it does not
authorize guessing missing business intent. See [operations](operations.md) and
[issue recovery](issues.md) before continuing.

If a batch is blocked while creating a checkout before any phase was dispatched,
there may be no desktop claim to cancel. Inspect its retained preparation
receipt and child state, then follow the
[same-batch preparation recovery](issues.md#workspace-preparation-failures)
flow. Use `issues_recover(name=..., acknowledge=true)` for a finite batch or
`repository_recover(name=..., acknowledge=true)` for repository mode, inspect
the returned state, and start that same batch only if it is `ready`. Proven
zero-dispatch recovery retains valid completed global assessments and decisions;
it does not renew the deadline or call budget. A claimed, expired or uncertain
request still requires the host cleanup and quarantine handling above. Changing
the worker or context identity does not turn it into a preparation failure.

## Limits and evidence

Desktop phase reservations count against `max_invocations`. They count handoffs,
not model requests, tokens, subagents, or dollars. DutyLoop cannot measure or cap
the current conversation's model usage; missing usage and cost remain unknown.
Wall time includes waiting for the host. A deadline prevents acceptance of late
work and does not guarantee termination of desktop-owned work.

Issue delivery still defaults to local. PR publication and merging require the
existing explicit configuration; merge mode retains its required-check and
branch-protection gates. Standing missions remain local. Native CLI drivers keep
their existing launch behavior when selected.

Official host documentation establishes configuration paths, not a successful
DutyLoop integration test. Actual Codex/Claude desktop setup, permissions,
review isolation, interruption, and end-to-end phase execution have not yet
been verified in the apps. See the [verification record](validation.md) and
[operational limits](limits-and-hosts.md).
