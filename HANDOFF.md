# Soleaux Cold-Start Handoff

Read in this order:

1. [`PROJECT-STATUS.json`](PROJECT-STATUS.json)
2. [`PROJECT-STATUS.md`](PROJECT-STATUS.md)
3. [`AGENTS.md`](AGENTS.md)
4. [`ROADMAP.md`](ROADMAP.md)
5. [`TASKS.md`](TASKS.md)
6. [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json)
7. [`PHASE4-INDEPENDENT-VERIFICATION.json`](PHASE4-INDEPENDENT-VERIFICATION.json)
8. [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json)
9. [`P5-002-P5-006-CLOSURE-RECEIPT.json`](P5-002-P5-006-CLOSURE-RECEIPT.json)
10. [`docs/plans/PHASE5-IMPLEMENTATION-PLAN.md`](docs/plans/PHASE5-IMPLEMENTATION-PLAN.md)
11. [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)
12. the locked MCP and Context Packet contracts.

## Current state

```text
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     DEFERRED — CLAIMS GATE
Phase 4:                     CLOSED
Phase 5:                     IN PROGRESS
Canonical branch:            main
productionClaimAllowed:      false
Public tool ceiling:         12
Unsigned alpha:              reproducible and independently verified
```

## Do not repeat

Phase 4 is complete. Do not reimplement P4-001 through P4-026. The merged native source already includes correctness, canonical state, durability/recovery, encrypted artifacts/policy, stable CLI, per-user service, typed IPC, deterministic alpha packaging, and complete extracted-package operational smoke.

## Exact next work

Seed the session task tracker first with the `load-tasks` skill; it reconstructs every open task and dependency from [`docs/plans/PHASE5-DEPENDENCIES.json`](docs/plans/PHASE5-DEPENDENCIES.json) and is safe to re-run.

1. **P5-009** — the LSP matrix closes at v2 after P5-023. P5-008, P5-010, and P5-021 are closed (evidence: [`P5-008-CLOSURE-RECEIPT.json`](P5-008-CLOSURE-RECEIPT.json), [`P5-010-CLOSURE-RECEIPT.json`](P5-010-CLOSURE-RECEIPT.json), [`P5-021-CLOSURE-RECEIPT.json`](P5-021-CLOSURE-RECEIPT.json)); the P5-V1 verifier milestone is merged (PR #54) with the separately reviewed per-client lifecycle oracle landing at adapter enablement. Waves and lanes: [`docs/plans/PHASE5-IMPLEMENTATION-PLAN.md`](docs/plans/PHASE5-IMPLEMENTATION-PLAN.md).
2. **P5-009 through P5-013** — close the LSP matrix at v2, design-partner validation, and the Phase 5 beta receipt.
3. **P5-014 through P5-020** — complete deep adapters, memory lifecycle, signed handoffs, and durable runs/subagents.
4. **P5-021 through P5-029** — complete intelligence depth, provider interfaces, SDKs, deterministic CI, editor integration, and optional hybrid search.
5. `anilize` is validated (evidence: [`docs/operations/P5-011-ANILIZE-VALIDATION.json`](docs/operations/P5-011-ANILIZE-VALIDATION.json)); validate two additional approved design partners (owner-sourced approvals).
6. Close Phase 5 only with an exact beta receipt and independent verification.

Phase 3 remains deferred. Reactivate it only when an efficacy claim is requested, and freeze all three arms before the first live call.

## Hard constraints

- keep the exact twelve-tool contract and locked digests;
- keep `0.4.0-dev.5` and `productionClaimAllowed=false` unless explicitly reviewed;
- no force-push or squash of receipt-bearing ancestry;
- no direct writes to vendor internal databases;
- no root-tool inflation;
- no production claim from an unsigned alpha, merge state, or test count;
- unknown client/provider/LSP versions enter safe or read-only mode;
- preserve exact evidence and branch-consolidation reports.

Use the stop format in `AGENTS.md` only when a hard contract or evidence condition is actually violated. P5-001 through P5-008 — including the P5-007 session service — plus P5-010, P5-021, and P5-W1 are closed and must not be reimplemented. Continue with P5-009 and the active adapter, memory, and depth lanes, following the implementation plan's waves and declared parallel lanes.
