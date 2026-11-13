# MCP control

The optional local stdio MCP server controls the same mission and issue engines
as the CLI. With `driver.name: desktop`, your current coding conversation can
also claim and perform phases. Native drivers still launch their configured
coding CLI. MCP connectivity alone does not establish execution compatibility
with a host.

## Install and register

From the source checkout/environment used for DutyLoop:

```console
python -m pip install -e ".[mcp]"
python -m dutyloop desktop-config --client codex --root /absolute/path/to/dutyloop-states --allow-project /absolute/path/to/project

dutyloop mcp --root /private/mission-states --allow-project /absolute/path/to/project
```

`desktop-config` prints Codex TOML; use `--client claude` for `mcpServers` JSON.
It does not write host settings or start work. Merge the output with your
client's existing configuration. It launches the current Python executable with
`-m dutyloop mcp`; keep that environment available. See the
[desktop guide](desktop.md) for host setup, specs, and a worker prompt. Run the
`mcp` command yourself only when inspecting the stdio server directly.

The command serves stdio; a terminal waiting for protocol input is expected.
Usually the MCP client launches it. `--allow-project` can be repeated for several
exact repository roots. The state root must stay outside every allowed project.
The server creates named mission state immediately below that root.

For clients that accept an `mcpServers` JSON configuration, use a client-local
entry like this, substituting real paths:

```json
{
  "mcpServers": {
    "dutyloop": {
      "command": "/absolute/path/to/venv/bin/dutyloop",
      "args": [
        "mcp",
        "--root", "/private/mission-states",
        "--allow-project", "/absolute/path/to/project"
      ]
    }
  }
}
```

On Windows, `command` can be `C:\\tools\\dutyloop-venv\\Scripts\\dutyloop.exe`,
and JSON path strings need escaped backslashes. Another portable launch form is
an absolute Python executable with `-m`, `dutyloop`, and the arguments above.
Client configuration formats and trust prompts differ; use your client's own
supported configuration location. DutyLoop does not change it automatically.

## Tools

| Tool | Inputs | Effect |
| --- | --- | --- |
| `mission_create` | `name`, `spec` | Validate and initialize; does not start an agent. |
| `mission_start` | `name` | Start the background runner, including authorized local verification commands. |
| `mission_status` | `name` | Read current state. |
| `mission_events` | `name`, optional `after` and `limit` | Read event history with a cursor. |
| `mission_stop` | `name` | Request asynchronous cancellation. |
| `mission_recover` | `name`, `acknowledge` | Explicitly reconcile uncertain mission work. |
| `issues_create` | `name`, `spec` | Capture issue evidence and initialize a batch without model work. |
| `issues_start` | `name` | Start the background issue runner, including configured delivery. |
| `issues_status` | `name` | Read batch state. |
| `issues_events` | `name`, optional `after` and `limit` | Read batch events with a cursor. |
| `issues_stop` | `name` | Request asynchronous cancellation. |
| `issues_recover` | `name`, `acknowledge` | Explicitly reconcile uncertain issue work without replaying it. |
| `issues_resolve` | `name`, `number`, `decision` | Record an operator decision against current issue evidence. |

Names are lowercase hyphenated slugs up to 64 characters; reserved Windows
device names are rejected. The selected project must exactly match a registered
root. With one registered project, `spec.project` may be omitted; with several,
choose one explicitly. MCP permits supported native drivers and the desktop
driver, not arbitrary custom-command argv. A desktop driver is exactly
`{"name": "desktop"}`; it does not accept model selection. Mission creation
keeps the native Codex default; issue creation defaults to desktop if no driver
is supplied. Set the driver explicitly in reusable specs. The regular
[mission](configuration.md) and [issue](issues.md) spec contracts still apply.

A minimal tool request has this shape; replace the path and checks:

```json
{
  "name": "library-reliability",
  "spec": {
    "mission": "Maintain parser reliability against documented behavior. Fix reproducible failures with regression coverage; wait when evidence is insufficient.",
    "project": "/absolute/path/to/project",
    "allowed_paths": ["src", "tests"],
    "verification": [["python", "-m", "pytest", "-q"]],
    "driver": {"name": "codex"},
    "limits": {"max_invocations": 12, "max_wall_seconds": 1800}
  }
}
```

After creation, inspect the result, then call `mission_start` with the same name.
For an issue batch, use `issues_create` and `issues_start`. Creation is not proof
work ran. Start returns launch information; read status/events to observe the
runner. A native driver can invoke its model, while a desktop driver queues
phases for your conversation. Both can execute operator-authorized verification
commands and project code. After stopping, poll status and inspect cleanup
evidence before assuming work ended.

`mission_events` uses the same cursor response as the CLI: `events` and
`next_cursor`. `after` defaults to 0 and must be nonnegative; `limit` defaults
to 20 and must be between 1 and 1000.

Issue events use the same cursor contract. Recovery requires
`acknowledge: true`, preserves used budgets, and does not clear stop/limit
states. Review [operations](operations.md) and [issue recovery](issues.md)
before invoking it.

## Desktop phase tools

Repository maintenance uses `repository_create`, `repository_start`,
`repository_status`, `repository_reconcile`, `repository_stop`,
`repository_events`, `repository_recover`, `repository_resolve`,
`repository_attention` and `repository_set_mode`. Creation and reconciliation do
not invoke models or publish changes. `repository_set_mode(name, mode="auto")`
explicitly authorizes incremental execution within the stored scope and delivery
policy, without replenishing limits. The default mode is `observe`.
See [repository maintenance](repository.md) before enabling it. Finite issue
controls reject repository state so they cannot bypass observation policy.

| Tool | Inputs | Effect |
| --- | --- | --- |
| `desktop_next` | `name`, `worker_id`, `context_id`, optional `kind`, `wait_seconds` | Claim one phase; optionally wait for work, control closure or the existing deadline. |
| `desktop_submit` | `name`, `request_id`, `token`, `worker_id`, `context_id`, `payload`, `work_stopped`, optional `kind` | Validate and record the claimed request's result. |
| `desktop_cancel` | The same claim identity and `work_stopped`, without `payload`; optional `kind` | Cancel a claimed request after host work has ended. |

`kind` defaults to `issues`; use `mission` for standing missions. Requests
provide `request_id`, `token`, `phase`, `prompt`, `cwd`, `schema`, `deadline`,
and `output_dir`. Work at the exact managed `cwd`, preserve the claiming
identity, and submit only a payload matching that request's schema. Complete or
stop all phase shells, tasks, and subagents before declaring
`work_stopped: true`. Review needs a fresh host context, not a renamed identity
in the implementation context. See the [desktop contract](desktop.md#phase-contract).

`wait_seconds` defaults to `0`; active workers should use `20` to avoid repeated
empty tool calls. It accepts finite numbers from `0` to `30`. The bounded wait
does not block submissions or stop tools on the server event loop, and preserves
claim ownership, project allowlisting, fresh review contexts and stored limits.

An active request with an unknown or expired outcome is quarantined rather than
automatically reassigned. Stop external host work, inspect the worktree, and use
explicit recovery. Recovery invalidates old claims. DutyLoop cannot stop
desktop-owned processes, attest reviewer isolation, wake an idle conversation,
or meter its model usage. Local protocol coverage does not establish actual
desktop app execution.

## Authority and evidence

Access to creation/start authorizes local command execution when a mission runs:
its verification argv, project scripts, host tools, and hooks execute with the
runner's environment authority. Issue start also authorizes configured GitHub
delivery: local by default, with publication and merge separately configured.
Restrict which clients can use this server and which projects they can control.

MCP project allowlisting is a control-plane boundary. It is not a filesystem,
network, or credential sandbox for child processes. State and tool responses can
contain private project data. See [operational limits](limits-and-hosts.md).

The official SDK stdio tests establish protocol communication. They do not
certify installation, authentication, or unattended operation in every client.
