# Soleaux Executable Task List

<!-- soleaux-docs:tasks current_phase=3 -->

**Owner:** the unified Soleaux repository  
**Status owner:** [`PROJECT-STATUS.json`](PROJECT-STATUS.json)  
**Phase owner:** [`ROADMAP.md`](ROADMAP.md)

Rules:

- Work top-down within the current phase.
- Do not begin the next phase until the current exit receipt and independent verification exist.
- Do not alter locked contract digests, version, 12-tool ceiling, or `productionClaimAllowed` without reviewed contract changes.
- Update this file, `PROJECT-STATUS.json`, `PROJECT-STATUS.md`, and `CHANGELOG.md` together when a phase status changes.
- A checkbox is not evidence; link the receipt or artifact.

## Completed phases

### Phase 0 — contract lock and native foundation

- [x] **P0-001** Lock the canonical 12-tool profile.
- [x] **P0-002** Lock Context Packet V2.
- [x] **P0-003** Fix version at `0.4.0-dev.5` and production claim at false.
- [x] **P0-004** Pass native format/check/Clippy/test/build/audit/smoke.
- [x] **P0-005** Persist exact gate receipt.

Evidence: `PHASE0-NATIVE-GATE-RECEIPT.json`.

### Phase 1 — unified catalog and context

- [x] **P1-001** Expose exact canonical tool order.
- [x] **P1-002** Implement `soleaux.context/v2`.
- [x] **P1-003** Implement native search, registry, repo identity, LSP, preview/edit, and restart.
- [x] **P1-004** Prove one-for-one substitutions remain at 12.
- [x] **P1-005** Pass exact native gate and independent MCP/schema smoke.

Evidence: `PHASE1-NATIVE-GATE-RECEIPT.json`.

### Phase 2 — remaining native Lineage A capabilities

- [x] **P2-001** Namespaced MCP gateway and CLI-mediated credentials.
- [x] **P2-002** Skills, agents, rules, ownership, tables, and backend registry domains.
- [x] **P2-003** Native adopt and attach provisioning.
- [x] **P2-004** Governance edges in registry and context.
- [x] **P2-005** Optional Next/Postgres/Turbo substitutions.
- [x] **P2-006** Full native gate.
- [x] **P2-007** Independent artifact verification and closure receipt.

Evidence: `PHASE2-CLOSURE-RECEIPT.json` and `PHASE2-INDEPENDENT-VERIFICATION.json`.

## Current phase

### Phase 3 — live same-model / same-task product proof

#### Experiment freeze

- [x] **P3-001** Create the standardized Phase 3 experiment package.
- [x] **P3-002** Pre-register the baseline implementation, treatment implementation, and target repository commit.
- [x] **P3-003** Pre-register the task IDs, objectives, scopes, success rubrics, and failure reporting.
- [x] **P3-004** Define the measurement schema and independent-verification requirements.
- [ ] **P3-005** Record the exact model ID, client name/build, sampling parameters, protocol version, and credentials availability.
- [ ] **P3-006** Run an oracle dry-run without a model and freeze expected validation commands.
- [ ] **P3-007** Change `docs/experiments/phase3/STATUS.json` to `frozen_ready` before the first live call.

#### Execution

- [ ] **P3-010** Materialize baseline server at the registered commit.
- [ ] **P3-011** Materialize native treatment at the registered commit.
- [ ] **P3-012** Verify the treatment returns exactly 12 tools and valid Context Packet V2.
- [ ] **P3-013** Run every baseline task; retain all failures and raw logs.
- [ ] **P3-014** Run every treatment task with identical parameters and budgets.
- [ ] **P3-015** Record tool schemas, tool calls, file-read context, compiled context, retries, elapsed time, cost, and correctness.
- [ ] **P3-016** Verify no secret leakage, non-native fallback, catalog inflation, or silent truncation occurred.

#### Analysis and closure

- [ ] **P3-020** Score all runs against the frozen rubric.
- [ ] **P3-021** Report raw distributions and aggregate correctness/context reduction.
- [ ] **P3-022** Prove equal-or-better aggregate correctness.
- [ ] **P3-023** Independently verify prompts, commits, packets, scores, digests, and measurements.
- [ ] **P3-024** Write exact Phase 3 receipt.
- [ ] **P3-025** Update project status; keep `productionClaimAllowed=false`.

## Remaining phases

### Phase 4 — canonical source consolidation and alpha foundation

- [ ] **P4-001** Create a normal native source branch from the verified Phase 2/3 source.
- [ ] **P4-002** Replace carrier-only development with checked-in Rust workspace files.
- [ ] **P4-003** Retain Python only as historical/conformance fixtures.
- [ ] **P4-004** Make native CI run directly from checkout.
- [ ] **P4-005** Reconcile license, package metadata, SBOM, and dependency policy.
- [ ] **P4-006** Update the default-branch documentation and public metadata.
- [ ] **P4-007** Build an unsigned alpha package with install/doctor/uninstall smoke.
- [ ] **P4-008** Open reviewed default-branch consolidation PR; no force-push.
- [ ] **P4-009** Write Phase 4 receipt and independent verification.

### Phase 5 — shared service, live adapters, and consumers

- [ ] **P5-001** Per-user daemon/service installation and workspace registry.
- [ ] **P5-002** Live Claude Code capability probe and matrix.
- [ ] **P5-003** Live Claude Desktop capability probe and matrix.
- [ ] **P5-004** Live Codex CLI/Desktop capability probe and matrix.
- [ ] **P5-005** Live OpenCode capability probe and matrix.
- [ ] **P5-006** Cursor/MCP host verification.
- [ ] **P5-007** Shared memory and handoff lifecycle.
- [ ] **P5-008** Materializer diff/backup/rollback/echo-loop live-host tests.
- [ ] **P5-009** Real LSP matrix.
- [ ] **P5-010** Turbo/Next documented-CLI/runtime compatibility matrix.
- [ ] **P5-011** Attach and validate `anilize`.
- [ ] **P5-012** Attach and validate two additional approved design-partner repos.
- [ ] **P5-013** Write Phase 5 receipt and beta-readiness report.

### Phase 6 — desktop, mobile, installers, operations

- [ ] **P6-001** Tauri desktop shell and daemon lifecycle.
- [ ] **P6-002** Context Inspector, catalog, sessions, health, and diagnostics UX.
- [ ] **P6-003** One Expo/React Native mobile app.
- [ ] **P6-004** Pairing, direct LAN, encrypted relay fallback, revoke.
- [ ] **P6-005** Keychain/keystore and artifact encryption.
- [ ] **P6-006** macOS/Windows/Linux development installers.
- [ ] **P6-007** Upgrade, repair, rollback, uninstall, and native-file restoration.
- [ ] **P6-008** Update channel and support bundle.
- [ ] **P6-009** Device and desktop E2E evidence.

### Phase 7 — assurance and parity

- [ ] **P7-001** Defined-hardware performance benchmark suite.
- [ ] **P7-002** Parser/LSP corpus and fuzzing.
- [ ] **P7-003** Path jail, shell policy, secret redaction, pairing, MCP, and update security tests.
- [ ] **P7-004** External penetration test.
- [ ] **P7-005** Privacy and legal/license review.
- [ ] **P7-006** Accessibility audit.
- [ ] **P7-007** Signed SBOM and build provenance.
- [ ] **P7-008** macOS, Windows, and Linux compatibility matrix.
- [ ] **P7-009** Incident-response and rollback exercise.
- [ ] **P7-010** Stage-17-style readiness decision.

### Phase 8 — RC and GA rollout

- [ ] **P8-001** Freeze `1.0.0-rc.1` only after Phase 7.
- [ ] **P8-002** Sign/notarize desktop artifacts.
- [ ] **P8-003** TestFlight and Play internal rollout.
- [ ] **P8-004** Design-partner staged release.
- [ ] **P8-005** Public staged release with rollback thresholds.
- [ ] **P8-006** Review and explicitly decide `productionClaimAllowed`.
- [ ] **P8-007** Publish release notes, support policy, and known limitations.
- [ ] **P8-008** GA verification and `1.0.0`.

## Documentation maintenance

- [x] **DOC-001** Establish one documentation authority hierarchy.
- [x] **DOC-002** Replace historical root README/TASKS/HANDOFF/AGENTS/CHANGELOG with unified native versions.
- [x] **DOC-003** Add project status, roadmap, release, testing, rollout, and marketing documents.
- [x] **DOC-004** Add a fail-closed documentation consistency checker.
- [x] **DOC-005** Add Phase 3 experiment templates and machine schemas.
- [ ] **DOC-006** Merge this documentation branch into the native Phase 2 branch after review.
- [ ] **DOC-007** Carry the same hierarchy into the canonical default branch during Phase 4.
