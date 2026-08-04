# Soleaux Branch Consolidation Plan

**Status:** binding next-work plan  
**No force-push. Merge to `main` only through the reviewed consolidation merge commit; Phase 4 remains open afterward until normal Rust source and direct CI pass.**

## Current branches

| Branch | Role | Action |
|---|---|---|
| `main` | Historical default branch that must become the canonical project branch through the reviewed consolidation merge | Merge the receipt-preserving native lineage and unified docs now; keep Phase 4 marked in progress |
| `native/0.4.0-dev.5` | Most complete native lineage; includes Phase 2 closure and later installer/Phase 3 harness work | **Canonical working branch now** |
| `docs/unified-project-system-0.4.0-dev.5` | Standardized docs and roadmap | Merged by PR #4 at `7af28901...`; remove after this audit is merged and references are preserved |
| `phase3/live-wedge-0.4.0-dev.5` | Earlier Phase 3 subset | Superseded by `native/0.4.0-dev.5`; freeze and remove after archival tag/reference |
| `phase2/native-lineage-a-0.4.0-dev.5` | Phase 2 evidence lineage | Freeze read-only until Phase 4 preserves tags/artifact references |
| `native-wedge/0.4.0-dev.4` | Phase 0/1 historical carrier/evidence | Freeze read-only until Phase 4 preserves tags/artifact references |

## Pull requests

| PR | Current role | Action |
|---:|---|---|
| #3 | Obsolete native-wedge draft into `main` | Close as superseded; never merge |
| #4 | Unified documentation | **Merged** into `native/0.4.0-dev.5` at `7af28901a67d7909a3442b0d22801ab3fe619293` |

## Immediate sequence

1. Merge the transcript/repository audit branch into `native/0.4.0-dev.5` after green documentation and repository CI.
2. Close PR #3 as superseded by exact Phase 0–2 receipts and the native branch.
3. Open and merge the receipt-preserving `native/0.4.0-dev.5` → `main` consolidation PR; then use `main` plus short-lived task branches as the only working line.
4. Do not execute the obsolete GitHub Models Phase 3 carrier. Replace it through the reviewed three-arm preregistration before live calls.
5. Create durable receipt/archive references, then remove merged documentation and obsolete Phase 3 branches when no evidence depends on branch names.

## Phase 4 cleanup

After the consolidated lineage is merged to `main`, complete Phase 4 by checking in the normal Rust source and verifying direct native CI:

1. create immutable tags or release references for Phase 0, Phase 1, and Phase 2 source/receipt commits;
2. verify all receipt URLs and artifact digests remain reachable;
3. confirm the consolidated native lineage and unified docs are reachable from `main`;
4. delete merged short-lived branches (`docs/...`, obsolete `phase3/...`) after archival references exist;
5. archive or delete `native-wedge/...` and `phase2/...` only after their evidence is preserved through tags and status references;
6. keep no more than `main`, one active release branch, and short-lived task branches.

## Stop conditions

Stop branch cleanup when:

- a receipt references a branch-only object that has not been tagged or copied into the canonical tree;
- a branch contains unmerged implementation absent from `native/0.4.0-dev.5`;
- CI or contract validation is not green;
- deletion would remove the only accessible audit evidence.
