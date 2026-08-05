# Release Gates

## Gate ownership

A release gate is green only with an exact receipt and, where required, independent verification.

| Gate | Phase | Status | Blocks |
|---|---:|---|---|
| Contract integrity | 0 | Green | all releases |
| Native build and surface | 1 | Green | all releases |
| Native product capabilities | 2 | Green | all releases |
| Live product proof | 3 | Deferred/open | quantified efficacy claims |
| Canonical durable installable alpha | 4 | Green | alpha distribution evidence |
| Live adapters and design partners | 5 | In progress | beta |
| Desktop/mobile/installers | 6 | Blocked | product distribution |
| Assurance and parity | 7 | Blocked | RC |
| Signed staged rollout | 8 | Blocked | GA |

## Current Phase 4 evidence

- [`../../PHASE4-ALPHA-CLOSURE-RECEIPT.json`](../../PHASE4-ALPHA-CLOSURE-RECEIPT.json)
- [`../../PHASE4-INDEPENDENT-VERIFICATION.json`](../../PHASE4-INDEPENDENT-VERIFICATION.json)
- [`../../PHASE4-CLOSURE-RECEIPT.json`](../../PHASE4-CLOSURE-RECEIPT.json)

The Phase 4 gate proves a reproducible unsigned development alpha and complete extracted-package operational lifecycle. It does not prove signed distribution or general availability.

## Claims versus gates

| Claim | Earliest gate |
|---|---|
| “Native Rust development build” | Phase 0 |
| “Exact 12-tool unified surface” | Phase 1 |
| “Gateway/catalog/adopt/attach/governance are native” | Phase 2 |
| “Reproducible unsigned development alpha” | Phase 4 |
| “Measured lower context waste at equal-or-better success” | Phase 3 |
| “Validated with declared clients and design partners” | Phase 5 |
| “Desktop/mobile distribution candidate” | Phase 6 |
| “Release candidate” | Phase 7 |
| “Generally available” | Phase 8 |

## Receipt requirements

Every phase receipt records schema version, exact source commit, workflow/run ID, conclusion, artifact identity/digest, product version, production claim, public ceiling, and next phase state.

Independent verification records downloaded artifact digest, ZIP/source integrity, embedded checksums, source commit, locked contracts, binary execution, required smokes, and phase-specific capability checks.
