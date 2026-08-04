# Soleaux Project Status

<!-- soleaux-docs:status current_phase=4 phase2=closed phase3=deferred phase4=in_progress version=0.4.0-dev.5 production_claim_allowed=false -->

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
Phase 3:                     DEFERRED (optional claims-gate)
Phase 4:                     IN PROGRESS
Public MCP ceiling:          12
productionClaimAllowed:      false
Production readiness:        prohibited
```

By owner direction on 2026-08-03, the live same-model comparison no longer
blocks the program. It remains available as an optional experiment that gates
efficacy claims only. The immediate work is canonical source consolidation.

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

## Current gate: Phase 4

Phase 4 materializes the independently verified Phase 2 native source as a
normal in-tree Rust workspace, replaces carrier-only assembly with
direct-checkout native CI, archives the Python lineage as history and
conformance fixtures, and produces a reproducible unsigned alpha package.

The verified source is preserved from CI artifact `8858165328`
(SHA-256 `3fa99fa2de889c7eb081e8ff2a913e66cb7c2027a1696f6ad4eb1c0d0b963ebe`).

## Remaining program

| Phase | Purpose | Current dependency |
|---:|---|---|
| 3 | Live product-proof experiment (optional; efficacy claims only) | Owner reactivation |
| 4 | Canonical native source and default-branch consolidation; alpha foundation | In progress |
| 5 | Shared service, live adapters, memory/handoff depth, consumer onboarding | Phase 4 |
| 6 | Desktop/mobile/installers and operational UX | Phase 5 |
| 7 | Compatibility, benchmark, security, privacy, accessibility, OS parity | Phase 6 |
| 8 | RC and GA rollout | Phase 7 |

See [ROADMAP.md](ROADMAP.md) and [TASKS.md](TASKS.md).

## Branch and release state

All lineages consolidate into `main` through one reviewed merge-commit pull
request. Receipt provenance is preserved through annotated tags
(`receipts/*`, `archive/*`) before the historical phase branches are deleted.
The native binaries remain published as pre-release `native-v0.4.0-dev.5`.

This consolidation does not authorize a production release.
`productionClaimAllowed` remains false until an explicit reviewed owner
decision.

## Update rule

This document may change status only when one of the following exists:

1. a successful exact-commit workflow receipt;
2. an independent verification receipt;
3. a reviewed planning decision that does not claim completed evidence.

When evidence and prose conflict, the machine receipts and locked contracts win.
