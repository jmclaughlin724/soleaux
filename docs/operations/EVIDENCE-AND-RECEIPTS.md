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

## External evidence receipts

Externally produced evidence — a penetration-test report, a legal or privacy opinion, a certified accessibility audit, a notarization ticket, a store review outcome — has no workflow run or CI artifact, so it is recorded through an external-evidence receipt validating against [`EXTERNAL-EVIDENCE-RECEIPT.schema.json`](EXTERNAL-EVIDENCE-RECEIPT.schema.json) (`soleaux.external-evidence-receipt/v1`).

Every external-evidence receipt records:

- the gate and task it satisfies (for example `P7-004`);
- the independent organization or signer and the engagement scope;
- the exact repository commit the assessment was performed against;
- the report or artifact SHA-256 and its retention location;
- the assessment date, expiry, and retest policy;
- findings counts by severity and the remediation status;
- prohibited substitutions confirmed absent (test keys, mock stores, dry runs, simulated cohorts, internal self-reports).

External receipts follow the same immutability rule as gate receipts, and a gate that requires external evidence is green only when its receipt validates against the schema.

## Readiness decision (P7-010)

The Phase 7 readiness decision is a recorded owner go/no-go review over the complete assurance evidence set: every internal hardening receipt green, every required external report recorded through a valid external-evidence receipt, the open-risk register empty or each remaining risk explicitly accepted in writing, and the rollback exercise receipt verified. The decision itself is persisted as a receipt naming the reviewer, the date, the evidence enumerated, and the outcome; a no-go names the blocking evidence.

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
