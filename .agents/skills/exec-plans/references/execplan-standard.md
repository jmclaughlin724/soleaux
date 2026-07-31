# ExecPlan standard

Use this procedure to create and maintain a two-file execution plan for work that spans sessions.

## Use two owners

Create one linked pair:

| File | Ownership |
| --- | --- |
| `PLAN.md` | Stable session-entry context, accepted specification, execution outline, checks, and success criteria |
| `TASKS.md` | Ordered Markdown checkboxes whose checked state is the live task completion status |

Do not create `PROGRESS.md`. Do not copy task status, checkboxes, or blockers into `PLAN.md`. Do not add an evidence log, evidence table, command-result ledger, timestamp history, duplicate status field, or placeholder progress record to either file.

## Write the session-entry plan

Make `PLAN.md` sufficient for a stateless executor to understand the work before reading the tracker. Include:

- the objective and observable outcome;
- the relevant background, current behavior, and repository context;
- the user or operator need and review expectations when they clarify the outcome;
- the desired behavior, intended architecture, and canonical owners;
- the files, symbols, or entrypoints to inspect first;
- explicit included and excluded scope;
- protected constraints, safety boundaries, and external approval requirements;
- fast-changing external claims and their authoritative verification sources when applicable;
- implementation requirements that constrain the executor without restating repository-wide rules;
- a phase-level execution outline;
- the local checks and any manual review required;
- a rollback or recovery strategy proportional to the work;
- measurable success criteria and definition of done; and
- a link to `TASKS.md`.

Objective, included scope, excluded scope, local checks, and success criteria must always be unambiguous. Include the other fields when they materially improve execution or review; do not copy irrelevant template sections or generic safety language into every plan.

Keep historical artifacts outside the required reading path. An existing handoff may remain optional, but `PLAN.md` must not depend on it for current requirements or architecture.

## Confirm readiness

Do not begin implementation until the objective and desired behavior are concrete, scope boundaries are enforceable, material risks and approvals are identified, and the stated checks can determine whether the success criteria are met. Represent unresolved preparation as unchecked tasks in `TASKS.md`; do not create a second readiness checklist in `PLAN.md`.

## Write the live task tracker

Link `PLAN.md` once, then list ordered tasks with Markdown checkboxes. The first incomplete dependency-ready task is the current task; the checked state is the status. Name paths, symbols, working directories, commands, dependencies, and expected results only when they materially help execution. Keep each task small enough to implement and verify as one coherent change.

Represent required validation as explicit tasks. Check a task only after its stated work is complete. When work is blocked, leave the task unchecked and add only the concise blocking condition needed to resume; update or remove the note when the condition changes.

## Update during execution

Before resuming, read `PLAN.md`, then continue with the first incomplete dependency-ready task in `TASKS.md`.

For each task:

1. Refresh scoped repository status and preserve concurrent work.
2. Execute and verify the first incomplete dependency-ready task.
3. Check the task only when it is complete; otherwise leave it unchecked and record a current blocker when resumption requires one.
4. Save the updated task state immediately.

Update `PLAN.md` only when the user accepts a material change to the specification, architecture, constraints, phase outline, or acceptance criteria. Update affected tasks in the same change.

## Coordinate execution

Keep the primary task as the sole writer of `PLAN.md` and `TASKS.md`. Use read-only helpers for independent research, review, and verification. Assign disjoint implementation paths in the active task when the user authorizes parallel work.

When a Codex goal drives the bundle, keep the goal to the accepted outcome, constraints, and verification, and point it to `PLAN.md`. Do not copy `TASKS.md` checkboxes or status into the goal. If the user changes the accepted specification, update `PLAN.md`, the affected `TASKS.md` items, and the goal together.

## Verify behavior

Run actual product tests, configured consumers, and security or runtime probes proportional to the change risk. The checks in `PLAN.md` define how to determine success; verification tasks in `TASKS.md` track whether that work is complete.

Report failed checks honestly to the user. If a failure prevents continuation, leave the relevant verification task unchecked and add the blocker needed to resume. A persistent execution transcript or per-task evidence record is not required.

Use one independent read-only final verifier when implementation risk warrants an additional completion check.

## Close the bundle

Leave completed tasks checked and every incomplete, blocked, deferred, or externally owned task unchecked and accurately described. Required work is complete only when every required checkbox is checked.

Report the files changed, checks actually run, results, and remaining uncertainty in the final response as required by repository instructions. Do not turn that response into a persistent completion summary or progress ledger unless the user asks for one.

## Migrate older bundles

When an active bundle uses another layout:

1. Move stable requirements, architecture, constraints, and acceptance criteria into `PLAN.md`.
2. Move current status, remaining tasks, and active blockers into `TASKS.md`.
3. Remove checklists and live status duplicated in `PLAN.md`.
4. Delete `PROGRESS.md` after its active task state has moved.
5. Keep historical artifacts only when they contain unique context worth preserving, and label them as nonauthoritative.
