# Mission lifecycle

The mission is the persistent unit of responsibility. Tasks are temporary units
of delivery discovered while pursuing that responsibility.

## Inputs

A version-1 JSON spec supplies:

- A nonempty mission and an existing Git repository root.
- Repository-relative allowed paths and optional behavioral constraints.
- Permitted work kinds: feature, bugfix, QA, or documentation.
- Verification commands as nonempty argv arrays supplied by the operator.
- Driver choice, optional model, and one to three discovery workers.
- Required invocation and elapsed wall-time limits.

Unknown spec and limit fields are rejected. In particular, a monetary limit is
not accepted as a substitute for an enforceable invocation or time limit. Mission
state must live outside the target repository.

## Execution

```mermaid
flowchart TD
    A[Check stop, deadline, and invocation budget] --> B[Inspect current accepted project]
    B --> C{Discovery result}
    C -->|work| D[Select one evidenced candidate]
    C -->|wait| E[Persist waiting state and poll]
    C -->|blocked| F[Persist blocker and return]
    E --> A
    D --> G[Implement in detached worktree]
    G --> H[Check changed paths and run operator verification]
    H --> I[Separate review invocation]
    I -->|accepted| J[Commit and advance mission branch]
    I -->|rejected or failed| K[Preserve attempt and apply failure policy]
    J --> A
    K --> A
```

Every driver invocation is reserved before it starts. Up to three discovery
workers can inspect independently. Implementations are serial, so two workers
do not independently advance the accepted branch. Discovery supplies a stable
candidate key, kind, priority, reason, evidence, and acceptance criteria. The
supervisor validates the structured response; valid JSON alone does not prove
that its evidence or priority is correct.

An implementation's `completed` response is insufficient for acceptance.
Verification is run by the supervisor using the configured argv, and review is
a separate driver invocation. A review from the same model is not an
independent human or an infallible correctness oracle.

Accepted work advances the mission's branch and accepted commit. It does not
finish the mission. Rejected or uncertain attempts remain inspectable. The
supervisor does not switch or edit the original checkout. Child tools retain
host permissions and access to shared Git metadata; worktrees are not a security
sandbox. No publication or deployment step is built into the loop.

## Waiting and termination

`wait` means no actionable candidate was identified in that inspection. The
mission waits for `poll_seconds` and inspects again while its limits allow.
Repeated idle inspections still consume invocations. A blocked result, failure
policy, exhausted limit, or explicit stop ends the current runner.

The wall-time window starts at the first run. Pausing the process, leaving it
offline afterward, or waiting for the next poll does not reset that window. An explicit
stop is persisted and checked before dispatch; it cannot be undone by a model
response.

## Recovery

State records calls, task outcomes, the mission branch, and the accepted head.
An unfinished call after an interruption has an unknown outcome. Automatic
replay would risk duplicating work or effects, so recovery requires explicit
operator acknowledgment. Inspect worktrees and call artifacts first. Neither
recovery nor a success-looking final message refunds spent invocations.

Unfinished implementation or verification tasks also require recovery even if
their last model call completed. Stop and integration are serialized; work not
integrated before a stop remains inspectable. Git refs and SQLite are separate
stores: an interruption between their updates can leave a mismatch. A restart
detects it and refuses to proceed; inspect the selected commit and initialize a
new mission rather than automatically replaying or repairing it.

These are lifecycle and acceptance controls. See [limits and host support](limits-and-hosts.md)
for the separate filesystem, credential, network, and process boundaries.
