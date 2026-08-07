# Soleaux Release Checklist

## Current classification

```text
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     DEFERRED — CLAIMS GATE
Phase 4:                     CLOSED
Phase 5:                     IN PROGRESS
Unsigned development alpha:  independently verified
Signed distribution:         not available
productionClaimAllowed:      false
```

## Immutable release identity

- [x] Product identity is Soleaux.
- [x] Version remains `0.4.0-dev.5`.
- [x] Public MCP hard ceiling is 12.
- [x] Optional providers replace one slot and never append.
- [x] Unified MCP profile digest is locked.
- [x] Context Packet V2 digest is locked.
- [x] `productionClaimAllowed` remains false.

## Phase evidence

- [x] Phase 0 exact receipt.
- [x] Phase 1 exact receipt.
- [x] Phase 2 exact receipt and independent verification.
- [x] Phase 4 alpha exact receipt.
- [x] Phase 4 independent artifact verification.
- [x] Phase 4 final closure and synchronized default-branch documentation.
- [ ] Deferred Phase 3 three-arm live proof.
- [ ] Phase 5 beta receipt and independent verification.
- [ ] Phase 6 app/device/install receipt.
- [ ] Phase 7 assurance receipt.
- [ ] Phase 8 signed-release and rollout receipt.

## Native development-alpha gate — green

- [x] Rust format, check, strict Clippy, complete tests, release build, and Cargo audit.
- [x] Canonical and every optional-substitution MCP smoke at exactly 12 tools.
- [x] Context Packet V2 validation.
- [x] Canonical state, migrations, leases, replay, backup, restore, and repair.
- [x] Encrypted artifact vault and deny-by-default policy.
- [x] Stable CLI, per-user service, typed IPC, peer checks, and concurrent clients.
- [x] Deterministic package metadata and path-independent Cargo SBOM.
- [x] Two byte-identical independently built archives.
- [x] Extracted-package install, launch, restart, doctor, backup, export, repair, restore, and uninstall.
- [x] State preserved by default on uninstall.
- [x] Exact receipt and independent artifact verification.

Evidence:

- `PHASE4-ALPHA-CLOSURE-RECEIPT.json`
- `PHASE4-INDEPENDENT-VERIFICATION.json`
- `PHASE4-CLOSURE-RECEIPT.json`

## Phase 5 beta gate — open

- [x] Installed service/workspace registry converges across concurrent client classes. Evidence: `P5-001-CLOSURE-RECEIPT.json`.
- [x] Claude Code matrix green. Evidence: `P5-002-P5-006-CLOSURE-RECEIPT.json`.
- [x] Claude Desktop supported boundary matrix green. Evidence: `P5-002-P5-006-CLOSURE-RECEIPT.json`.
- [x] Codex CLI/Desktop app-server matrix green. Evidence: `P5-002-P5-006-CLOSURE-RECEIPT.json`.
- [x] OpenCode OpenAPI/SSE/plugin matrix green. Evidence: `P5-002-P5-006-CLOSURE-RECEIPT.json`.
- [x] Cursor and generic MCP-host matrix green. Evidence: `P5-002-P5-006-CLOSURE-RECEIPT.json`.
- [ ] Canonical session/history lifecycle green.
- [ ] Memory lifecycle and compaction survival green.
- [ ] Signed handoffs and destination-native lineage green.
- [ ] Durable runs/subagents/approvals/cancellation/recovery green.
- [ ] Rules/skills/agents materializer diff/apply/rollback/load verification green.
- [ ] Real LSP, Turbo, and Next matrices green.
- [ ] `anilize` plus two approved design partners green.
- [ ] Provider interfaces, SDKs, deterministic CI, and editor MVP green.
- [ ] Exact Phase 5 receipt and independent verification.

## Deferred Phase 3 claims gate — open

Before measured efficacy claims:

- [ ] Freeze the no-Soleaux, historical Python, and native arms.
- [ ] Freeze exact authenticated model/client/build/protocol/sampling/budgets/retries.
- [ ] Freeze tasks, prompts, rubrics, oracles, and hashes.
- [ ] Run all arms and retain every attempt/failure.
- [ ] Require equal-or-better correctness before considering context economy.
- [ ] Independently verify results.
- [ ] Keep `productionClaimAllowed=false` unless explicitly reviewed later.

## Phase 6 distribution candidate gate — open

- [ ] Tauri desktop and Expo mobile complete.
- [ ] Pairing, LAN, E2E relay fallback, revoke, push, and audit complete.
- [ ] macOS, Windows, and Linux development installers complete.
- [ ] Upgrade, repair, rollback, uninstall, and native-file restoration complete.
- [ ] Desktop/device E2E and accessibility-ready UX complete.

## Phase 7 assurance gate — open

- [ ] Defined-hardware performance matrix.
- [ ] Parser/LSP/protocol fuzzing.
- [ ] Large-repository and resource-pressure matrix.
- [ ] Security, prompt-injection, path-jail, shell, update, pairing, and cross-workspace tests.
- [ ] External penetration test.
- [ ] Privacy, retention, deletion, license, accessibility, and internationalization reviews.
- [ ] OS/architecture parity.
- [ ] Signed SBOM and provenance.
- [ ] Incident, outage, backup/restore, upgrade/downgrade, and rollback exercises.

## Phase 8 release gate — open

- [ ] Freeze `1.0.0-rc.1` only after Phase 7.
- [ ] Sign and notarize desktop artifacts and sign Windows packages.
- [ ] TestFlight and Play internal/staged delivery.
- [ ] Design-partner then public staged rollout with rollback thresholds.
- [ ] Publish release notes, support policy, compatibility, privacy, and known limitations.
- [ ] Explicitly review `productionClaimAllowed`.
- [ ] Verify GA and publish `1.0.0`.

## Prohibited conclusions

The verified unsigned development alpha does not establish:

- quantified model-success or context-savings claims;
- universal client, LSP, framework, OS, or repository compatibility;
- completed desktop/mobile distribution;
- signing, notarization, store approval, release-candidate status, or general availability.
