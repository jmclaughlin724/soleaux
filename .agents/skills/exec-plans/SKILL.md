---
name: exec-plans
description: Author, review, execute, or resume a self-contained two-file Markdown ExecPlan after explicit user selection.
---

# Exec Plans

## Contract

Use two linked files for an accepted multi-session task:

- `PLAN.md` is the required session entry. It owns the objective, current and desired behavior, repository context, included and excluded scope, intended architecture and canonical owners, constraints, safety and external approval boundaries, phase outline, checks, rollback strategy, and measurable success criteria.
- `TASKS.md` is the sole live tracker. Its ordered Markdown checkboxes own task completion status. Add a concise blocker note only when it is needed to resume the work accurately.

Do not require or create an evidence log, evidence table, command-result ledger, timestamp history, duplicate status field, or `PROGRESS.md`. Product verification remains required work, but verification is represented by executable tasks and accurate checkbox state rather than a second progress record. Keep `PLAN.md` self-contained so a new session can understand the work before opening `TASKS.md`.

Activate this skill only when the user explicitly selects `$exec-plans`. Preserve the requested work layer: author or revise the bundle when asked for planning, review it read-only when asked for review, and execute only accepted implementation work when asked to execute or resume.

Store durable cross-task conventions in `AGENTS.md`. Store task-specific architecture and constraints in `PLAN.md`, execution state in `TASKS.md`, and temporary assignments in the active task.

## Workflow

1. Read the complete [ExecPlan standard](references/execplan-standard.md), the named plan, applicable repository instructions, and the repository owners needed to make the plan accurate.
2. Create or update linked `PLAN.md` and `TASKS.md` files.
3. Make `PLAN.md` the accepted task specification and complete orientation for a stateless executor.
4. Put ordered actionable work and explicit verification work in `TASKS.md`. Use checkbox state as the completion status and keep only current blocker notes needed for resumption.
5. Keep the primary task as the sole writer of both control files. Use read-only helpers when independent research or verification adds value.
6. Update `TASKS.md` immediately after a task completes, becomes blocked, or resumes. Update `PLAN.md` only when the accepted specification changes.
7. Run actual product tests and configured consumers proportional to risk. Report the checks and results in the final response; do not persist a task evidence log unless the user explicitly requests one.

## Coordination

Use `$team` when the user permits delegation and independent workstreams can proceed safely. Keep write scopes disjoint and assignment state in the active task, not in another planning document. Use one independent read-only final verifier when implementation risk warrants it.

## Completion

Leave both files accurate at every stopping point. Keep completed tasks checked, incomplete tasks unchecked, and blocker notes current. Carry stable requirements and decisions into `PLAN.md`, carry only live task status into `TASKS.md`, then remove redundant active planning records.
