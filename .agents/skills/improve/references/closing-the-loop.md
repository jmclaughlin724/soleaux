# Closing the Loop — execute, reconcile, issues

This file covers three explicitly requested follow-through flows: dispatching and reviewing an executor (`execute`), maintaining a plan backlog (`reconcile`), and publishing plans (`--issues`).

The advisor never edits source code. In `execute`, a separate subagent performs the implementation while the parent owns scope, reconciliation, review, and final validation. Codex subagents normally share the current filesystem; worktree isolation exists only when the host explicitly provides and verifies it.

---

## `execute <plan>` — dispatch and review

### Preconditions

- The user explicitly requested execution, not only an audit or plan.
- The plan exists, its dependencies are satisfied, and its scope is still current.
- The host supports a bounded implementation subagent. If isolation is required, verify a real isolated worktree before dispatch; never infer one from the existence of git.
- Reconcile concurrent or dirty-tree changes before assigning overlapping writes.

### Dispatch

Spawn one bounded implementation subagent using an available Codex role. Preserve an explicit user-selected model or effort when the host exposes those controls; otherwise use the host default. Do not promise a model, role, or isolation mode that the active host does not expose.

The subagent prompt must contain:

1. The exact plan or the minimum self-contained implementation slice, including scope, owners, stop conditions, and validation.
2. The executor preamble:

> Implement only the bounded plan below. Read the applicable owner instructions, preserve unrelated and concurrent work, and run the named validation. Stop on any listed stop condition or material scope conflict. Do not install dependencies, commit, merge, push, or perform external writes unless the user separately authorized that action. Report only claims supported by this session's evidence and name every skipped or failed check.

3. The report format:

```
STATUS: COMPLETE | STOPPED
STEPS: per step — done/skipped + verification command result
STOPPED BECAUSE: (only if STOPPED) which STOP condition, what was observed
FILES CHANGED: list
NOTES: anything the reviewer should know (deviations, surprises, judgment calls)
```

### Review (the advisor's real job here)

Review like a tech lead reviewing a PR against the spec — never fix anything yourself:

1. **Re-run every done criterion** in the actual implementation boundary. Do not treat the executor's report as proof.
2. **Check scope compliance** against the plan's in-scope list and distinguish the executor's changes from pre-existing work. Any unexplained overlap or out-of-scope edit requires reconciliation.
3. **Read the full diff.** Judge it against "Why this matters" (does it solve the actual problem?) and the repo conventions named in the plan (does it look like the rest of the codebase?).
4. **Audit the new tests.** Executors game criteria — a test that asserts nothing meaningful passes `pnpm test` and proves nothing. Read what the tests assert.

### Verdict

**Documented deviations are judged on merit, not reflex-blocked.** "Do not improvise" exists to stop silent drift; an executor that hits a real obstacle (e.g. the plan's approach breaks existing test mocks), adapts minimally, and explains it in NOTES has done the right thing. Approve it if the adaptation serves the plan's intent and stays in scope; treat _undocumented_ deviations as review failures.

| Verdict | When | Action |
| --- | --- | --- |
| **APPROVE** | Criteria pass, scope clean, quality holds | Update an owned plan index when one exists. Present the diff summary, validation, and material notes. Never merge, push, or commit without authorization. |
| **REVISE** | Fixable gaps | Send a targeted revision task to the same executor with the exact failed criterion and evidence. Use at most two revision rounds, then block. |
| **BLOCK** | STOP condition hit, scope violated unrecoverably, or revisions exhausted | Mark BLOCKED in the index with the reason. Refine or rewrite the plan with what was learned. Tell the user what happened and what changed in the plan. |

---

## `reconcile` — maintain an accepted plan owner

Run this mode only when the user names a plan owner or current repository instructions identify one. If no plan owner or index exists, stop and return the reconciliation in chat rather than creating a new convention. Read the accepted index and its referenced plans, then process each status:

- **DONE** — spot-check that the done criteria still hold on the current HEAD (cheap ones only). Mark verified in the index. Don't delete plan files — they're the record.
- **BLOCKED** — read the reason. Investigate the underlying obstacle in the codebase. Either rewrite the plan around it (new number if the approach changed fundamentally, in-place refresh otherwise) or mark REJECTED with one line of rationale.
- **IN PROGRESS** (stale) — report the stale status and inspect only execution state the active host can actually verify.
- **TODO** — run the drift check. If drifted: re-verify the finding still exists (it may have been fixed in passing), then refresh the "Current state" excerpts and `Planned at` SHA. If the finding is gone, mark REJECTED ("fixed independently").

Finish with a short report: what's verified done, what was refreshed, what's rejected, and what's executable right now.

---

## `--issues` — publish plans as GitHub issues

Modifier on an explicit improve planning request using `--issues`. External issue creation still requires the repository visibility check and the user's authorization for the exact titles being published.

1. Preflight: `gh auth status` succeeds and the repo has a GitHub remote. If either fails, return the plans through the accepted artifact channel and say why issues were skipped.
2. Show the list of titles about to become issues; confirm once if interactive.
3. Per plan: `gh issue create --title "<plan title>" --body-file <plan file>`. Labels: `improve` plus the category — apply only if the labels exist or can be created without erroring; skip labels rather than fail.
4. Record each issue URL in the plan's Status block (`- **Issue**: <url>`) and the index.

The accepted plan artifact remains the source of truth; the issue is distribution. When no artifact owner exists, the authorized issue body must itself be self-contained.
