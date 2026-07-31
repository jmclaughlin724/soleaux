---
name: update
description: Update relevant repository documentation and context owners after accepted, verified changes. Not for proposing or implementing the behavior change itself.
---

# Update

## Contract

After an accepted implementation, architecture, workflow, or policy change is verified, reconcile all relevant repository documentation and context owners with live behavior. Make the narrowest durable correction in each canonical owner. Remove stale or duplicate guidance, refresh only declared projections, and prove contradictory references are gone. Do not create a context surface merely to record the task.

Run `$update` only after the task or plan is complete and its checks have passed.

Relevant owners include README files, architecture and operational documentation, `AGENTS.md`, `.agents/skills/**`, and `.codex/agents/**`. They also include explanatory guidance beside hooks, rules, tests, continuous integration (CI), or context tooling. A Codex-native path does not exclude its documentation or reusable guidance from this skill.

`$optimizer` owns Codex runtime behavior and configuration. Run `$update` afterward, once the task or plan is complete and its checks have passed.

## Owner Routing Gate

Classify every candidate before editing:

- Keep executable facts, installed providers, version selections, and file associations in their live configuration or manifest owner. Do not mirror discoverable configuration into prose.
- Put repeatable setup, migration, and troubleshooting procedures in the closest skill or skill reference.
- Put architecture explanations and operational runbooks in repository documentation.
- Put enforceable policy in the owning rule, hook, CI workflow, or runtime configuration.
- Put only durable, prescriptive constraints that future agents must obey across unrelated tasks in the closest applicable `AGENTS.md`.

Treat `AGENTS.md` as an operating contract, never as task history, a changelog, an implementation inventory, a setup guide, or an incident report. A fact being verified or likely to recur does not make it an invariant. Require the user to explicitly request a root project-instruction change before editing the root `AGENTS.md`; invoking `$update` alone does not authorize one.

Before adding any `AGENTS.md` instruction, require all of the following:

1. It directs future agent behavior instead of describing current implementation.
2. It remains useful when incidental versions, tools, and file layouts change.
3. `AGENTS.md` is the canonical owner rather than live configuration, a skill, a rule, or repository documentation.
4. Omitting it would violate a repository contract, not merely require rediscovering implementation details.

Route the content elsewhere when any condition fails. Return `NO_CHANGE` when no existing documentation or context owner needs reconciliation. If an existing root instruction is demonstrably stale but changing the root brief was not explicitly requested, return `BLOCKED` with the exact conflict.

## Use When

- Reconcile README, architecture, runbook, package, or operational guidance after accepted behavior changes.
- Update repository instruction and context owners, including `AGENTS.md`, skills, agent guidance, and explanatory rule or hook documentation.
- Codify a verified recurring lesson or failure mode in its durable owner.
- Remove stale commands, paths, ownership claims, or duplicate guidance.
- Refresh an existing documentation or context projection through its declared owner.

## Direct Workflow

1. Confirm the accepted behavior, the exact task-owned scope, and current post-change evidence for every acceptance criterion the documentation will claim. A pre-change result or self-report does not verify changed behavior. Stop if the behavior is proposed or unverified.
2. Inspect the live implementation, direct consumers, applicable instruction chain, relevant documentation and context owners, and declared projections. Start with exact stale names, paths, commands, and ownership claims.
3. Apply the Owner Routing Gate, then resolve each affected canonical owner from live repository evidence.
4. Edit each affected canonical owner and remove stale or duplicate guidance. Update adjacent context only when the accepted behavior requires it.
5. Run every touched projection through its declared owner and verify a second pass is unchanged. Do not invent a projection or edit generated output directly.
6. Run each owner's focused parser or validator, confirm referenced paths, commands, and links exist, repeat the exact stale-reference search, run `git diff --check`, and review the in-scope diff.
7. Return `UPDATED`, `NO_CHANGE`, or `BLOCKED`. Include the canonical owners, task-owned paths, projection commands when applicable, validation evidence, runtime-repair handoffs, and the exact blocker when present.

## Boundaries

- Do not edit implementation or a generated projection directly. Codex documentation and reusable guidance remain in scope.
- Do not describe proposed or unverified behavior.
- Do not add a second owner for the same policy or workflow.
- Do not stage or commit; the calling workflow owns Git delivery.
- Preserve unrelated and concurrent work.
