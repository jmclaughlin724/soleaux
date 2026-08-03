# Branch Consolidation Record

**Date:** 2026-08-03

**Decision:** All remaining native / Phase 3 work consolidates onto a single working branch.

## Working Branch

`native/0.4.0-dev.5` (created from `phase3/live-wedge-0.4.0-dev.5` tip `5fa6c1d85639150393bd7b114e3206d38363282c`)

This branch contains:
- Full Phase 0 / Phase 1 / Phase 2 receipt chain
- Phase 2 independent verification and closure receipt (`PHASE2-CLOSURE-RECEIPT.json`)
- Phase 3 design freezes, six-task oracle set, governance/redaction fixtures, fail-closed harness unpacker, and verified live-wedge harness carrier

## Rules Going Forward

1. All new native work, Phase 3 harness execution, comparison artifacts, and receipts land only on this branch (or short-lived PR branches that merge back into it).
2. Do not create additional long-lived `phaseN/...` or `native-wedge/...` branches.
3. Do not merge this lineage into `main` until Phase 3 (live same-model wedge) successfully closes and `productionClaimAllowed` can be reconsidered.
4. Historical branches (`native-wedge/0.4.0-dev.4`, `phase2/native-lineage-a-0.4.0-dev.5`, `phase3/live-wedge-0.4.0-dev.5`) are retained for exact-commit receipt provenance only.

## Status at Consolidation

- Phase 2: CLOSED
- Phase 3: UNBLOCKED (design + harness carrier present; execution not yet complete)
- productionClaimAllowed: false
- publicToolCeiling: 12
