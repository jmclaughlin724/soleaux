# Evidence and Receipts

## Exact gate receipt

An exact gate receipt identifies source commit, workflow and run ID, conclusion, artifact identity, version, public ceiling, production-claim state, and phase-specific capability results.

## Independent verification

Independent verification obtains the producing artifact separately and checks:

- GitHub-reported and independently calculated artifact digest;
- ZIP integrity and embedded checksums;
- source archive integrity and path safety;
- exact source commit;
- locked contract hashes;
- binary presence and execution;
- canonical and substituted tool lists;
- schema validation;
- phase capability and operational results.

## Phase 4 evidence

| Evidence | Value |
|---|---|
| Implementation source | `34c394efd01c9bb5348ba38e505317e6ca4da190` |
| Merge commit | `f450c1a9cd2cd74d366324b5e2031e4751fb5942` |
| Alpha workflow | `31026328918` |
| Artifact | `8939137324` |
| Artifact SHA-256 | `9917b2dd8335b2d17cdb3f0dc15191699ec7f0d1ff431f9e3b2135e0a1f4684b` |
| Exact receipt | [`../../PHASE4-ALPHA-CLOSURE-RECEIPT.json`](../../PHASE4-ALPHA-CLOSURE-RECEIPT.json) |
| Independent verification | [`../../PHASE4-INDEPENDENT-VERIFICATION.json`](../../PHASE4-INDEPENDENT-VERIFICATION.json) |
| Final closure | [`../../PHASE4-CLOSURE-RECEIPT.json`](../../PHASE4-CLOSURE-RECEIPT.json) |

## Status rule

A phase may be implemented but open, exact-gate proven but awaiting independent verification, or closed only after every required evidence layer and documentation convergence pass.

## Immutability

Do not rewrite or delete receipts. Correct errors through a later receipt that identifies superseded evidence.
