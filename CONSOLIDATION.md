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

1. All new native work lands only on this branch until the merge to `main`, after which `main` is the single branch.
2. Do not create additional long-lived `phaseN/...` or `native-wedge/...` branches.
3. Amended 2026-08-03 by owner direction: the merge into `main` proceeds through one reviewed merge-commit pull request. The Phase 3 live wedge is deferred as an optional claims-gate and no longer blocks the merge.
4. Historical branches (`native-wedge/0.4.0-dev.4`, `phase2/native-lineage-a-0.4.0-dev.5`, `phase3/live-wedge-0.4.0-dev.5`, `docs/unified-project-system-0.4.0-dev.5`) are deleted after their exact receipt commits are preserved through annotated `receipts/*` and `archive/*` tags; any branch is recreatable from its archive tag.

## Status at Consolidation (amended 2026-08-03)

- Phase 2: CLOSED
- Phase 3: DEFERRED (optional claims-gate; the Grok-era fixture harness is removed from the tree)
- Phase 4: IN PROGRESS (canonical source consolidation)
- productionClaimAllowed: false
- publicToolCeiling: 12
