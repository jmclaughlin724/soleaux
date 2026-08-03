# Evidence and Receipts

## Exact gate receipt

An exact gate receipt identifies:

- source commit;
- workflow and run ID;
- conclusion;
- artifact name/link;
- version;
- public ceiling;
- production-claim state.

## Independent verification

Independent verification downloads the artifact outside the producing workflow and checks:

- GitHub-reported digest;
- local ZIP digest and integrity;
- embedded checksums;
- source archive integrity and path safety;
- exact source commit;
- locked contract hashes;
- binary presence and execution;
- canonical and substituted tool lists;
- schema validation;
- phase capability results.

## Status rule

A phase may be:

- implemented but open;
- exact-gate proven but awaiting independent verification;
- closed only after every required evidence layer passes.

## Immutability

Do not rewrite or delete receipts. Correct errors through a later receipt that identifies the superseded evidence.
