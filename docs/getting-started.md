# Your first project mission

A mission delegates an ongoing responsibility. Tasks are discovered during the
run; you do not need to supply an initial task list or approve each ordinary
step. Start with a narrow product area and verification you already trust.

## Prerequisites

- Python 3.11 or newer and Git on `PATH`.
- A repository with at least one commit and a configured Git author identity.
- For native execution, an installed coding CLI with working authentication and
  model access. Your [current local desktop agent](desktop.md) can instead
  perform phases through MCP without another model CLI login.
- Dependencies and checks that work in a fresh worktree. DutyLoop does not copy
  a local virtual environment, ignored files, or uncommitted changes into it.

Install from a clone, preferably in a virtual environment:

```console
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` on Windows PowerShell or
`source .venv/bin/activate` on Linux/macOS. Then install:

```console
python -m pip install -e .
dutyloop --version
```

Use `dutyloop demo --directory ../dutyloop-demo` for the offline scripted
demonstration before connecting a model. Its driver is explicitly simulated.

This page continues with native CLI execution. For desktop handoffs, install
the MCP extra with `python -m pip install -e ".[mcp]"`, then follow
[desktop setup](desktop.md) and use its example spec and host prompt.

## 1. Define the responsibility and authority

A useful mission says what to improve, what counts as evidence, and what must
remain true. For example:

> Maintain the reliability of the CSV import/export module. Find reproducible
> failures against documented behavior and fix them with regression coverage.
> Preserve the public API and file format. Wait when there is no evidenced work.

“Improve this whole product” leaves business priorities unspecified. Market
positioning, changing pricing, or deciding which users to serve needs direction
from the owner. A committed feedback file can be useful input; its contents
remain evidence to assess, not instructions that grant the agent more authority.

Copy [the example spec](../examples/mission.json) to `mission.json` outside the
target repository. Replace its project path and verification command. The
[configuration reference](configuration.md) explains every field.

Alternatively, generate a validated starter file. The following is a POSIX-shell
example; when shell quoting differs, editing the JSON file is equivalent:

```sh
dutyloop configure --project /absolute/path/to/project --output mission.json --mission "Maintain CSV import/export reliability against documented behavior." --allow-path src --allow-path tests --verify-json '["python","-m","pytest","-q"]' --driver codex --max-invocations 12 --max-wall-seconds 1800
```

Each `--allow-path` adds an accepted path prefix; each `--verify-json` adds one
argv array. `configure` refuses to overwrite an existing file and does not create
state, a branch, or a running agent. Edit the generated file for constraints,
permitted kinds, or a larger discovery team.

Choose the model through `driver.model` or `configure --model` only if needed;
otherwise the native CLI's selection applies. Model availability depends on that
CLI and your account. No global model setting is changed.

## 2. Check before starting

```console
dutyloop doctor --driver codex --project /absolute/path/to/project
dutyloop validate --spec mission.json --state ../mission-state
```

`doctor` inspects the executable/version and optional Git project metadata. It
does not call a model, check authentication, or establish quota. Its JSON can
contain an unavailable-driver diagnostic even when the diagnostic command itself
exits normally. Read the result.

`validate` returns the normalized spec and warnings without creating a mission
or branch. The target must be the exact Git repository root with a committed
`HEAD`. It warns about uncommitted changes because discovery starts from `HEAD`.
Use a new branch name, or omit `branch` to let DutyLoop choose a unique one.

Verification argv runs in the task worktree. Resolve tool paths correctly: an
absolute Python path can select a preinstalled environment; `python` uses the
runner's `PATH`. The project still needs any required build assets or dependencies.
Keep decisive acceptance checks outside writable paths when possible.

## 3. Initialize, then run

```console
dutyloop init --spec mission.json --state ../mission-state
dutyloop run --state ../mission-state
```

`init` creates durable state and a mission branch at the current committed
`HEAD`. It does not invoke a model. `run` blocks until the runner waits, works,
and eventually reaches a terminal condition. Waiting is part of the loop; it
does not return immediately just because one inspection found no work.

For a detached process, use `start` instead of `run`:

```console
dutyloop start --state ../mission-state
dutyloop status --state ../mission-state --human
dutyloop events --state ../mission-state --limit 20
```

The parent command returning `starting` only confirms process launch. Read status
to see whether the runner actually began, was blocked, or reached a limit. A
mission has one runner; launching another does not create a second team.

## 4. Inspect the output

Read the JSON snapshot for `branch`, `accepted_head`, candidate rationale,
acceptance criteria, task checks, review findings, and call output directories:

```console
dutyloop status --state ../mission-state
git -C /absolute/path/to/project log --oneline work/project-maintenance
git -C /absolute/path/to/project show work/project-maintenance
```

The example spec uses `work/project-maintenance`; substitute the actual branch
from status. `git show` inspects the latest accepted commit. Inspect every
accepted commit you intend to adopt, not only the last one. For an overall diff,
compare the recorded initial base from the `initialized` event to that branch:

```console
git -C /absolute/path/to/project diff BASE_SHA...MISSION_BRANCH
```

You can test the accepted branch in a separate review worktree, provided the
chosen directory does not already exist:

```console
git -C /absolute/path/to/project worktree add --detach ../mission-review MISSION_BRANCH
```

The runtime leaves publication and integration to you. Merge or cherry-pick the
reviewed commits through your normal project process. State can also contain
rejected work; a worktree's existence does not mean its changes were accepted.

## 5. Stop and diagnose

```console
dutyloop stop --state ../mission-state
dutyloop status --state ../mission-state --human
```

Stopping is asynchronous. Check `runner_pid`, stop state, and cleanup results;
do not treat a returned stop request as proof that all effects were reversed.
A stop is latched. Starting the same mission again does not clear it.

For missing executables, authentication failures, failed checks, interruptions,
or budget exhaustion, see [operations and troubleshooting](operations.md).
