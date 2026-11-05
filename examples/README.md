# Mission examples

Copy a spec outside the target repository, replace placeholders, and validate
it before initialization. These examples describe authority, not verified
results on your project.

| File | Purpose | What to replace |
| --- | --- | --- |
| [mission.json](mission.json) | Bounded public-API reliability and usability maintenance. | Project path, scope, and unittest verification command. |
| [documentation-mission.json](documentation-mission.json) | Correct documentation/examples without changing product behavior. | Project path and the operator-owned documentation checker. |

The documentation checker in the second example is a placeholder; DutyLoop
does not supply a universal documentation oracle. Choose checks that test your
actual commands, links, examples, or public behavior. Keep decisive checks
outside writable paths where practical.

```console
dutyloop validate --spec mission.json --state /private/mission-state
dutyloop init --spec mission.json --state /private/mission-state
dutyloop run --state /private/mission-state
```

Each named branch must be new. Omit `branch` to generate a unique name. A mission
starts from committed `HEAD`; uncommitted source and ignored local files are not
copied into task worktrees. Read [configuration](../docs/configuration.md) for
field meanings and [operations](../docs/operations.md) for inspecting outputs.
