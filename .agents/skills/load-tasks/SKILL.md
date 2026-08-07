---
name: load-tasks
description: Load, seed, or restore the Soleaux remaining-work task tracker from the repository's dependency graph. Use at cold start of an execution session, when the user asks to load or restore the task list, or when the tracker is empty or stale. Idempotent; never duplicates existing tracker entries.
---

# Load Tasks

## Contract

Reconstruct the session task tracker from repository state alone.
The sources of truth are [`docs/plans/PHASE5-DEPENDENCIES.json`](../../../docs/plans/PHASE5-DEPENDENCIES.json) (nodes, groups, edges, `nextOpen`) and [`TASKS.md`](../../../TASKS.md) (completion checkboxes).
The tracker is a projection; never treat a tracker entry as completion evidence — receipts and checkboxes own that.
Re-running must create zero duplicates.

## Procedure

1. Read `docs/plans/PHASE5-DEPENDENCIES.json` and `TASKS.md`.
2. Classify each graph node: closed when its checkbox line (`checkbox` field, else `id`) is `- [x]` in `TASKS.md`; open otherwise.
   A group is open unless every task in its `taskRange` is checked.
3. Call `TaskList`.
   An entry already exists when a tracker subject starts with the node id plus `:` (for example `P5-007:`) or equals the group `title`.
4. For each open node or group with no existing entry, call `TaskCreate`:
   - subject: `<id>: <short outcome>` for nodes, the group `title` for groups;
   - description: the concrete outcome, the canonical owner file paths, the evidence that closes it (receipt name from the graph), the scope bound, and the plan-doc section reference from `planSection` or the node id — full acceptance detail stays in [`docs/plans/PHASE5-IMPLEMENTATION-PLAN.md`](../../../docs/plans/PHASE5-IMPLEMENTATION-PLAN.md); the description summarizes and links.
5. For every open entry, map graph `blockedBy` ids to tracker task ids and apply `TaskUpdate` with `addBlockedBy`, restricted to edges whose dependency is still open (closed dependencies are satisfied and need no edge).
6. Skip closed nodes entirely; do not create completed tracker entries.
7. Report: counts of created, already-present, and skipped-closed entries, plus the current unblocked frontier (open entries with no open blockers).
   The frontier must include the graph's `nextOpen` task.

## Boundaries

- Read-only toward the repository: this skill writes the session tracker, never files.
- Never mark tracker entries completed from this skill; closures happen through the receipt discipline and doc-sync, after which a re-run drops them naturally.
- If the graph and `TASKS.md` disagree in a way the consistency checker would reject (missing checkbox, unknown edge), stop and report the drift instead of guessing.
