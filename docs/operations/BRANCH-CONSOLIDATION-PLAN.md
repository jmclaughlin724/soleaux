# Soleaux Branch Consolidation Plan

**Status:** binding next-work plan  
**No force-push. No merge to `main` until Phase 4 exit gates pass.**

## Current branches

| Branch | Role | Action |
|---|---|---|
| `main` | Historical Python/FastMCP default branch | Keep unchanged until Phase 4 canonical-source PR |
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
3. Treat `native/0.4.0-dev.5` as the only working source branch for Phase 3 preparation and Phase 4 consolidation.
4. Do not execute the obsolete GitHub Models Phase 3 carrier. Replace it through the reviewed three-arm preregistration before live calls.
5. Remove the merged documentation branch after the audit PR lands and no open reference depends on it.

## Phase 4 cleanup

After the normal Rust source is checked in and the default-branch consolidation PR is independently verified:

1. create immutable tags or release references for Phase 0, Phase 1, and Phase 2 source/receipt commits;
2. verify all receipt URLs and artifact digests remain reachable;
3. merge the canonical native source and unified docs to `main` through review;
4. delete merged short-lived branches (`docs/...`, obsolete `phase3/...`);
5. archive or delete `native-wedge/...` and `phase2/...` only after their evidence is preserved through tags and status references;
6. keep no more than `main`, one active release branch, and short-lived task branches.

## Stop conditions

Stop branch cleanup when:

- a receipt references a branch-only object that has not been tagged or copied into the canonical tree;
- a branch contains unmerged implementation absent from `native/0.4.0-dev.5`;
- CI or contract validation is not green;
- deletion would remove the only accessible audit evidence.
