# Soleaux Project Status

<!-- soleaux-docs:status current_phase=5 phase2=closed phase3=deferred_reconciliation_required phase4=closed phase5=in_progress version=0.4.0-dev.5 production_claim_allowed=false -->

**As of:** 2026-08-07
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
Phase 4:                     CLOSED
Phase 5:                     IN PROGRESS
Phase 6:                     BLOCKED BY PHASE 5
Phase 7:                     BLOCKED BY PHASE 6
Phase 8:                     BLOCKED BY PHASE 7
Canonical branch:            main
Public MCP ceiling:          12
Unsigned alpha:              reproducible and independently verified
productionClaimAllowed:      false
Production readiness:        prohibited
```

## Phase 4 closure

Phase 4 closed the correct, durable native-alpha foundation on implementation source `34c394efd01c9bb5348ba38e505317e6ca4da190` and merge `f450c1a9cd2cd74d366324b5e2031e4751fb5942`.

| Evidence | Outcome |
|---|---|
| Exact alpha workflow `31026328918` | Native fmt/check/strict Clippy/tests/release/audit, canonical and substituted MCP smokes, deterministic packaging, archive verification, and operational smoke passed |
| Artifact `8939137324` | Checksummed Phase 4 evidence and reproducible unsigned alpha |
| [`PHASE4-ALPHA-CLOSURE-RECEIPT.json`](PHASE4-ALPHA-CLOSURE-RECEIPT.json) | Exact producing-workflow receipt |
| [`PHASE4-INDEPENDENT-VERIFICATION.json`](PHASE4-INDEPENDENT-VERIFICATION.json) | Independent ZIP, checksum, source, contract, binary, package, and lifecycle verification |
| [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json) | Final phase closure and documentation convergence |

The closed implementation includes:

- strict request-schema validation;
- watcher-backed fresh reads and stale-evidence rejection;
- transactional edit rollback and reconciliation receipts;
- pre-side-effect idempotency and exact result replay;
- shared comprehensive secret redaction;
- transactional adopt, attach, and revert;
- truthful coverage gaps, truncation, and continuations;
- typed invalid-PostgreSQL validation;
- truthful LSP capability advertisement;
- atomic one-time preview claims and immutable binding;
- canonical accounts, mappings, sessions, turns, messages, memory claims, handoffs, runs, approvals, artifacts, cursors, audit, tombstones, and retention;
- serialized persistence, migrations, recovery, backup, restore, integrity repair, and leases;
- encrypted content-addressed artifacts and deny-by-default capability policy;
- stable CLI, per-user service, typed local IPC, peer checks, and concurrent clients;
- reproducible unsigned development-alpha packaging with deterministic Cargo SBOM;
- clean install, daemon restart, doctor, backup, export, repair, offline restore, and state-preserving uninstall.

## Phase 5 progress

P5-001 is closed on product source `1744424444d08d6ee380dc40c948db86a626ee04` and merge `f231b86f581b7f3d5d081ed4b8d235a72758342a`. The daemon-owned registry now converges canonical workspace identity, CLI/desktop/editor/adapter registrations, leases, trust and compatibility restrictions, restart persistence, bounded IPC pages and mutation summaries, and transactional attach/revert behavior across Linux and macOS.

Evidence: [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json).

P5-002 through P5-006 are closed on implementation merge `c51265a3927f3b163b9cac686e0572b47da48b8c` (PR #38) and remediation merge `de6c813ee9b8bd0cb441482b0debbf323c4ea133` (PR #40). The daemon now embeds the six-platform client capability matrix with pinned artifact verification, bounded signal-oracled probes, revision-guarded revalidation, and safe-mode read-only admission for every external client; the mutation-eligible set is empty pending a daemon-trusted receipt verifier.

Evidence: [`P5-002-P5-006-CLOSURE-RECEIPT.json`](P5-002-P5-006-CLOSURE-RECEIPT.json).

The remaining Phase 5 work is consolidated in [`docs/plans/PHASE5-IMPLEMENTATION-PLAN.md`](docs/plans/PHASE5-IMPLEMENTATION-PLAN.md) with machine-readable dependencies in [`docs/plans/PHASE5-DEPENDENCIES.json`](docs/plans/PHASE5-DEPENDENCIES.json), including the registered pre-task P5-V1 (admission receipt verifier). P5-W1 crate wiring is closed (PR #44; [`P5-W1-CLOSURE-RECEIPT.json`](P5-W1-CLOSURE-RECEIPT.json)). P5-007 is closed on merges `85d3382929a13b0fdb1e9588f9fe3f7eb82a5183` (PR #46) and `ab7d707fb47d9bfc96374d76e63ad0f68e77e2de` (PR #47): the canonical session/history service with the validated active/archived state machine, ordinal-ordered turns and messages over typed IPC, race-free ordinal claims, bounded lineage, the memory.search session-scope canonical section, and the `soleaux://sessions` resources; evidence: [`P5-007-CLOSURE-RECEIPT.json`](P5-007-CLOSURE-RECEIPT.json).

The foundations wave is closed: P5-008 (materializer compiler, PR #53, merge `dd154ac5e88e1106458ddeacb4d76f2b3ddd73a5`), P5-010 (Turbo/Next version matrices, PR #52, merge `41dfb78b890ae886e294a094ddb0bf27a08f675c`), and P5-021 (real Oxc extraction + tree-sitter query packs, PR #55, merge `5d96813c1209f79065ba0f36387f1623affce5ba`); evidence: [`P5-008-CLOSURE-RECEIPT.json`](P5-008-CLOSURE-RECEIPT.json), [`P5-010-CLOSURE-RECEIPT.json`](P5-010-CLOSURE-RECEIPT.json), [`P5-021-CLOSURE-RECEIPT.json`](P5-021-CLOSURE-RECEIPT.json). P5-009 milestone A (seventeen-family LSP table, PR #51) and the P5-V1 admission-verifier milestone (PR #54, merge `e3510ceb39e391acfe3b1fc13c07fcc8548bbf14`) are merged; their checkboxes close at matrix v2 and at per-client lifecycle-oracle enablement respectively. External runtime write admission now has its daemon-trusted receipt verifier; `mutationEligible` remains empty pending per-client reviewed enablement. The next open implementation task is **P5-009**.

## Locked invariants

```text
Unified MCP profile SHA-256:
89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc

Context Packet V2 SHA-256:
3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f

PRODUCT_VERSION:              0.4.0-dev.5
PRODUCTION_CLAIM_ALLOWED:     false
HARD_CEILING:                 12
```

## Current Phase 5 gate

Phase 5 owns four work groups.

### A. Live platform adapters

- Claude Code Agent SDK, SessionStore, hooks, permissions, compaction, and restart reconciliation.
- Claude Desktop supported local connector and export/import boundaries.
- Codex generated app-server client, approvals, steering, compaction, archive, cursors, reconnect, and safe mode.
- OpenCode generated OpenAPI client, persistent SSE cursor/reconciliation, permissions, and plugin/session lifecycle.
- Cursor and generic MCP-host verification.

### B. Canonical lifecycle and orchestration

- Same-platform resume, fork, and archive where supported.
- Full memory proposal, validation, activation, conflict, supersession, expiry, tombstone, import/export, and compaction lifecycle.
- Signed cross-platform handoffs with Git/code state, artifacts, exclusions, permissions, and destination-native lineage.
- Durable runs and subagents with budgets, worktree leases, attenuated capabilities, approvals, recovery, cancellation propagation, and aggregation.
- Compatibility materializers with diff, backup, atomic apply, rollback, echo guards, and load verification.

### C. Intelligence and extensibility depth

- Complete Oxc, Tree-sitter, LibCST, shell, LSP, Turbo, and Next.js production matrices.
- Versioned parser, graph, route, context, materializer, and gateway provider interfaces.
- Stable Rust API and generated Python/TypeScript daemon SDKs.
- Deterministic `soleaux ci`, editor extension, and capability-gated event export.
- Optional hybrid search only after licensing, sensitivity, migration, and corruption-recovery gates.

### D. Real repositories and evidence

- Validate `jmclaughlin724/anilize`.
- Validate two additional approved design partners.
- Close Phase 5 only with an exact beta receipt and independent verification.

## Deferred Phase 3

Phase 3 remains deferred and does not block implementation. Before any quantified efficacy claim, freeze and execute:

```text
control_no_soleaux
historical_python
native_treatment
```

The same authenticated model/client, tasks, budgets, sampling, protocol, retries, oracles, and scoring must apply to every arm. Context economy cannot compensate for lower correctness. `productionClaimAllowed` remains false.

## Remaining program

| Phase | Purpose | Dependency |
|---:|---|---|
| 5 | Adapters, lifecycle, intelligence depth, materializers, SDKs, design partners | In progress |
| 6 | Tauri desktop, Expo mobile, secure remote control, installers, and operations | Phase 5 receipt |
| 7 | Performance, scale, security, privacy, accessibility, OS/relay/enterprise assurance | Phase 6 receipt |
| 8 | Signed RC, stores, staged rollout, explicit production-claim decision, and GA | Phase 7 receipt |

## Branch state

`main` is the sole implementation and status authority. Fully merged short-lived branches were pruned. Branches with unique commits were retained as non-authoritative archival lineage and are enumerated in [`docs/operations/BRANCH-CONSOLIDATION-2026-08-07.json`](docs/operations/BRANCH-CONSOLIDATION-2026-08-07.json), which supersedes the 2026-08-05 report and also records the archived never-pushed local lineage tags.

## Update rule

Status changes require coordinated updates to machine status, human status, roadmap, tasks, handoff, changelog, audit registry, release gates, and public claims. Contracts and exact receipts outrank prose. A merge does not imply production readiness.
