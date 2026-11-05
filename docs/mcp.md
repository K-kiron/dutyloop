# MCP control

The optional MCP server lets a configured client control the same local mission
engine as the CLI. It is a control interface; it does not install host adapters
into every development tool or establish universal orchestration compatibility.

## Install and register

From the source checkout/environment used for DutyLoop:

```console
python -m pip install -e ".[mcp]"
dutyloop mcp --root /private/mission-states --allow-project /absolute/path/to/project
```

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

Names are lowercase hyphenated slugs up to 64 characters; reserved Windows
device names are rejected. The selected project must exactly match a registered
root. With one registered project, `spec.project` may be omitted; with several,
choose one explicitly. Mission creation/start permits native drivers only, not
arbitrary custom-command argv. The regular [spec contract](configuration.md)
still applies.

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
Creating a mission is not proof it ran. Starting returns launch information;
read status/events to observe the runner. After stopping, poll status for actual
termination and inspect cleanup results.

`mission_events` uses the same cursor response as the CLI: `events` and
`next_cursor`. `after` defaults to 0 and must be nonnegative; `limit` defaults
to 20 and must be between 1 and 1000.

## Authority and evidence

Access to creation/start authorizes local command execution when a mission runs:
its verification argv, project scripts, host tools, and hooks execute with the
runner's environment authority. The native-driver restriction does not make
arbitrary verification commands harmless. Restrict which clients can use this
server and which projects they can control.

MCP project allowlisting is a control-plane boundary. It is not a filesystem,
network, or credential sandbox for child processes. State and tool responses can
contain private project data. See [operational limits](limits-and-hosts.md).

The official SDK stdio tests establish protocol communication. They do not
certify installation, authentication, or unattended operation in every client.
