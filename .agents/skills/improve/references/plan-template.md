# Implementation Plan Template

Use this template only when the user explicitly requests a durable plan. Return the plan in chat by default. Write it only to a repository-owned plan surface identified by current instructions or explicitly accepted by the user.

A plan is self-contained for a fresh executor: it names the owner, exact scope, current evidence, ordered changes, verification, and stop conditions without depending on the advisor session or guessing unavailable host features.

## Template

```markdown
# <Imperative outcome>

## Status

- Priority: P1 | P2 | P3
- Effort: S | M | L
- Risk: LOW | MED | HIGH
- Depends on: <plan identifier or none>
- Planned at: commit <short SHA>, <YYYY-MM-DD>

## Why this matters

State the verified problem, concrete impact, and user-visible outcome.

## Current evidence

- `<path>:<line>` — role and observed behavior.
- Applicable owner instructions and one local exemplar.
- Direct consumers, generated surfaces, and constraints.
- Short excerpts only when a symbol or contract cannot be identified clearly by path.

## Scope

In scope:

- exact files, symbols, or owner boundaries

Out of scope:

- adjacent work that must remain unchanged, with the reason

## Steps

### 1. <Imperative step>

Describe the exact owner change and affected consumers.

Verify: `<repository-owned command>` Expected: <observable result>

### 2. <Next step>

Continue in dependency order. Add or remove a layer only when the final contract requires it.

Verify: `<repository-owned command>` Expected: <observable result>

## Test plan

- Focused acceptance, rejection, regression, and boundary cases.
- Exact test owner and command.
- What each new assertion proves.

## Done criteria

- [ ] The requested behavior is present at the canonical owner.
- [ ] Every in-scope consumer uses the new owner.
- [ ] Removed names and paths have no remaining in-scope references.
- [ ] Focused tests and repository policy checks pass.
- [ ] The diff contains no unexplained out-of-scope changes.

## Stop conditions

Stop and report when:

- current evidence no longer matches the plan;
- a required owner or consumer is outside the accepted scope;
- verification fails twice without a supported in-scope repair; or
- a key assumption is disproven.

## Maintenance notes

Record only non-obvious future interactions or deliberately deferred work.
```

## Optional Artifact Index

Create or update an index only when the accepted plan owner already uses one or the user explicitly requests it. The index should contain the plan identifier, title, dependency, status, and one-line blocked or rejected reason. Do not assume `plans/`, `plans/README.md`, a branch, a worktree, dependency installation, or a commit workflow.

## Quality Bar

- A fresh executor can act using only the plan, repository, and available tools.
- Every material claim has current evidence.
- Every command exists in the repository and has an expected result.
- Steps name exact owners and consumers rather than "relevant files."
- Search and rewrite instructions use the repository's structured tooling for source code.
- Git operations, installs, external writes, and host-specific isolation appear only when separately authorized and available.
- No secret values appear in the artifact.
