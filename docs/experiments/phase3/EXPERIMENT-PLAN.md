# Phase 3 Experiment Plan

**Status:** deferred by owner direction. Do not execute until explicitly reactivated and re-frozen.

## Purpose

Test both required claims:

1. **Market-value gate:** native Soleaux replaces waste context at equal-or-better task success than ordinary client repository access without Soleaux.
2. **Compatibility-regression gate:** native Soleaux does not regress the useful historical Python/FastMCP lineage.

## Why three arms are required

A historical-Soleaux-versus-native-Soleaux comparison cannot prove value versus not using Soleaux. A no-Soleaux comparison cannot prove that unification retained the original product's useful capabilities. Phase 3 therefore uses all three arms.

## Pre-registered arms

### Control — no Soleaux

The selected authenticated client uses only its ordinary built-in repository/file/search/edit capabilities. No Soleaux MCP, historical server, hidden equivalent MCP, or precompiled Soleaux packet is attached.

The exact allowed client tools must be captured before the first run.

### Historical compatibility baseline

```text
Repository: jmclaughlin724/soleaux
Commit:     8ec7abcdf180130448c59127c8487a9ec0515611
Surface:    Python/FastMCP public lineage
Label:      0.1.0-unreleased
```

### Native treatment

```text
Repository: jmclaughlin724/soleaux
Commit:     6768d9de2aa8a61ba90356409033c0d69b2d5afc
Surface:    unified native Rust Soleaux
Label:      0.4.0-dev.5
Tools:      exactly 12
```

### Target repository

```text
Repository: jmclaughlin724/anilize
Commit:     2b7a0fab88dbc202f75b5e443725c825f7dc4fa2
Profile:    Turborepo + Next.js monorepo
```

## Superseded experiment

`native/0.4.0-dev.5/phase3/experiment-design.json` and its GitHub Models/synthetic-task carriers are historical development evidence. They are not authorized as the current Phase 3 experiment because they do not include a no-Soleaux control and do not use the registered real target tasks. They must be archived or removed during source consolidation.

## Fixed tasks

The three tasks in `TASKS.json` remain frozen before live execution:

- architecture trace;
- Next.js/Turborepo route and boundary analysis;
- bounded duplicate-card-ID validation-test change.

No task or prompt may change after the first live call. A change requires a new experiment ID and complete restart.

## Model, client, and environment lock

Before execution, record:

- exact client and build;
- exact model identifier;
- MCP protocol version;
- temperature, top-p, seed or null;
- system/developer/task prompts;
- host resources;
- maximum context and output budgets;
- maximum tool calls if constrained;
- timeout and retry policy;
- tokenizer/estimation method;
- cost source;
- credentials availability.

All three arms use identical values except the registered repository-intelligence surface.

## Oracle dry-run

Before live calls, independently derive and freeze:

- required facts and acceptable gaps for the two analysis tasks;
- canonical card-ID owner, minimal patch, authoritative test taxonomy, and validation commands for the mutation task;
- scoring inputs and hard-fail conditions;
- clean-worktree and changed-file checks.

## Repetition

Minimum blocking run: one attempt per task per arm (nine runs). Preferred: three attempts per task per arm when deterministic/controlled repetition and cost allow (twenty-seven runs). All attempts and failures remain in the dataset.

## Primary gates

```text
mean native score >= mean no-Soleaux control score
AND native hard-fail rate <= control hard-fail rate
AND native mutation-task oracle passes
```

Compatibility gate:

```text
mean native score >= mean historical Python score
AND native hard-fail rate <= historical hard-fail rate
```

Context economy is reported against the no-Soleaux control and cannot compensate for lower correctness.

## Treatment integrity

Native treatment is invalid when:

- tools/list is not exactly twelve;
- a selected parser or LSP is non-native;
- Context Packet V2 fails validation;
- required fields are silently omitted;
- secrets leak;
- hidden tools or Python treatment mode are used.

## Closure

Phase 3 closes only after every run/failure is retained, both correctness gates pass, context economy is reported, exact receipts and artifact digests exist, independent verification passes, and `productionClaimAllowed` remains false.
