# Contributing to DutyLoop

Useful contributions improve a specific user workflow or make a documented
control contract more dependable. Report a reproducible problem, discuss a
concrete integration gap, or send a focused pull request. For a new host adapter,
establish the host's actual interface before claiming compatibility.

## Local development

Use Python 3.11 or newer and an existing Git installation. Create a virtual
environment, then run from the source checkout:

```console
python -m pip install -e ".[dev,mcp]"
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src
python -m build
python scripts/verify_distribution.py
```

The optional MCP dependency is included above so its stdio tests run rather than
being skipped. Tests that create commits configure fixture-local Git identity;
do not change your global identity for them. Platform process-tree behavior is
configured for Windows, Ubuntu, and macOS CI. A local run does not replace the other
platform's result.

Use `python -m dutyloop` when testing from an environment without the installed
console script. Test the packaged path with an installation of the built wheel
and `dutyloop demo --directory` in a new or empty directory.

## Evidence for changes

Tests must use local fixtures or explicitly simulated drivers by default. Do
not make authenticated model calls or require production credentials in CI.
Label live host checks separately, record the host version and permission mode,
and keep raw state and transcripts outside the repository.

Changes to dispatch, stop, limits, acceptance, or recovery need behavior tests
that demonstrate the failure before the fix. Include concurrency or interruption
cases when those contracts change. Keep unknown usage unknown and preserve
evidence of rejected or interrupted work.

For a native adapter change, include the tested executable/version, phase launch
settings, final-output parsing, cancellation behavior, missing-usage handling,
and offline protocol fixtures. A `--version` probe is not a successful model
invocation. Label authenticated live checks separately and record their scope;
do not present an offline mock as live host support.

Keep README tables, CLI examples, spec reference, and MCP allowlists aligned
when adding an adapter or command. Update both README language versions for
user-visible changes. Source/docstrings and the main documentation use English;
the Chinese README is an explicit translated entry point.

## Review and reporting

Explain the user-visible problem, final behavior, and checks run in a PR. Include
material limits or unverified platforms. Small changes need small explanations;
dispatch, acceptance, limits, recovery, and process cleanup changes need enough
evidence to assess their failure modes.

Bug reports should include DutyLoop/Python/OS/host versions, a minimal sanitized
spec, observed lifecycle state, and a reproduction. Avoid whole state databases
and raw transcripts. Do not post secrets or sensitive exploit details in public
issues; provide a sanitized reproduction when that is possible.

## Repository hygiene

Use functional branch and commit names, a configured human Git identity, and
focused commits. Keep temporary plans and local experiment artifacts outside
the repository. Never publish credentials or private conversation links.
