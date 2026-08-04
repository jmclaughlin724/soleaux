# Soleaux Executable Task List

<!-- soleaux-docs:tasks current_phase=4 -->

**Owner:** the unified Soleaux repository  
**Status owner:** [`PROJECT-STATUS.json`](PROJECT-STATUS.json)  
**Phase owner:** [`ROADMAP.md`](ROADMAP.md)  
**Gap owner:** [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)

Rules:

- Work top-down within the current phase.
- Do not begin a later implementation phase until the current implementation phase receipt and independent verification exist. The deferred Phase 3 claims experiment does not block Phase 4–8 implementation.
- Do not alter locked digests, version, twelve-tool ceiling, or `productionClaimAllowed` without reviewed contract changes.
- Update this file, both project-status files, handoff, roadmap, and changelog together when status changes.
- A checkbox is not evidence; link a receipt or immutable artifact.
- Internal capabilities are not dropped merely because they are not root tools; use the capability absorption map.

## Branch and documentation convergence

- [x] **BR-001** Retarget and merge documentation PR #4 into `native/0.4.0-dev.5` at `7af28901a67d7909a3442b0d22801ab3fe619293`.
- [ ] **BR-002** Merge the transcript/repository gap audit and expanded task registry into `native/0.4.0-dev.5` after green CI.
- [ ] **BR-003** Close obsolete draft PR #3 as superseded; never merge it.
- [ ] **BR-004** Merge the consolidated native lineage to `main` through one reviewed merge commit; preserve exact receipt ancestry and keep release status developmental.
- [ ] **BR-005** Create durable receipt/archive references and then remove merged or redundant long-lived branches when no evidence depends on branch names.

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

### Phase 2 — remaining native Lineage A capabilities

- [x] **P2-001** Namespaced MCP gateway and CLI-mediated credentials.
- [x] **P2-002** Skills, agents, rules, ownership, tables, and backend registry domains.
- [x] **P2-003** Native adopt and attach provisioning.
- [x] **P2-004** Governance edges in registry and context.
- [x] **P2-005** Optional Next/Postgres/Turbo substitutions.
- [x] **P2-006** Full native gate.
- [x] **P2-007** Independent artifact verification and closure receipt.

Evidence: `PHASE2-CLOSURE-RECEIPT.json` and `PHASE2-INDEPENDENT-VERIFICATION.json`.

## Deferred phase — efficacy and compatibility claims gate

### Phase 3 — real-client product proof

Deferred by owner direction on 2026-08-03. These tasks have no current scheduling obligation and do not block Phase 4–8 implementation. They must be completed before any quantified efficacy claim or any reviewed change to `productionClaimAllowed`.

#### Experiment reconciliation and freeze

- [x] **P3-001** Create a durable Phase 3 experiment package.
- [x] **P3-002** Register the native treatment and real target-repository commit.
- [x] **P3-003** Register task objectives, scopes, success rubrics, and failure reporting.
- [x] **P3-004** Define the measurement schema and independent-verification requirements.
- [ ] **P3-005** Reconcile the obsolete GitHub Models/synthetic harness with the real-repository plan; record the decision and superseded artifacts.
- [ ] **P3-006** Freeze three arms: `control_no_soleaux`, `historical_python`, and `native_treatment`.
- [ ] **P3-007** Record the exact authenticated model ID, client/build, MCP protocol, sampling parameters, budgets, retry policy, and credential availability.
- [ ] **P3-008** Run model-free oracle dry-runs for all tasks and freeze validation commands and expected facts.
- [ ] **P3-009** Hash prompts, tasks, schema, rubric, oracles, manifests, and change Phase 3 status to `frozen_ready` before the first live call.

#### Materialization and preflight

- [ ] **P3-010** Materialize a no-Soleaux control using only the selected client's ordinary repository capabilities.
- [ ] **P3-011** Materialize the historical Python/FastMCP compatibility baseline at the registered commit.
- [ ] **P3-012** Materialize native treatment at `6768d9de...`; verify exactly twelve tools, Context Packet V2, native parser/LSP selection, and no secret leakage.
- [ ] **P3-013** Create clean isolated target worktrees for every arm × task × repetition.

#### Execution

- [ ] **P3-014** Run every no-Soleaux control task and retain every attempt/failure.
- [ ] **P3-015** Run every historical Python baseline task with identical parameters and budgets.
- [ ] **P3-016** Run every native treatment task with identical parameters and budgets.
- [ ] **P3-017** Record schemas, calls, reads, compiled context, gaps, retries, elapsed time, cost, changed files, and oracle results.
- [ ] **P3-018** Verify no secret leakage, non-native fallback, catalog inflation, silent truncation, task drift, or hidden tools.

#### Analysis and closure

- [ ] **P3-020** Score all runs against the frozen rubric, including hard failures.
- [ ] **P3-021** Report raw distributions and aggregate correctness/context economy for all three arms.
- [ ] **P3-022** Pass the market-value gate: native treatment correctness ≥ no-Soleaux control with lower waste context.
- [ ] **P3-023** Pass the regression gate: native treatment correctness ≥ historical Python baseline.
- [ ] **P3-024** Independently verify commits, prompts, packets, scores, calculations, and artifacts.
- [ ] **P3-025** Write the exact Phase 3 receipt and update status; keep `productionClaimAllowed=false`.

## Current phase

### Phase 4 — canonical source, durable core, and alpha foundation

- [ ] **P4-001** Complete the consolidated lineage merge to `main` through a reviewed merge commit; continue Phase 4 there or on short-lived task branches only.
- [ ] **P4-002** Materialize the proven Rust workspace as normal checked-in source and eliminate carrier-only development.
- [ ] **P4-003** Retain Python only as historical/conformance fixtures; remove client-visible Python product mode.
- [ ] **P4-004** Make the complete native gate run directly from checkout.
- [ ] **P4-005** Reconcile license, metadata, dependency policy, SBOM, and source provenance.
- [ ] **P4-006** Update default-branch product metadata and documentation without overstating release status.
- [ ] **P4-007** Build an unsigned alpha package with install/doctor/service/restart/uninstall smoke.
- [ ] **P4-008** Review and merge the normal-source/direct-CI Phase 4 PR; do not force-push or squash receipt-bearing ancestry.
- [ ] **P4-009** Write the Phase 4 receipt and independent artifact verification.
- [ ] **P4-010** Expand the canonical database to platform accounts, native mappings, sessions, turns, messages, content parts, memory claims, rules, skills, agents, runs, subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones, and retention.
- [ ] **P4-011** Implement serialized writer/read pool migrations, crash recovery, replay, backup, integrity repair, and schema downgrade refusal.
- [ ] **P4-012** Implement durable operation reservation, idempotency, leases, process/native-session reconciliation, cancellation, and pending-approval recovery.
- [ ] **P4-013** Implement encrypted content-addressed artifact vault, OS-keychain master keys, per-workspace key separation, redaction, and policy/capability foundation.
- [ ] **P4-014** Implement exact CLI contract: `serve`, `install`, `service`, `doctor`, `ci`, `cache`, `index`, `integrate`, `handoff`, `backup`, `restore`, `export`, `repair`, and `uninstall --restore-native`; all support `--json`, mutators support `--dry-run`.
- [ ] **P4-015** Implement per-user daemon lifecycle and typed local IPC with peer-credential checks; closing UI must not stop work.
- [ ] **P4-016** Preserve Phase 0–2 source/evidence through durable receipt/archive references and remove merged/redundant branches under the branch plan.

## Remaining phases

### Phase 5 — adapters, data lifecycle, intelligence depth, and extensibility

- [ ] **P5-001** Installable per-user service and workspace registry across concurrent CLI/desktop/IDE clients.
- [ ] **P5-002** Claude Code Agent SDK/CLI capability probe and compatibility matrix.
- [ ] **P5-003** Claude Desktop MCP/extension plus explicit export/import boundary and capability matrix.
- [ ] **P5-004** Codex CLI/desktop app-server capability probe and generated schema matrix.
- [ ] **P5-005** OpenCode HTTP/OpenAPI/SSE/plugin capability probe and matrix.
- [ ] **P5-006** Cursor and generic MCP-host verification.
- [ ] **P5-007** Canonical session/history service with same-platform resume/fork/archive where supported.
- [ ] **P5-008** Materializer compatibility compiler, diff/backup/atomic apply/rollback, echo guards, and native load verification.
- [ ] **P5-009** Real LSP matrix: TS/VTSLS, BasedPyright, Bash, Rust, Go, SourceKit, clangd, Kotlin, JDT, Vue, Svelte, Astro, MDX, YAML/JSON/HTML/CSS.
- [ ] **P5-010** Turborepo and Next.js compatibility matrix on real repositories and versions.
- [ ] **P5-011** Attach and validate `anilize` as the first design partner.
- [ ] **P5-012** Attach and validate two additional approved design-partner repositories.
- [ ] **P5-013** Write Phase 5 receipt and beta-readiness report.
- [ ] **P5-014** Claude SDK execution host, external SessionStore, hooks, permission events, compaction and restart reconciliation.
- [ ] **P5-015** Claude Desktop user-facing import/export and supported local connector workflows; retain hosted CRUD non-goal.
- [ ] **P5-016** Codex app-server generated client, approvals, steering, compaction, archive, event cursors, reconnect, and safe-mode behavior.
- [ ] **P5-017** OpenCode generated client, persistent SSE cursor/reconciliation, permissions, fork/abort/summarize/revert, and plugin compatibility.
- [ ] **P5-018** Memory lifecycle: Proposed→Validated→Active→Superseded/Tombstoned/Rejected, scopes, confidence, sensitivity, expiry, conflicts, provenance, import/export, and compaction survival.
- [ ] **P5-019** Signed handoff manifest with objective, decisions, open tasks, git branch/commit, dirty patch, changed files, artifacts, exclusions, permissions, and destination-native session lineage.
- [ ] **P5-020** Durable run/subagent orchestration with approvals, budgets, worktree leases, capability attenuation, recovery, cancellation propagation, and aggregation.
- [ ] **P5-021** Complete Oxc symbol/module/route/server-action extraction; Tree-sitter query packs, language injection, incremental watcher integration, and damaged-file corpus.
- [ ] **P5-022** Integrate LibCST Python writes and `mvdan.cc/sh` shell semantics; add ShellCheck option, effect classification, sandbox, process-tree capture, and diagnostics.
- [ ] **P5-023** Complete LSP event completion, multi-root/versioned documents, pull/push diagnostics, workspace edits, health, restart/backoff/quarantine, RSS/concurrency/idle limits.
- [ ] **P5-024** Turbo static graph plus version-probed documented CLI (`ls`, dry run, boundaries, affected); optional LSP only after compatibility probe.
- [ ] **P5-025** Next Oxc static routes/actions/boundaries plus DevTools `init`, `nextjs_index`, advertised-tool calls, multi-app/runtime evidence merge.
- [ ] **P5-026** Versioned provider/plugin interfaces for parsers, workspace graphs, route providers, context sources, materializers, and gateway backends.
- [ ] **P5-027** Stable Rust API and generated Python/TypeScript SDKs that call the native daemon; no alternate product runtime; complete `soleaux ci`.
- [ ] **P5-028** Editor extension MVP and capability-gated webhook/event export for automation/SIEM.
- [ ] **P5-029** Optional hybrid lexical/vector/graph search with pinned model/license/hash, sensitivity exclusions, migration, rebuild, and corruption recovery.

### Phase 6 — desktop, mobile, remote control, installers, and operations

- [ ] **P6-001** Tauri desktop shell and daemon lifecycle.
- [ ] **P6-002** Context Inspector, catalog, sessions, health, and diagnostics UX.
- [ ] **P6-003** One Expo/React Native mobile application; parsers remain server-side.
- [ ] **P6-004** Pairing, direct LAN, end-to-end encrypted relay fallback, revoke, and audit.
- [ ] **P6-005** Keychain/keystore and artifact encryption integration.
- [ ] **P6-006** macOS/Windows/Linux development installers.
- [ ] **P6-007** Upgrade, repair, rollback, uninstall, and native-file restoration.
- [ ] **P6-008** Update channel, version alignment, support bundle, and opt-in redacted crash reporting.
- [ ] **P6-009** Device and desktop E2E evidence.
- [ ] **P6-010** First-run detection, operating mode, repository trust, indexing progress/cancel/partial availability, and capability matrix UI.
- [ ] **P6-011** Unified sessions/transcript/event/artifact/lineage, live run console, subagent graph, and handoff UX.
- [ ] **P6-012** Approval inbox, permission profiles, risk preview, biometric/desktop confirmation, and conflict-resolution UI.
- [ ] **P6-013** Memory lifecycle review and rule/skill/agent compatibility/materialization UX.
- [ ] **P6-014** Versioned mobile/remote API, device certificates, replay-safe commands, cursor event stream, APNs/FCM opaque notifications, and remote expiry policy.
- [ ] **P6-015** Backups, restore, integrations, updates, diagnostics, device management, and accessibility/i18n-ready design system.
- [ ] **P6-016** Optional offline mobile library only after encrypted replication, tombstones, quotas, key rotation, and conflict rules pass.

### Phase 7 — assurance, scale, parity, and enterprise readiness

- [ ] **P7-001** Defined-hardware cold/warm p50/p95/p99 benchmark suite for parse, incremental update, LSP, routes, context, policy, session, and search.
- [ ] **P7-002** Parser/LSP corpus, malformed-input and protocol fuzzing.
- [ ] **P7-003** Path jail, shell policy, redaction, prompt-injection, pairing, MCP, update, and cross-workspace security tests.
- [ ] **P7-004** External penetration test.
- [ ] **P7-005** Privacy, retention, deletion, and legal/license review.
- [ ] **P7-006** Desktop/mobile accessibility and internationalization audit.
- [ ] **P7-007** Signed SBOM and build provenance.
- [ ] **P7-008** macOS, Windows, and Linux compatibility matrix including case-insensitive paths and symlink cycles.
- [ ] **P7-009** Incident-response, backup/restore, relay outage, upgrade/downgrade, and rollback exercises.
- [ ] **P7-010** Stage-17-style readiness decision.
- [ ] **P7-011** Large-repository and pathological-file tests: 10k+ files, generated/minified/multi-MB files, concurrent clients, memory pressure, and worker crash recovery.
- [ ] **P7-012** Internet relay production hardening: tenant routing, durable queue, expiry/DLQ, abuse/rate limits, multi-region replay defense, APNs/FCM rotation, SLOs, self-host package, and disaster recovery.
- [ ] **P7-013** Enterprise audit export, retention policy, air-gap installation, and SSO only after local product gates pass.

### Phase 8 — release candidate and general availability

- [ ] **P8-001** Freeze `1.0.0-rc.1` only after Phase 7.
- [ ] **P8-002** Sign/notarize desktop artifacts and sign Windows packages.
- [ ] **P8-003** TestFlight and Play internal rollout.
- [ ] **P8-004** Design-partner staged release.
- [ ] **P8-005** Public staged release with rollback thresholds.
- [ ] **P8-006** Review and explicitly decide `productionClaimAllowed`.
- [ ] **P8-007** Publish release notes, support policy, compatibility table, privacy disclosures, and known limitations.
- [ ] **P8-008** GA verification and `1.0.0`.

## Documentation maintenance

- [x] **DOC-001** Establish one documentation authority hierarchy.
- [x] **DOC-002** Replace historical root README/TASKS/HANDOFF/AGENTS/CHANGELOG with unified native versions.
- [x] **DOC-003** Add project status, roadmap, release, testing, rollout, and marketing documents.
- [x] **DOC-004** Add a fail-closed documentation consistency checker.
- [x] **DOC-005** Add Phase 3 experiment templates and machine schemas.
- [x] **DOC-006A** Review both full transcripts against the proven native source and create the gap audit/registry.
- [x] **DOC-006B** Merge the standardized documentation system into `native/0.4.0-dev.5` through PR #4 at `7af28901...`.
- [ ] **DOC-006C** Merge the transcript gap audit, capability map, expanded tasks, and deferred three-arm Phase 3 correction into native after green CI.
- [ ] **DOC-007** Carry the same hierarchy into `main` during Phase 4.
