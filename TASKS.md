# Soleaux Executable Task List

<!-- soleaux-docs:tasks current_phase=5 -->

**Status owner:** [`PROJECT-STATUS.json`](PROJECT-STATUS.json)  
**Phase owner:** [`ROADMAP.md`](ROADMAP.md)  
**Gap owner:** [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)

Rules:

- Work top-down within the current implementation phase.
- Phase 3 is deferred and does not block implementation; it remains mandatory before quantified efficacy claims.
- Do not change locked digests, version, twelve-tool ceiling, or `productionClaimAllowed` without a reviewed contract change.
- Update status, roadmap, tasks, handoff, changelog, audit, release gates, and claims together when status changes.
- A checkbox is not evidence; record a PR, exact receipt, workflow, artifact, or independent verification.
- A capability is not dropped merely because it is not a root tool.

## Branch and documentation convergence

- [x] **BR-001** Merge standardized documentation through PR #4.
- [x] **BR-002** Merge the receipt-bearing lineage to `main` through PR #5.
- [x] **BR-003** Close obsolete PR #3 as superseded.
- [x] **BR-004** Create receipt/archive tags before historical branch cleanup.
- [x] **BR-005** Materialize native source and direct CI through PR #6.
- [x] **BR-006** Merge the transcript gap audit and expanded executable registry through PR #11.
- [x] **BR-007** Prune every fully merged short-lived branch and retain unique-commit branches only as documented non-authoritative archival lineage.

Evidence: [`docs/operations/BRANCH-CONSOLIDATION-2026-08-07.json`](docs/operations/BRANCH-CONSOLIDATION-2026-08-07.json) (supersedes the 2026-08-05 report).

## Completed phases

### Phase 0 — contract lock and native foundation

- [x] **P0-001** Lock the canonical twelve-tool profile.
- [x] **P0-002** Lock Context Packet V2.
- [x] **P0-003** Fix version at `0.4.0-dev.5` and production claim at false.
- [x] **P0-004** Pass native format/check/Clippy/test/build/audit/smoke.
- [x] **P0-005** Persist exact gate receipt.

Evidence: `PHASE0-NATIVE-GATE-RECEIPT.json`.

### Phase 1 — unified catalog and context

- [x] **P1-001** Expose exact canonical tool order.
- [x] **P1-002** Implement `soleaux.context/v2`.
- [x] **P1-003** Implement native search, registry, repo identity, LSP, preview/edit, and restart.
- [x] **P1-004** Prove one-for-one substitutions remain at twelve.
- [x] **P1-005** Pass exact native gate and independent MCP/schema smoke.

Evidence: `PHASE1-NATIVE-GATE-RECEIPT.json`.

### Phase 2 — gateway, catalog, provisioning, and governance

- [x] **P2-001** Namespaced MCP gateway and CLI-mediated credentials.
- [x] **P2-002** Skills, agents, rules, ownership, tables, and backend domains.
- [x] **P2-003** Native adopt and attach provisioning.
- [x] **P2-004** Governance edges in registry and context.
- [x] **P2-005** Optional Next/Postgres/Turbo substitutions.
- [x] **P2-006** Full native gate.
- [x] **P2-007** Independent artifact verification and closure receipt.

Evidence: `PHASE2-CLOSURE-RECEIPT.json` and `PHASE2-INDEPENDENT-VERIFICATION.json`.

## Deferred claims phase

### Phase 3 — three-arm real-client product proof

These tasks do not block Phases 4–8. They must be completed before quantified efficacy claims or a reviewed change to `productionClaimAllowed`.

#### Reconciliation and freeze

- [x] **P3-001** Preserve the real-repository task package and measurement/rubric files.
- [ ] **P3-005** Record the obsolete GitHub Models/synthetic design as superseded evidence.
- [ ] **P3-006** Freeze arms `control_no_soleaux`, `historical_python`, `native_treatment`.
- [ ] **P3-007** Record exact authenticated model, client/build, protocol, sampling, budgets, retries, and credentials.
- [ ] **P3-008** Run model-free oracle dry-runs and freeze validation commands/expected facts.
- [ ] **P3-009** Hash all prompts, tasks, schemas, rubrics, oracles, and manifests before the first live call.

#### Materialization and execution

- [ ] **P3-010** Materialize no-Soleaux control using ordinary selected-client repository capabilities.
- [ ] **P3-011** Materialize historical Python/FastMCP baseline at the registered commit.
- [ ] **P3-012** Materialize native treatment and verify exact twelve tools, Context V2, and native selections.
- [ ] **P3-013** Create clean isolated worktrees for every arm/task/repetition.
- [ ] **P3-014** Run all no-Soleaux tasks and retain every attempt/failure.
- [ ] **P3-015** Run all historical-baseline tasks with identical parameters/budgets.
- [ ] **P3-016** Run all native-treatment tasks with identical parameters/budgets.
- [ ] **P3-017** Record schemas, calls, reads, packets, gaps, retries, time, cost, files, and oracle results.
- [ ] **P3-018** Verify no secret leakage, fallback, catalog inflation, silent truncation, task drift, or hidden tools.

#### Analysis and closure

- [ ] **P3-020** Score every run, including hard failures.
- [ ] **P3-021** Report raw distributions and aggregate correctness/context economy.
- [ ] **P3-022** Pass market-value gate versus no-Soleaux control.
- [ ] **P3-023** Pass compatibility gate versus historical Python baseline.
- [ ] **P3-024** Independently verify commits, prompts, packets, scores, calculations, and artifacts.
- [ ] **P3-025** Write exact receipt and update status while keeping production claim false.

## Completed Phase 4

### Phase 4 — correct, durable native alpha foundation

#### Consolidation and alpha closure

- [x] **P4-001** Establish canonical native source on `main` through PRs #5/#6.
- [x] **P4-002** Replace carrier-only development with checked-in Rust workspace files.
- [x] **P4-003** Retain Python only as governed history, fixtures, conformance, and verification.
- [x] **P4-004** Run native CI directly from checkout.
- [x] **P4-005** Reconcile repository license and native package metadata/provenance.
- [x] **P4-006** Synchronize default-branch status, roadmap, tasks, handoff, changelog, audit, release gates, and public metadata.
- [x] **P4-007** Build reproducible unsigned alpha package and operational smoke.
- [x] **P4-008** Merge normal-source/direct-CI source through PR #6.
- [x] **P4-009** Write exact Phase 4 receipt and independently verify the artifact.
- [x] **P4-016** Preserve Phase 0–2 evidence through durable tags and remove redundant merged lineage.

#### Canonical state, durability, artifacts, and operations

- [x] **P4-010** Add canonical accounts, mappings, sessions, turns, messages, content, memory claims, rules, skills, agents, handoffs, runs, subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones, and retention.
- [x] **P4-011** Implement serialized persistence, migrations, crash recovery, replay, backup, restore, integrity repair, and schema-downgrade refusal.
- [x] **P4-012** Implement durable reservations, idempotency, leases, reconciliation, cancellation, abandonment, and pending-approval recovery.
- [x] **P4-013** Implement encrypted content-addressed artifacts, OS-protected key paths, workspace separation, rotation, redaction, and deny-by-default capability policy.
- [x] **P4-014** Implement stable operations CLI for serve/install/service/doctor/ci/cache/index/integrate/handoff/backup/restore/export/repair/uninstall.
- [x] **P4-015** Implement per-user service and typed local IPC with peer checks and concurrent clients.

#### Correctness closure

- [x] **P4-017** Validate active tool input schemas before dispatch.
- [x] **P4-018** Add watcher/incremental refresh and content-hash revalidation.
- [x] **P4-019** Roll back failed post-write edit bookkeeping with durable reconciliation.
- [x] **P4-020** Reserve operation keys atomically before side effects and replay exact results.
- [x] **P4-021** Centralize comprehensive structured and textual secret redaction.
- [x] **P4-022** Make adopt, attach, and revert transactional.
- [x] **P4-023** Report truthful gaps, truncation, and continuation cursors.
- [x] **P4-024** Return invalid PostgreSQL as typed validation data.
- [x] **P4-025** Advertise/call LSP methods only after initialized capability confirmation.
- [x] **P4-026** Atomically claim previews and bind all immutable execution inputs.

Evidence:

- `PHASE4-ALPHA-CLOSURE-RECEIPT.json`
- `PHASE4-INDEPENDENT-VERIFICATION.json`
- `PHASE4-CLOSURE-RECEIPT.json`

## Current phase

### Phase 5 — adapters, lifecycle, intelligence depth, and extensibility

#### Service and platform matrices

- [x] **P5-001** Installable per-user service/workspace registry across concurrent CLI/desktop/editor/adapter clients.
- [x] **P5-002** Claude Code capability probe and version matrix.
- [x] **P5-003** Claude Desktop supported connector/export-import capability matrix.
- [x] **P5-004** Codex CLI/desktop app-server capability and schema matrix.
- [x] **P5-005** OpenCode HTTP/OpenAPI/SSE/plugin capability matrix.
- [x] **P5-006** Cursor and generic MCP-host verification.

P5-001 evidence: [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json). P5-002 through P5-006 evidence: [`P5-002-P5-006-CLOSURE-RECEIPT.json`](P5-002-P5-006-CLOSURE-RECEIPT.json).

#### Consolidation pre-tasks (2026-08-07)

Acceptance detail, entry state, and dependencies for every remaining Phase 5 task live in [`docs/plans/PHASE5-IMPLEMENTATION-PLAN.md`](docs/plans/PHASE5-IMPLEMENTATION-PLAN.md) with edges in [`docs/plans/PHASE5-DEPENDENCIES.json`](docs/plans/PHASE5-DEPENDENCIES.json).

- [x] **P5-W1** Wire `soleaux-mcp` to `soleaux-state` and give `soleaux-vault` its first daemon consumer.

P5-W1 evidence: [`P5-W1-CLOSURE-RECEIPT.json`](P5-W1-CLOSURE-RECEIPT.json). The next open implementation task is P5-008.

#### Canonical lifecycle, materializers, and real repositories

- [x] **P5-007** Canonical session/history service and same-platform resume/fork/archive. Evidence: [`P5-007-CLOSURE-RECEIPT.json`](P5-007-CLOSURE-RECEIPT.json).
- [ ] **P5-008** Materializer compatibility compiler, diff/backup/atomic apply/rollback, echo guards, and load verification.
- [ ] **P5-009** Real LSP matrix: TypeScript/VTSLS, BasedPyright, Bash, Rust, Go, SourceKit, clangd, Kotlin, JDT, Vue, Svelte, Astro, MDX, YAML/JSON/HTML/CSS.
- [ ] **P5-010** Turborepo and Next.js compatibility matrices on real repositories/versions.
- [ ] **P5-011** Attach and validate `anilize`.
- [ ] **P5-012** Validate two additional approved design partners.
- [ ] **P5-013** Write Phase 5 beta receipt and independent verification.

#### Deep adapter implementations

- [ ] **P5-V1** Daemon-trusted admission receipt verifier and reviewed lifecycle oracle for external client write admission.
- [ ] **P5-014** Claude SDK execution host, external SessionStore, hooks, permissions, compaction, and restart reconciliation.
- [ ] **P5-015** Claude Desktop user-facing import/export and supported local connector workflows; hosted CRUD remains a non-goal.
- [ ] **P5-016** Codex generated app-server client, approvals, steering, compaction, archive, cursors, reconnect, and safe mode.
- [ ] **P5-017** OpenCode generated client, persistent SSE cursor/reconciliation, permissions, fork/abort/summarize/revert, and plugin compatibility.

#### Memory, handoffs, and orchestration

- [ ] **P5-018** Memory lifecycle: Proposed→Validated→Active→Superseded/Tombstoned/Rejected, scopes, confidence, sensitivity, expiry, conflicts, provenance, import/export, and compaction survival.
- [ ] **P5-019** Signed handoff manifest with objective, decisions, tasks, Git branch/commit, dirty patch, files, artifacts, exclusions, permissions, and target-native lineage.
- [ ] **P5-020** Durable run/subagent orchestration with approvals, budgets, worktree leases, capability attenuation, recovery, cancellation, and aggregation.

#### Intelligence and extensibility depth

- [ ] **P5-021** Complete Oxc extraction and Tree-sitter query/injection/incremental watcher/damaged-file corpus.
- [ ] **P5-022** Add LibCST Python writes and `mvdan.cc/sh` semantics, optional ShellCheck, sandbox, process tree, and diagnostics.
- [ ] **P5-023** Complete LSP multi-root/versioning, push/pull diagnostics, workspace edits, events, request-aware caches, and resource controls.
- [ ] **P5-024** Turbo static graph plus version-probed documented CLI; optional LSP only after compatibility probe.
- [ ] **P5-025** Next Oxc static routes/actions/boundaries plus capability-driven DevTools `init`, index, advertised calls, and multi-app merge.
- [ ] **P5-026** Versioned provider/plugin interfaces for parsers, workspace graphs, routes, context sources, materializers, and gateway backends.
- [ ] **P5-027** Stable Rust API and generated Python/TypeScript daemon SDKs; complete deterministic `soleaux ci`.
- [ ] **P5-028** Editor extension MVP and capability-gated webhook/SIEM export.
- [ ] **P5-029** Optional hybrid lexical/vector/graph search with pinned model/license/hash, sensitivity exclusions, migration, rebuild, and corruption recovery.

## Remaining phases

### Phase 6 — desktop, mobile, remote control, installers, and operations

- [ ] **P6-001** Tauri desktop shell and daemon lifecycle.
- [ ] **P6-002** Full Context Inspector, catalog, sessions, health, and diagnostics UX.
- [ ] **P6-003** One Expo/React Native mobile application; parsers remain server-side.
- [ ] **P6-004** Pairing, direct LAN, E2E relay fallback, revoke, and audit.
- [ ] **P6-005** Keychain/keystore and artifact encryption integration.
- [ ] **P6-006** macOS/Windows/Linux development installers.
- [ ] **P6-007** Upgrade, repair, rollback, uninstall, and native-file restoration.
- [ ] **P6-008** Update channel, version alignment, support bundle, and opt-in redacted crash reporting.
- [ ] **P6-009** Device and desktop E2E evidence.
- [ ] **P6-010** First-run detection, operating mode, repository trust, indexing progress/cancel/partial availability, and capability UI.
- [ ] **P6-011** Unified sessions/transcripts/events/artifacts/lineage, live run console, subagent graph, and handoff UX.
- [ ] **P6-012** Approval inbox, permission profiles, risk preview, biometric/desktop confirmation, and conflict resolution.
- [ ] **P6-013** Memory lifecycle review and catalog compatibility/materialization UX.
- [ ] **P6-014** Versioned remote API, device certificates, replay-safe commands, cursor stream, opaque APNs/FCM, and expiry policy.
- [ ] **P6-015** Backups, restore, integrations, updates, diagnostics, device management, and accessibility/i18n-ready design.
- [ ] **P6-016** Optional offline mobile library only after encrypted replication, tombstones, quotas, rotation, and conflict rules pass.

### Phase 7 — assurance, scale, parity, and enterprise readiness

- [ ] **P7-001** Defined-hardware cold/warm p50/p95/p99 benchmark suite.
- [ ] **P7-002** Parser/LSP corpus, malformed-input, and protocol fuzzing.
- [ ] **P7-003** Path jail, shell policy, redaction, prompt injection, pairing, MCP, update, and cross-workspace security tests.
- [ ] **P7-004** External penetration test.
- [ ] **P7-005** Privacy, retention, deletion, and legal/license review.
- [ ] **P7-006** Desktop/mobile accessibility and internationalization audit.
- [ ] **P7-007** Signed SBOM and build provenance.
- [ ] **P7-008** macOS/Windows/Linux and architecture matrix, including case-insensitive paths and symlink cycles.
- [ ] **P7-009** Incident response, backup/restore, relay outage, upgrade/downgrade, and rollback exercises.
- [ ] **P7-010** Stage-17-style readiness decision.
- [ ] **P7-011** Large-repository/pathological-file/concurrent-client/memory-pressure/worker-crash tests.
- [ ] **P7-012** Production relay hardening, queues, expiry/DLQ, abuse controls, replay defense, push rotation, SLOs, self-hosting, and DR.
- [ ] **P7-013** Enterprise audit export, retention, air-gap, and SSO only after local product gates pass.

### Phase 8 — release candidate and general availability

- [ ] **P8-001** Freeze `1.0.0-rc.1` only after Phase 7.
- [ ] **P8-002** Sign/notarize desktop artifacts and sign Windows packages.
- [ ] **P8-003** TestFlight and Play internal rollout.
- [ ] **P8-004** Design-partner staged release.
- [ ] **P8-005** Public staged release with rollback thresholds.
- [ ] **P8-006** Review and explicitly decide `productionClaimAllowed`.
- [ ] **P8-007** Publish release notes, support policy, compatibility, privacy, and known limitations.
- [ ] **P8-008** GA verification and `1.0.0`.

## Documentation maintenance

- [x] **DOC-001** Establish one documentation authority hierarchy.
- [x] **DOC-002** Replace historical root status/roadmap/tasks/handoff/changelog.
- [x] **DOC-003** Add testing, release, rollout, claims, and marketing documents.
- [x] **DOC-004** Add fail-closed documentation consistency validation.
- [x] **DOC-005** Add Phase 3 experiment templates and machine schemas.
- [x] **DOC-006** Merge the unified documentation system through PR #4.
- [x] **DOC-007** Merge the transcript gap audit, capability map, and expanded tasks through PR #11.
- [x] **DOC-008** Synchronize the audit, registry, receipts, release gates, and public status through Phase 4 closure and enforce future convergence.
