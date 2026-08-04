# Branch and Release Policy

## Branches

- `main` currently preserves the historical public lineage.
- `phase2/native-lineage-a-0.4.0-dev.5` contains proven native phase evidence.
- documentation consolidation occurs on a dedicated branch.
- Phase 4 will create the reviewed canonical native-source branch.

## Rules

- no force-push;
- no merge to `main` without explicit review;
- no deletion of evidence branches;
- no source-status claim from a receipt-only commit;
- no release tag before the release checklist allows it.

## Pull requests

Every PR states:

- current phase;
- task IDs;
- contracts touched or untouched;
- validations run;
- evidence level;
- docs updated;
- production-claim impact.

## Release artifacts

Development artifacts are labeled development, unsigned where applicable, and not marketed as release candidates.
