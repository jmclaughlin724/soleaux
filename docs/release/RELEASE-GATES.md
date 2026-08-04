# Release Gates

## Gate ownership

A release gate is green only with an exact receipt and, where required, independent verification.

| Gate | Phase | Blocks |
|---|---:|---|
| Contract integrity | 0 | all releases |
| Native build and surface | 1 | all releases |
| Native product capabilities | 2 | all releases |
| Live product proof | 3 | alpha claims and product-efficacy claims |
| Canonical source and installable alpha | 4 | alpha distribution |
| Live adapters and design partners | 5 | beta |
| Desktop/mobile/installers | 6 | product distribution |
| Assurance and parity | 7 | RC |
| Signed staged rollout | 8 | GA |

## Claims versus gates

| Claim | Earliest gate |
|---|---|
| “Native Rust development build” | Phase 0 |
| “Exact 12-tool unified surface” | Phase 1 |
| “Gateway/catalog/adopt/attach/governance are native” | Phase 2 |
| “Measured lower context waste at equal-or-better success” | Phase 3 |
| “Alpha installable product” | Phase 4 |
| “Validated with declared clients and design partners” | Phase 5 |
| “Desktop/mobile distribution candidate” | Phase 6 |
| “Release candidate” | Phase 7 |
| “Generally available” | Phase 8 |

## Receipt requirements

Every phase receipt records:

- schema version;
- exact source commit;
- workflow name and run ID;
- conclusion;
- artifact name/ID/digest;
- product version;
- production claim;
- public ceiling;
- next phase state.

Independent verification records:

- downloaded artifact digest;
- ZIP and source archive integrity;
- embedded checksums;
- exact source commit;
- locked contract hashes;
- binary execution;
- required smokes;
- phase-specific capabilities.
