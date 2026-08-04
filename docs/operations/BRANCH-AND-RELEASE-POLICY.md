# Branch and Release Policy

## Current branch roles

- `native/0.4.0-dev.5` is the canonical working lineage until its reviewed merge commit lands on `main`.
- the transcript audit branch is short-lived and must merge into native.
- `main` is the historical default and becomes the canonical project branch after the consolidated merge commit; Phase 4 remains open until normal Rust source/direct CI pass.
- `phase2/...`, `native-wedge/...`, and the obsolete Phase 3 branch are evidence/history branches until durable receipt/archive references preserve every required object.
- the merged documentation branch is removable after the audit PR and its references are preserved.

See `BRANCH-CONSOLIDATION-PLAN.md`.

## Rules

- no force-push or squash of receipt-bearing ancestry;
- no deletion of evidence branches before durable references and artifact links are verified;
- no source-status claim from a receipt-only commit;
- no release tag before the release checklist allows it;
- no new long-lived phase branch; use short-lived task branches into the canonical branch;
- merge to `main` does not imply alpha, production readiness, or `productionClaimAllowed=true`.

## Pull requests

Every PR states phase, task IDs, contract impact, validation, evidence level, documentation impact, branch impact, and production-claim impact.

## Release artifacts

Development artifacts remain clearly labeled and unsigned where applicable. `productionClaimAllowed` changes only through the reviewed Phase 8 decision after efficacy and assurance evidence.
