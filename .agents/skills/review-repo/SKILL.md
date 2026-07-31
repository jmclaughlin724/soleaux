---
name: review-repo
description: Review a working tree, commit range, pull request, or live review feedback for concrete defects, regressions, security risks, and missing verification.
---

# Review

## Contract

Review is read-only by default. Find actionable defects in the requested change, verify each finding against the owning contract and affected consumer, and report findings before any summary unless the caller provides an explicit report schema or order. Do not edit code, post comments, resolve threads, approve a pull request, or otherwise change external state unless the user explicitly requests that specific action.

Prioritize correctness, regressions, security, privacy, permissions, data integrity, missing tests, and material maintainability or performance risks. For documentation changes, retain material heading hierarchy, terminology, formatting, rendering, and clarity defects when they impair comprehension, discoverability, accessibility, contract accuracy, or safe execution. Ignore taste-only style preferences and pre-existing issues the change does not expose or worsen.

## Select the Mode and Scope

Use the user's explicit target first. Documentation review defaults to explicitly named tracked files and named baseline context; do not widen it to all untracked files. Inspect an untracked document only when the caller names it or explicitly requests complete current-change review. For other reviews without an explicit target, review the complete current change: staged changes, unstaged changes, and untracked files. For a branch or pull request, resolve the authoritative base and include both committed and working-tree changes when relevant. Do not silently reduce scope to the easiest available diff.

- **Review mode** is the default and permits only read-only inspection and verification.
- **Fix mode** requires an explicit request to fix or address findings. Edit only confirmed findings inside the accepted scope, then run focused checks. It does not authorize GitHub comments or thread resolution.
- **PR-feedback mode** applies when the user asks to inspect or address live review comments. Load current thread state before deciding what remains actionable. Replies, resolutions, approvals, labels, and other external writes each require explicit authorization.

## Review Workflow

1. Establish the review target, user intent, accepted requirements, applicable instructions, base revision, dirty-worktree boundaries, allowed side effects, and any caller-provided report schema.
2. For a pull request, complete the mergeability preflight below before assigning a verdict.
3. Inspect the complete diff, then read enough surrounding owner and consumer code to understand the changed behavior. Trace new preconditions, return shapes, errors, timing, state, permissions, and deleted safeguards across direct callers and callees. Do not require full-file reads when a smaller complete context is decisive.
4. Run the independent review angles in [the review playbook](references/review-playbook.md#review-angles). Scale depth to risk and change size; do not create a fixed agent fleet. Each candidate must identify a concrete failure scenario, affected contract, and precise changed location.
5. Verify candidates rather than forwarding speculation. Re-read the decisive source, owner configuration, consumer, tests, or authoritative semantics. Remove duplicates, false positives, findings outside the changed scope, and claims unsupported by a reproducible scenario.
6. Rank surviving findings by impact and likelihood:
   - `P0`: catastrophic or actively exploitable; blocks all use or release.
   - `P1`: high-impact correctness, security, privacy, or data-loss defect likely in normal use.
   - `P2`: real defect affecting a narrower or recoverable path.
   - `P3`: low-impact but concrete defect worth fixing; never use this level for taste-only nits.
7. Report findings first, most severe first, unless the caller's explicit report schema sets another order. Each finding must include a concise title, file and line, failure scenario, impact, and narrowest appropriate correction. State assumptions or missing decisive evidence separately.
8. If no finding survives verification, say so directly and report only material residual risks or testing gaps. Do not invent findings to satisfy a count.

## Pull request mergeability preflight

For a live pull request, resolve the authoritative base and head, draft state, merge conflict or provider mergeability state, required reviews, and required check results through the available read-only repository or provider route. Treat local merge simulation and repository configuration as supporting evidence, not proof of current hosted state. If live evidence is unavailable, mark mergeability unverified without suppressing the diff review.

Classify required checks as passed, failed, pending, not run, or fork-restricted from the observed provider evidence. Use `fork-restricted` only when a check was skipped or withheld because the contribution comes from a fork and an approval, credential, or trust boundary prevented the workflow from running. Do not report fork-restricted CI as passed, failed, or generic skipped CI; state the observed restriction, its mergeability consequence under repository policy, and the action or owner needed to unblock it.

## PR Feedback

When reviewing live feedback, obtain current pull-request metadata, review threads, review comments, and issue comments through an available authenticated GitHub route. Cluster duplicate comments and classify each item as actionable, question, stale, already addressed, or requiring user choice. Preserve the reviewer's intent; do not implement a literal suggestion that conflicts with the repository contract or introduces a defect.

If the user asks to address all feedback, fix every confirmed actionable item that fits the accepted scope. If selection materially changes the work and the user did not authorize all items, present the numbered clusters and ask which to address. After authorized fixes, rerun affected checks and summarize the item-to-change mapping. Post replies or resolve threads only when explicitly requested, and only after the corresponding change is verified.

Do not hardcode a versioned plugin-cache path or make the project skill depend on a personal helper script. Use the current authenticated GitHub capability when live state is required; otherwise report that live thread state could not be verified.

## Output

An explicit caller-provided report schema, field order, verdict vocabulary, or severity system controls the response unless it conflicts with higher authority or requires claims unsupported by evidence. Otherwise use this order:

1. findings, each anchored to the tightest relevant file and line;
2. mergeability preflight for pull-request targets, including required-check classifications;
3. open questions or assumptions that could materially change the verdict;
4. compact change overview and verification gaps.

Omit empty sections. A review with no findings should start with “No actionable findings.” In fix mode, report the findings fixed, checks run, and any item deliberately left unresolved. Never bury findings beneath a general summary.

## Boundaries

- Do not treat tests added by the author as proof that the production route is correct; inspect what they cover and what they omit.
- Do not flag missing tests without naming the unprotected behavior and a plausible regression the test would catch.
- Do not report speculative security or performance concerns without a concrete input, scale, permission boundary, or failure path.
- Do not expand a diff review into unrelated cleanup or rewrite protected public contracts.
- Do not post, reply, resolve, approve, merge, stage, commit, or push without explicit authority for that action.
- Keep simplification findings behavior-preserving; route architecture-wide consolidation to `$elegant` only when the user asks for that broader outcome.
