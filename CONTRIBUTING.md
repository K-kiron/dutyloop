# Contributing

Use Python 3.11 or newer and an existing Git installation.

```console
python -m pip install -e ".[dev,mcp]"
python -m pytest -q
python -m compileall -q src
python -m build
```

Tests must use local fixtures or explicitly simulated drivers by default. Do
not make authenticated model calls or require production credentials in CI.
Label live host checks separately, record the host version and permission mode,
and keep raw state and transcripts outside the repository.

Changes to dispatch, stop, limits, acceptance, or recovery need behavior tests
that demonstrate the failure before the fix. Include concurrency or interruption
cases when those contracts change. Keep unknown usage unknown and preserve
evidence of rejected or interrupted work.

Use functional branch and commit names, a configured human Git identity, and
focused commits. Keep temporary plans and local experiment artifacts outside
the repository. Never publish credentials or private conversation links.
