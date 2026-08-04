# Soleaux Executable Task List

<!-- soleaux-docs:tasks current_phase=4 -->

**Status owner:** [`PROJECT-STATUS.json`](PROJECT-STATUS.json)  
**Phase owner:** [`ROADMAP.md`](ROADMAP.md)  
**Gap owner:** [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)

Rules:

- Work top-down within the current implementation phase.
- Phase 3 is deferred and does not block implementation; it remains mandatory before quantified efficacy claims.
- Do not change locked digests, version, twelve-tool ceiling, or `productionClaimAllowed` without a reviewed contract change.
- Update status, roadmap, tasks, handoff, changelog, phase documents, and claims together when status changes.
- A checkbox is not evidence; record a PR, exact receipt, workflow, artifact, or independent verification.
- A capability is not dropped merely because it is not a root tool; follow the capability absorption map.

## Branch and documentation convergence

- [x] **BR-001** Merge standardized documentation through PR #4.
- [x] **BR-002** Merge the full receipt-bearing lineage to `main` through merge-commit PR #5.
- [x] **BR-003** Close obsolete PR #3 as superseded.
- [x] **BR-004** Create receipt/archive tags and remove historical phase branches.
- [x] **BR-005** Materialize native source/direct CI through PR #6.
- [ ] **BR-006** Merge the transcript gap audit and expanded executable registry after green CI.
- [ ] **BR-007** Delete merged/collision audit branches after BR-006 and verify no unique commits remain.

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
- [x] **P1-003** Implement native search, registry, repo identity, LSP, preview/edit and restart.
- [x] **P1-004** Prove one-for-one substitutions remain at twelve.
- [x] **P1-005** Pass exact native gate and independent MCP/schema smoke.

Evidence: `PHASE1-NATIVE-GATE-RECEIPT.json`.

### Phase 2 — gateway, catalog, provisioning and governance

- [x] **P2-001** Namespaced MCP gateway and CLI-mediated credentials.
- [x] **P2-002** Skills, agents, rules, ownership, tables and backend domains.
- [x] **P2-003** Native adopt and attach provisioning.
- [x] **P2-004** Governance edges in registry and context.
- [x] **P2-005** Optional Next/Postgres/Turbo substitutions.
- [x] **P2-006** Full native gate.
- [x] **P2-007** Independent artifact verification and closure receipt.

Evidence: `PHASE2-CLOSURE-RECEIPT.json` and `PHASE2-INDEPENDENT-VERIFICATION.json`.

## Deferred claims phase

### Phase 3 — three-arm real-client product proof

These tasks have no current scheduling obligation and do not block Phases 4–8. They must be completed before quantified efficacy claims or a reviewed change to `productionClaimAllowed`.

#### Reconciliation and freeze

- [x] **P3-001** Preserve the real-repository task package and measurement/rubric files.
- [ ] **P3-005** Record the obsolete GitHub Models/synthetic design as superseded evidence.
- [ ] **P3-006** Freeze arms `control_no_soleaux`, `historical_python`, `native_treatment`.
- [ ] **P3-007** Record exact authenticated model, client/build, protocol, sampling, budgets, retries and credentials.
- [ ] **P3-008** Run model-free oracle dry-runs and freeze validation commands/expected facts.
- [ ] **P3-009** Hash all prompts, tasks, schemas, rubrics, oracles and manifests before the first live call.

#### Materialization and execution

- [ ] **P3-010** Materialize no-Soleaux control using only ordinary selected-client repository capabilities.
- [ ] **P3-011** Materialize historical Python/FastMCP baseline at the registered commit.
- [ ] **P3-012** Materialize native treatment and verify exact twelve tools, Context V2 and native selections.
- [ ] **P3-013** Create clean isolated worktrees for every arm/task/repetition.
- [ ] **P3-014** Run all no-Soleaux tasks and retain every attempt/failure.
- [ ] **P3-015** Run all historical-baseline tasks with identical parameters/budgets.
- [ ] **P3-016** Run all native-treatment tasks with identical parameters/budgets.
- [ ] **P3-017** Record schemas, calls, reads, packets, gaps, retries, time, cost, files and oracle results.
- [ ] **P3-018** Verify no secret leakage, fallback, catalog inflation, silent truncation, task drift or hidden tools.

#### Analysis and closure

- [ ] **P3-020** Score every run, including hard failures.
- [ ] **P3-021** Report raw distributions and aggregate correctness/context economy.
- [ ] **P3-022** Pass market-value gate versus no-Soleaux control.
- [ ] **P3-023** Pass compatibility gate versus historical Python baseline.
- [ ] **P3-024** Independently verify commits, prompts, packets, scores, calculations and artifacts.
- [ ] **P3-025** Write exact receipt and update status while keeping production claim false.

## Current phase

### Phase 4 — correct, durable native alpha foundation

#### Completed consolidation and source milestones

- [x] **P4-001** Establish canonical native source on `main` through PRs #5/#6.
- [x] **P4-002** Replace carrier-only development with checked-in Rust workspace files.
- [x] **P4-003** Retain historical Python lineage only as governed compatibility/history surfaces; no client-visible Python product mode.
- [x] **P4-004** Run native CI directly from checkout.
- [x] **P4-005** Reconcile repository license and native package metadata/provenance.
- [ ] **P4-006** Update default-branch status, roadmap, tasks, handoff, changelog, audit and public metadata through BR-006.
- [ ] **P4-007** Build reproducible unsigned alpha package and operational smoke.
- [x] **P4-008** Merge normal-source/direct-CI source through reviewed PR #6.
- [ ] **P4-009** Write Phase 4 exact receipt and independent artifact verification.
- [x] **P4-016** Preserve Phase 0–2 evidence through durable tags and remove redundant historical branches.

#### Canonical state, durability and operations

- [ ] **P4-010** Add platform accounts, native mappings, sessions, turns, messages, content parts, memory claims, rules, skills, agents, handoffs, runs, subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones and retention.
- [ ] **P4-011** Implement serialized writer/read pool, migrations, crash recovery, replay, backup, integrity repair and schema-downgrade refusal.
- [ ] **P4-012** Implement durable operation reservation, idempotency, leases, process/native-session reconciliation, cancellation and pending-approval recovery.
- [ ] **P4-013** Implement encrypted content-addressed artifact vault, OS-keychain master keys, per-workspace separation, rotation, redaction and policy/capability foundation.
- [ ] **P4-014** Implement exact CLI: `serve`, `install`, `service`, `doctor`, `ci`, `cache`, `index`, `integrate`, `handoff`, `backup`, `restore`, `export`, `repair`, `uninstall --restore-native`; all commands support `--json`, mutators support `--dry-run`.
- [ ] **P4-015** Implement per-user daemon lifecycle and typed local IPC with peer-credential checks; closing UI must not stop work.

#### Current-source correctness blockers

- [ ] **P4-017** Validate active tool input schemas before dispatch; reject missing required, unknown and out-of-range arguments with typed invalid-params errors.
- [ ] **P4-018** Add watcher/incremental refresh and content-hash revalidation so search/context cannot hydrate stale indexed ranges after external or applied changes.
- [ ] **P4-019** Make edit filesystem/index/database/preview/audit updates transactional or roll back from backup with a durable reconciliation receipt.
- [ ] **P4-020** Reserve idempotency/operation keys atomically before side effects and return/replay the existing result for duplicates.
- [ ] **P4-021** Use one comprehensive prefix/header/PEM/env/structured-secret redactor across context, memory, handoff, logs, telemetry, mobile and artifacts.
- [ ] **P4-022** Render/validate all adopt/apply/revert actions first; commit transactionally and restore all prior files on any failure.
- [ ] **P4-023** Report truthful coverage gaps, truncation and continuations for bounded text search, memory, index and unavailable-provider paths.
- [ ] **P4-024** Return invalid PostgreSQL through the declared typed validation result; reserve tool errors for operational failures.
- [x] **P4-025** Advertise/call LSP methods only after successful initialization confirms support; PR #7 and its stub-server regression tests.
- [ ] **P4-026** Atomically claim one-time previews and bind workspace, canonical path, source revision/preimage hash, expiry, formatter/diagnostic plan and consumption receipt.

### Phase 4 exit

- every open P4 task above closed with focused regressions;
- full native fmt/check/Clippy/test/build/audit plus canonical/substitution/context smokes green;
- install/service/restart/backup/restore/repair/uninstall smoke green;
- reproducible alpha artifact, exact receipt and independent verification;
- version remains developmental and production claim false.

## Remaining phases

### Phase 5 — adapters, lifecycle, intelligence depth and extensibility

- [ ] **P5-001** Installable per-user service/workspace registry across concurrent CLI/desktop/editor clients.
- [ ] **P5-002** Claude Code capability probe and version matrix.
- [ ] **P5-003** Claude Desktop supported connector/export-import capability matrix.
- [ ] **P5-004** Codex CLI/desktop app-server capability and schema matrix.
- [ ] **P5-005** OpenCode HTTP/OpenAPI/SSE/plugin capability matrix.
- [ ] **P5-006** Cursor and generic MCP-host verification.
- [ ] **P5-007** Canonical session/history service and same-platform resume/fork/archive.
- [ ] **P5-008** Materializer compatibility compiler, diff/backup/atomic apply/rollback, echo guards and load verification.
- [ ] **P5-009** Real LSP matrix: TypeScript/VTSLS, BasedPyright, Bash, Rust, Go, SourceKit, clangd, Kotlin, JDT, Vue, Svelte, Astro, MDX, YAML/JSON/HTML/CSS.
- [ ] **P5-010** Turborepo and Next.js compatibility matrices on real repositories/versions.
- [ ] **P5-011** Attach and validate `anilize`.
- [ ] **P5-012** Validate two additional approved design partners.
- [ ] **P5-013** Write Phase 5 beta receipt and independent verification.
- [ ] **P5-014** Claude SDK execution host, external SessionStore, hooks, permissions, compaction and restart reconciliation.
- [ ] **P5-015** Claude Desktop user-facing import/export and supported local connector workflows; hosted CRUD remains a non-goal.
- [ ] **P5-016** Codex generated app-server client, approvals, steering, compaction, archive, cursors, reconnect and safe mode.
- [ ] **P5-017** OpenCode generated client, persistent SSE cursor/reconciliation, permissions, fork/abort/summarize/revert and plugin compatibility.
- [ ] **P5-018** Memory lifecycle: Proposed→Validated→Active→Superseded/Tombstoned/Rejected, scopes, confidence, sensitivity, expiry, conflicts, provenance, import/export and compaction survival.
- [ ] **P5-019** Signed handoff manifest with objective, decisions, tasks, Git branch/commit, dirty patch, files, artifacts, exclusions, permissions and target-native lineage.
- [ ] **P5-020** Durable run/subagent orchestration with approvals, budgets, worktree leases, capability attenuation, recovery, cancellation and aggregation.
- [ ] **P5-021** Complete Oxc extraction and Tree-sitter query/injection/incremental watcher/damaged-file corpus.
- [ ] **P5-022** Add LibCST Python writes and `mvdan.cc/sh` shell semantics, optional ShellCheck, sandbox, process tree and diagnostics.
- [ ] **P5-023** Complete LSP multi-root/versioning, push/pull diagnostics, workspace edits, events, request-aware caches and resource controls.
- [ ] **P5-024** Turbo static graph plus version-probed documented CLI; optional LSP only after compatibility probe.
- [ ] **P5-025** Next Oxc static routes/actions/boundaries plus capability-driven DevTools `init`, index, advertised calls and multi-app merge.
- [ ] **P5-026** Versioned provider/plugin interfaces for parsers, workspace graphs, routes, context sources, materializers and gateway backends.
- [ ] **P5-027** Stable Rust API and generated Python/TypeScript daemon SDKs; complete deterministic `soleaux ci`.
- [ ] **P5-028** Editor extension MVP and capability-gated webhook/SIEM export.
- [ ] **P5-029** Optional hybrid lexical/vector/graph search with pinned model/license/hash, sensitivity exclusions, migration, rebuild and corruption recovery.

### Phase 6 — desktop, mobile, remote control, installers and operations

- [ ] **P6-001** Tauri desktop shell and daemon lifecycle.
- [ ] **P6-002** Full Context Inspector, catalog, sessions, health and diagnostics UX.
- [ ] **P6-003** One Expo/React Native mobile application; parsers remain server-side.
- [ ] **P6-004** Pairing, direct LAN, E2E relay fallback, revoke and audit.
- [ ] **P6-005** Keychain/keystore and artifact encryption integration.
- [ ] **P6-006** macOS/Windows/Linux development installers.
- [ ] **P6-007** Upgrade, repair, rollback, uninstall and native-file restoration.
- [ ] **P6-008** Update channel, version alignment, support bundle and opt-in redacted crash reporting.
- [ ] **P6-009** Device and desktop E2E evidence.
- [ ] **P6-010** First-run detection, operating mode, repository trust, indexing progress/cancel/partial availability and capability UI.
- [ ] **P6-011** Unified sessions/transcripts/events/artifacts/lineage, live run console, subagent graph and handoff UX.
- [ ] **P6-012** Approval inbox, permission profiles, risk preview, biometric/desktop confirmation and conflict resolution.
- [ ] **P6-013** Memory lifecycle review and catalog compatibility/materialization UX.
- [ ] **P6-014** Versioned remote API, device certificates, replay-safe commands, cursor stream, opaque APNs/FCM and expiry policy.
- [ ] **P6-015** Backups, restore, integrations, updates, diagnostics, device management and accessibility/i18n-ready design.
- [ ] **P6-016** Optional offline mobile library only after encrypted replication, tombstones, quotas, rotation and conflict rules pass.

### Phase 7 — assurance, scale, parity and enterprise readiness

- [ ] **P7-001** Defined-hardware cold/warm p50/p95/p99 benchmark suite.
- [ ] **P7-002** Parser/LSP corpus, malformed-input and protocol fuzzing.
- [ ] **P7-003** Path jail, shell policy, redaction, prompt injection, pairing, MCP, update and cross-workspace security tests.
- [ ] **P7-004** External penetration test.
- [ ] **P7-005** Privacy, retention, deletion and legal/license review.
- [ ] **P7-006** Desktop/mobile accessibility and internationalization audit.
- [ ] **P7-007** Signed SBOM and build provenance.
- [ ] **P7-008** macOS/Windows/Linux and architecture matrix, including case-insensitive paths and symlink cycles.
- [ ] **P7-009** Incident response, backup/restore, relay outage, upgrade/downgrade and rollback exercises.
- [ ] **P7-010** Stage-17-style readiness decision.
- [ ] **P7-011** Large-repository/pathological-file/concurrent-client/memory-pressure/worker-crash tests.
- [ ] **P7-012** Production relay hardening, queues, expiry/DLQ, abuse controls, replay defense, push rotation, SLOs, self-hosting and DR.
- [ ] **P7-013** Enterprise audit export, retention, air-gap and SSO only after local product gates pass.

### Phase 8 — release candidate and general availability

- [ ] **P8-001** Freeze `1.0.0-rc.1` only after Phase 7.
- [ ] **P8-002** Sign/notarize desktop artifacts and sign Windows packages.
- [ ] **P8-003** TestFlight and Play internal rollout.
- [ ] **P8-004** Design-partner staged release.
- [ ] **P8-005** Public staged release with rollback thresholds.
- [ ] **P8-006** Review and explicitly decide `productionClaimAllowed`.
- [ ] **P8-007** Publish release notes, support policy, compatibility, privacy and known limitations.
- [ ] **P8-008** GA verification and `1.0.0`.

## Documentation maintenance

- [x] **DOC-001** Establish one documentation authority hierarchy.
- [x] **DOC-002** Replace historical root status/roadmap/tasks/handoff/changelog.
- [x] **DOC-003** Add testing, release, rollout, claims and marketing documents.
- [x] **DOC-004** Add fail-closed documentation consistency validation.
- [x] **DOC-005** Add Phase 3 experiment templates and machine schemas.
- [x] **DOC-006** Merge the unified documentation system through PR #4.
- [ ] **DOC-007** Merge the transcript gap audit, capability map, expanded tasks and current Phase 4 state through BR-006.
- [ ] **DOC-008** Keep the audit/registry synchronized as tasks close; never infer implementation from transcript claims.
