# Branch and Release Policy

## Branch authority

- `main` is the sole current implementation, status, roadmap, task, and release authority.
- Receipt-bearing history is merged without squash or force-push.
- Fully merged short-lived branches are pruned after their evidence is durable.
- Branches with unique commits are retained only as non-authoritative archival lineage until their commits are reviewed, merged, tagged, or explicitly superseded.
- The current classification is recorded in `BRANCH-CONSOLIDATION-2026-08-07.json`, which supersedes the 2026-08-05 report.

## Rules

- no force-push;
- no squash or rewrite of receipt-bearing ancestry;
- no deletion of unique commits or another session's work;
- no source-status claim from a receipt-only commit;
- no direct vendor-internal database writes;
- no release tag before the release checklist allows it;
- no production claim from branch state, merge state, test count, or an unsigned alpha.

## Pull requests

Every PR states current phase, task IDs, contracts touched or untouched, validations run, evidence level, documentation impact, and production-claim impact.

## Cleanup

A branch may be deleted only after its tip is proven to be an ancestor of `main`, or after its unique commits have been deliberately preserved and explicitly classified. Automated cleanup must fail closed on deletion errors and must never delete `main` or the branch executing the cleanup.

## Release artifacts

Development artifacts are labeled development and unsigned where applicable. Release-candidate, signed-distribution, store, and GA claims require the corresponding later phase receipts.
