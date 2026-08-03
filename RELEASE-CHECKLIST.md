# Soleaux Release Checklist

**Current version:** `0.4.0-dev.5`  
**Current release posture:** development only  
**productionClaimAllowed:** `false`

This checklist controls release eligibility. A completed development phase does not imply a signed release.

## Gate summary

| Gate | Required evidence | Current |
|---|---|---|
| Contracts | Locked schemas and drift checks | Green |
| Native foundation | fmt/check/Clippy/test/build/audit | Green through Phase 2 |
| Public surface | Exact 12 tools and substitutions | Green |
| Context contract | Context Packet V2 schema and fail-closed behavior | Green |
| Native capabilities | Gateway/catalog/adopt/attach/governance | Green |
| Live product proof | Same-model/same-task comparison | **Open** |
| Canonical source | Normal native source on reviewed default branch | Open |
| Live compatibility | Agent clients, LSPs, Turbo/Next, design partners | Open |
| Product apps | Desktop/mobile/installers | Open |
| Assurance | Benchmarks, security, privacy, accessibility, OS parity | Open |
| Distribution | Signing, notarization, stores, staged rollout | Open |
| Production claim | Explicit reviewed decision | Prohibited |

## Version ladder

```text
0.4.0-dev.x
    current; contracts/native core proven, live product proof incomplete

0.4.0-alpha.x
    eligible after Phase 3 and canonical-source consolidation begin

0.4.0-beta.x
    eligible after live client/repository matrices and product workflows

1.0.0-rc.x
    eligible only after assurance gates and signed candidate artifacts

1.0.0
    eligible only after staged rollout and explicit production-claim decision
```

## Required RC evidence

- Exact source commit and reproducible build.
- Locked contract digests.
- Full native gate logs.
- Live Phase 3 product-proof results.
- At least three design-partner repository results.
- Declared client and LSP compatibility matrix.
- Cold/warm p50/p95/p99 benchmarks on defined hardware.
- Security, privacy, license, and accessibility reviews.
- Install, upgrade, repair, rollback, uninstall evidence.
- Signed SBOM and provenance.
- Signed/notarized desktop artifacts.
- Mobile internal-distribution evidence.
- Incident response and rollback runbook.
- Known limitations and support policy.
- Explicit approval to change `productionClaimAllowed`.

## Prohibited current claims

Until the relevant gates pass, do not say:

- production-ready;
- generally available;
- release candidate;
- proven to improve model correctness;
- universal native session resume;
- complete cross-client memory synchronization;
- fully compatible with every LSP/client/OS;
- signed or store-ready.

See [`docs/governance/CLAIMS-POLICY.md`](docs/governance/CLAIMS-POLICY.md).
