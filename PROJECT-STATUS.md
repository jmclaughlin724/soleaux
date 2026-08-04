# Soleaux Project Status

<!-- soleaux-docs:status current_phase=4 phase2=closed phase3=deferred_reconciliation_required phase4=in_progress version=0.4.0-dev.5 production_claim_allowed=false -->

**As of:** 2026-08-04  
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
Canonical branch:            main
Public MCP ceiling:          12
productionClaimAllowed:      false
Production readiness:        prohibited
```

Phases 0–2 prove the locked native repository-intelligence foundation. PRs #5 and #6 completed branch/source consolidation. PR #7 repaired LSP capability truthfulness. PRs #8 and #10 added a secured telemetry API and daemon-served dashboard foundation. These milestones do not prove the complete session, memory, adapter, desktop, mobile, orchestration, security, and distribution product described in the reviewed transcripts.

## Proven phases and consolidation

| Evidence | Outcome |
|---|---|
| Phase 0 source `a31820d26...`, workflow `30766171022` | Contracts and native foundation closed |
| Phase 1 source `d3eecd458...`, workflow `30773147694` | Exact twelve-tool catalog and Context Packet V2 closed |
| Phase 2 source `6768d9de...`, workflow `30818963313`, artifact `8858165328` | Gateway/catalog/adopt/attach/governance closed and independently verified |
| PR #5 | Receipt-bearing lineage merged to `main`; historical branches safely archived/tagged |
| PR #6 | Verified Rust source checked in under `native/`; carrier chain retired; direct native CI established |
| PR #7 | `inspect`/`navigate` now advertise and call only initialized LSP capabilities; regression tests added |
| PR #8 | Telemetry daemon bearer, origin/CORS, token-file, and process-argument redaction baseline |
| PR #10 | Static dashboard export served same-origin by the daemon; unsafe PR #9 closed |

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

Canonical tools:

```text
context.compile
code.search
memory.search
get_symbols
registry.list
registry.read
repo_info
navigate
inspect
preview
edit
restart_lsp
```

Broader capabilities remain required behind existing slot modes, MCP resources, namespaced gateway operations, daemon APIs, CLI, desktop/mobile operations, hooks/plugins, SDKs, and generated native files. See [`docs/architecture/CAPABILITY-ABSORPTION-MAP.md`](docs/architecture/CAPABILITY-ABSORPTION-MAP.md).

## Transcript-to-repository audit

Both complete transcripts were reconciled against current `main`, exact receipts, the independently verified source artifact, and merged PRs through #10.

The audit distinguishes implemented foundations from missing product systems and owns the expanded finish line:

- [`docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md`](docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md)
- [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)

## Current Phase 4 gate

Phase 4 now has three remaining work groups.

### A. Native correctness

Close `P4-017` through `P4-024` and `P4-026`:

- closed request schemas;
- fresh index/context claims;
- transactional edit and rollback integrity;
- pre-side-effect idempotency reservation;
- comprehensive secret redaction;
- transactional adopt/apply/revert;
- truthful bounded coverage and continuations;
- typed invalid-SQL semantics;
- atomic one-time preview claim and complete binding metadata.

`P4-025` is closed by PR #7.

### B. Durable native core

Implement `P4-010` through `P4-015`:

- canonical accounts/mappings/sessions/messages/memory/runs/approvals/artifacts/materializations/cursors/audit/tombstones;
- serialized writes, migrations, replay, backup, integrity repair and downgrade refusal;
- durable operation reservation, execution leases, process/native-session reconciliation, cancellation and approval recovery;
- encrypted content-addressed artifacts, keychain and policy/capability foundation;
- exact CLI with JSON and dry-run behavior;
- per-user service and typed local IPC with peer checks.

### C. Alpha closure

Produce a reproducible unsigned alpha with install/service/doctor/backup/restore/repair/uninstall smoke, exact Phase 4 receipt, and independent artifact verification.

## Deferred Phase 3

Phase 3 does not block implementation. Before it can support any efficacy claim, it must be re-frozen as:

```text
control_no_soleaux
historical_python
native_treatment
```

The no-Soleaux arm proves market value; the historical arm proves compatibility. Context savings cannot compensate for lower correctness. `productionClaimAllowed` remains false.

## Remaining program

| Phase | Purpose | Dependency |
|---:|---|---|
| 4 | Correct, durable native alpha foundation | In progress |
| 5 | Live adapters, canonical lifecycle, intelligence depth, materializers, SDK/provider/editor/automation | Phase 4 receipt |
| 6 | Tauri desktop, Expo mobile, secure remote control, installers and operations | Phase 5 receipt |
| 7 | Performance/scale/security/privacy/accessibility/OS/relay/enterprise assurance | Phase 6 receipt |
| 8 | Signed RC, stores, staged rollout, production-claim decision and GA | Phase 7 receipt |

## Update rule

Status changes require reviewed planning evidence for planned work and exact receipts plus independent verification for completed gates. Contracts and receipts outrank prose. A merge to `main` does not imply production readiness.
