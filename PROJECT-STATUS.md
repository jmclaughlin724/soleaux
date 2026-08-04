# Soleaux Project Status

<!-- soleaux-docs:status current_phase=3 phase2=closed phase3=unblocked_not_started version=0.4.0-dev.5 production_claim_allowed=false -->

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
Phase 3:                     UNBLOCKED, NOT STARTED
Public MCP ceiling:          12
productionClaimAllowed:      false
Production readiness:        prohibited
```

The immediate product gate is a live same-model, same-task comparison. No additional implementation phase may be treated as a substitute for that evidence.

## Proven phases

| Phase | Outcome | Exact source | Workflow | Evidence |
|---:|---|---|---:|---|
| 0 | Contracts locked; native build foundation green | `a31820d26f46d258175b52fe30fdbecf7b650265` | `30766171022` | `PHASE0-NATIVE-GATE-RECEIPT.json` |
| 1 | Exact 12-tool catalog and `soleaux.context/v2` green | `d3eecd45867e82d5777e57753c581483971214dd` | `30773147694` | `PHASE1-NATIVE-GATE-RECEIPT.json` |
| 2 | Gateway, catalog domains, adopt/attach, governance green | `6768d9de2aa8a61ba90356409033c0d69b2d5afc` | `30818963313` | `PHASE2-CLOSURE-RECEIPT.json` + `PHASE2-INDEPENDENT-VERIFICATION.json` |

Phase 2 independent verification recorded artifact SHA-256 `3fa99fa2de889c7eb081e8ff2a913e66cb7c2027a1696f6ad4eb1c0d0b963ebe`, exact 12-tool canonical and substituted profiles, binary execution, clean Clippy/audit evidence, Context Packet V2 validation, and native gateway/catalog/adopt/attach/governance smokes.

## Locked invariants

```text
Unified MCP profile:
89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc

Context Packet V2:
3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f

PRODUCT_VERSION:
0.4.0-dev.5

PRODUCTION_CLAIM_ALLOWED:
false

HARD_CEILING:
12
```

Canonical tool order:

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

## Current gate: Phase 3

Phase 3 must prove that Soleaux produces equal-or-better final task correctness with measurable context/tool reduction under the same model, client, tasks, budgets, and scoring method.

The experiment package is under:

```text
docs/experiments/phase3/
```

The first live call is blocked until the model ID, client build, sampling parameters, credentials, and oracle dry-run are recorded and the experiment status changes from `draft_blocked` to `frozen_ready`.

## Remaining program

| Phase | Purpose | Current dependency |
|---:|---|---|
| 3 | Live product-proof experiment | Model/client lock and execution |
| 4 | Canonical native source and default-branch consolidation; alpha foundation | Phase 3 |
| 5 | Shared service, live adapters, memory/handoff depth, consumer onboarding | Phase 4 |
| 6 | Desktop/mobile/installers and operational UX | Phase 5 |
| 7 | Compatibility, benchmark, security, privacy, accessibility, OS parity | Phase 6 |
| 8 | RC and GA rollout | Phase 7 |

See [ROADMAP.md](ROADMAP.md) and [TASKS.md](TASKS.md).

## Branch and release state

The historical default branch still contains the Python/FastMCP `0.1.0` lineage. The proven native implementation and receipts live on `phase2/native-lineage-a-0.4.0-dev.5`. The documentation consolidation is being prepared on `docs/unified-project-system-0.4.0-dev.5`.

This documentation change does not merge the native source to `main` and does not authorize a production release. Canonical-source consolidation is Phase 4.

## Update rule

This document may change status only when one of the following exists:

1. a successful exact-commit workflow receipt;
2. an independent verification receipt;
3. a reviewed planning decision that does not claim completed evidence.

When evidence and prose conflict, the machine receipts and locked contracts win.
