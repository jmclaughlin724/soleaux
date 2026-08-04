# Soleaux Project Status

<!-- soleaux-docs:status current_phase=4 phase2=closed phase3=deferred_reconciliation_required phase4=in_progress version=0.4.0-dev.5 production_claim_allowed=false -->

**As of:** 2026-08-03  
**Machine-readable owner:** [`PROJECT-STATUS.json`](PROJECT-STATUS.json)

## Executive state

```text
Product:                     Soleaux
Definition:                  Unified repository intelligence
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     DEFERRED — RECONCILIATION REQUIRED BEFORE USE
Phase 4:                     IN PROGRESS
Canonical working branch:    native/0.4.0-dev.5
Public MCP ceiling:          12
productionClaimAllowed:      false
Production readiness:        prohibited
```

Phases 0–2 prove a valuable native repository-intelligence foundation. They do not prove the full session, memory, adapter, desktop, mobile, orchestration, security, and distribution product described in the reviewed transcripts. The detailed coverage and missing work are owned by the transcript gap audit and machine registry.

## Proven phases

| Phase | Outcome | Exact source | Workflow | Evidence |
|---:|---|---|---:|---|
| 0 | Contracts locked; native build foundation green | `a31820d26f46d258175b52fe30fdbecf7b650265` | `30766171022` | `PHASE0-NATIVE-GATE-RECEIPT.json` |
| 1 | Exact twelve-tool catalog and `soleaux.context/v2` green | `d3eecd45867e82d5777e57753c581483971214dd` | `30773147694` | `PHASE1-NATIVE-GATE-RECEIPT.json` |
| 2 | Gateway, catalog domains, adopt/attach, governance green | `6768d9de2aa8a61ba90356409033c0d69b2d5afc` | `30818963313` | Phase 2 closure + independent verification |

## Locked invariants

```text
Unified MCP profile:
89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050ddf57ed967a9c57e3a60fc

Context Packet V2:
3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f

PRODUCT_VERSION:              0.4.0-dev.5
PRODUCTION_CLAIM_ALLOWED:     false
HARD_CEILING:                 12
```

The broader requested capabilities are preserved through root-tool modes, resources, namespaced gateway operations, daemon APIs, CLI, desktop, mobile, hooks, plugins, and generated native files according to [`docs/architecture/CAPABILITY-ABSORPTION-MAP.md`](docs/architecture/CAPABILITY-ABSORPTION-MAP.md). They must not inflate the public root catalog.

## Transcript and repository audit

The two full transcripts were compared to the exact current repository and independently verified Phase 2 artifact. The audit found:

- the normal proven Rust source contains the CLI, engine, intelligence, MCP, and storage crates—but not the complete session/memory/adapter/run/policy/artifact/desktop/mobile product;
- several intelligence paths are foundations rather than full production implementations;
- the complete requested adapter, memory, handoff, run/subagent, materializer, SDK/plugin/editor, remote/mobile, and release surfaces were not yet fully represented in the roadmap;
- the old Phase 3 carrier and the real-repository Phase 3 plan conflict and cannot be executed as-is;
- `native/0.4.0-dev.5` is the most complete lineage, while historical branches remain evidence owners pending safe consolidation.

See:

- [`docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md`](docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md)
- [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)
- [`docs/operations/BRANCH-CONSOLIDATION-PLAN.md`](docs/operations/BRANCH-CONSOLIDATION-PLAN.md)

## Current phase: Phase 4

Phase 4 is not merely a branch rename. It must:

1. place the verified Rust source in a normal, reviewable repository tree;
2. run native gates directly from checkout;
3. consolidate the active lineage into `main` through a reviewed merge commit while preserving receipts;
4. implement the canonical data model for sessions, messages, memory, runs, approvals, artifacts, materializations, cursors, audit, and tombstones;
5. implement crash-durable storage, operation reservation, leases, recovery, backup, repair, encrypted artifacts, keychain and policy foundations;
6. complete the exact CLI, per-user service, typed IPC, install/doctor/backup/restore/uninstall lifecycle;
7. produce an independently verified unsigned alpha artifact.

## Deferred Phase 3

Phase 3 was deferred by owner direction and does not block implementation. It still gates efficacy claims and any later decision to set `productionClaimAllowed=true`.

Before it can be executed, it must be re-frozen as three arms:

```text
control_no_soleaux
historical_python
native_treatment
```

The obsolete GitHub Models/synthetic carrier must not be used as current product-proof evidence.

## Branch state

- `native/0.4.0-dev.5` is the canonical working lineage.
- documentation PR #4 was merged into native at `7af28901a67d7909a3442b0d22801ab3fe619293`.
- the transcript audit is being reviewed on a short-lived branch.
- draft PR #3 is obsolete and must be closed.
- `phase3/live-wedge...` is a subset/superseded working branch.
- `phase2/...` and `native-wedge/...` remain evidence branches until their receipt commits and artifacts are preserved through durable references.
- `main` remains the historical default until the consolidated merge-commit PR lands; Phase 4 remains in progress after that merge until normal Rust source and direct CI are proven.

## Exact next work

1. merge the transcript audit and expanded task registry into native;
2. close obsolete PR #3;
3. open and review the consolidated native-to-main merge-commit PR and preserve evidence references before branch cleanup;
4. materialize the verified native source as normal checked-in files and replace carrier CI;
5. execute Phase 4 durable state, service, security, CLI, install, backup, repair, and uninstall tasks;
6. proceed through the expanded Phase 5–8 task registry toward beta, applications, assurance, RC, and GA.

Status changes require reviewed planning evidence, exact receipts where applicable, and independent verification. Contracts and receipts outrank prose.
