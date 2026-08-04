# Phase 3 Experiment Plan

## Purpose

Test the north-star claim:

> Soleaux replaces waste context at equal or better final task success.

## Pre-registered implementations

### Baseline

```text
Repository: jmclaughlin724/soleaux
Commit:     8ec7abcdf180130448c59127c8487a9ec0515611
Surface:    Python/FastMCP public lineage
Label:      0.1.0-unreleased
```

### Treatment

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

## Fixed tasks

The task definitions in `TASKS.json` are frozen before the first live call:

- `P3-T01` repository architecture trace;
- `P3-T02` Next.js route and Turborepo boundary analysis;
- `P3-T03` bounded validation-test change.

No task may be removed after the first run. Any changed task requires a new experiment ID and complete restart.

## Model and client lock

The run is blocked until these are recorded in `STATUS.json`:

- client name and exact build;
- model identifier;
- protocol version;
- temperature;
- top-p;
- seed or explicit null;
- credentials availability.

The baseline and treatment use identical values.

## Budgets

Before the first run, record identical:

- task prompt;
- host resources;
- file/context budget;
- maximum tool calls if constrained;
- timeout;
- retry policy.

Soleaux's compiled packet may use its normal internal budget, but the final host/model budget must match the baseline arm.

## Repetition

Minimum:

- one run per task per arm for the first blocking result;
- three runs per task per arm when the client/model supports deterministic or controlled repeatability and cost permits.

All attempts remain in the dataset.

## Primary outcome

Aggregate correctness must be equal or better in the treatment arm.

## Secondary outcomes

- root schema tokens;
- tool calls;
- file-read tokens;
- compiled-context tokens;
- elapsed time;
- retries;
- cost;
- coverage gaps;
- secret redactions.

## Integrity conditions

Treatment runs are invalid when:

- root tools are not exactly 12;
- a selected parser/LSP is non-native;
- Context Packet V2 fails validation;
- required fields are silently omitted;
- secrets leak;
- hidden tools or a Python treatment mode are used.

## Closure

Phase 3 closes only after:

- all runs and failures are retained;
- scoring is complete;
- aggregate correctness gate passes;
- context-economy results are reported;
- artifact digests and commits are independently verified;
- exact receipt is written;
- `productionClaimAllowed` remains false.
