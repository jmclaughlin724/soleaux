# Phase 3 Experiment Plan

**Status:** deferred by owner direction. Do not execute until explicitly reactivated and re-frozen.

## Purpose

Test both required claims:

1. **Market-value gate:** native Soleaux replaces waste context at equal-or-better task success than the selected client's ordinary repository access without Soleaux.
2. **Compatibility gate:** native Soleaux does not regress the useful historical Python/FastMCP lineage.

A two-arm historical-versus-native comparison cannot prove value versus not using Soleaux. A no-Soleaux comparison cannot prove compatibility. All three arms are required.

## Frozen implementations when reactivated

### Arm A — `control_no_soleaux`

The selected authenticated client uses only its ordinary built-in repository/file/search/edit capabilities. No Soleaux MCP, historical server, hidden equivalent MCP, or precompiled Soleaux packet is attached. The exact allowed client tools and schemas must be captured before the first run.

### Arm B — `historical_python`

```text
Repository: jmclaughlin724/soleaux
Commit:     8ec7abcdf180130448c59127c8487a9ec0515611
Surface:    Python/FastMCP public lineage
Label:      0.1.0-unreleased
```

### Arm C — `native_treatment`

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

The prior synthetic/GitHub Models carrier experiment is historical development evidence only. It is not authorized as the current product-proof experiment because it lacks the no-Soleaux control and does not use the registered real-repository tasks. It must not be executed or cited as current efficacy evidence.

## Fixed tasks

The task definitions in `TASKS.json` are frozen before the first live call:

- `P3-T01` repository architecture trace;
- `P3-T02` Next.js route and Turborepo boundary analysis;
- `P3-T03` bounded validation-test change.

No task, prompt, rubric, oracle, or budget may change after the first live call. A change requires a new experiment ID and complete restart.

## Model, client and environment lock

Before execution, record:

- exact client and build;
- exact authenticated model ID;
- MCP protocol version;
- temperature, top-p and seed/null;
- system/developer/task prompts;
- host resources;
- maximum context/output/tool-call budgets;
- timeout and retry policy;
- token-estimation method and provider-reported usage;
- cost source;
- credentials availability.

All three arms use identical values except the registered repository-intelligence surface.

## Oracle dry-run

Before live calls, independently derive and freeze:

- required facts and acceptable gaps for the two analysis tasks;
- canonical card-ID owner, minimal patch, authoritative test taxonomy and validation commands for the mutation task;
- scoring inputs and hard-fail conditions;
- clean-worktree and changed-file checks.

## Repetition

Minimum blocking dataset: one attempt per task per arm (nine runs). Preferred: three attempts per task per arm when deterministic/controlled repetition and cost permit (twenty-seven runs). Every attempt and failure remains in the dataset.

## Primary gates

Market-value gate:

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

## Integrity conditions

A native treatment run is invalid when:

- `tools/list` is not exactly twelve;
- a selected parser or LSP is non-native;
- Context Packet V2 fails validation;
- required fields are silently omitted;
- secrets leak;
- hidden tools or a Python treatment mode are used;
- task, prompt, budget, model or rubric drift occurs.

## Closure

Phase 3 closes only after every run/failure is retained, both correctness gates pass, context economy is reported, exact receipts and artifact digests exist, independent verification passes, and `productionClaimAllowed` remains false.
