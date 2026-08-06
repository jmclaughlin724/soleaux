# Soleaux Roadmap

<!-- soleaux-docs:roadmap current_phase=5 version=0.4.0-dev.5 -->

This is the sole phase model for the unified native product. The transcript audit and gap registry are binding roadmap inputs:

- [`docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md`](docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md)
- [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)
- [`docs/architecture/CAPABILITY-ABSORPTION-MAP.md`](docs/architecture/CAPABILITY-ABSORPTION-MAP.md)

## Program objective

Deliver one local-first product that:

- turns a repository into one lean twelve-slot MCP server;
- compiles accurate, bounded context with trust, provenance, ownership, constraints, validation routes, and explicit gaps;
- governs one catalog of skills, rules, agents, ownership, artifacts, and registered backends;
- uses native parsers, LSPs, and framework providers when selected;
- provides canonical sessions, history, memory, handoffs, runs, approvals, artifacts, policy, and audit without mutating vendor internal stores;
- exposes non-model operations through native CLI, service, desktop, mobile, SDK, editor, and automation surfaces;
- ships only after release assurance and an explicit production-claim decision.

## Phase overview

| Phase | Name | Status | Exit evidence |
|---:|---|---|---|
| 0 | Contract lock and native foundation | **Closed** | Exact native gate receipt |
| 1 | Unified public surface and Context Packet V2 | **Closed** | Exact twelve-tool smoke + schema validation |
| 2 | Gateway, catalog, provisioning, governance | **Closed** | Exact native gate + independent artifact verification |
| 3 | Three-arm real-client product proof | **Deferred; reconciliation required before use** | Market-value + historical-compatibility gates |
| 4 | Correct, durable native alpha foundation | **Closed** | Exact alpha receipt + independent verification |
| 5 | Adapters, lifecycle, intelligence depth, materializers, extensibility | **In progress** | Live matrices, design partners, beta receipt |
| 6 | Desktop, mobile, remote control, installers, operations | Blocked by Phase 5 | Complete app/device/install E2E |
| 7 | Assurance, scale, parity, enterprise readiness | Blocked by Phase 6 | Benchmarks and external assurance |
| 8 | Release candidate and general availability | Blocked by Phase 7 | Signed staged release and explicit production decision |

## Completed convergence

- PR #4 established the documentation authority system.
- PR #5 made `main` canonical while preserving receipt ancestry.
- PR #6 checked the verified Rust workspace into `native/`.
- PR #7 repaired LSP capability truthfulness.
- PRs #12, #15, #17, #18, #21, #22, #23, #25, and #26 closed the remaining native correctness wave.
- PRs #27–#29 added canonical state, recovery, encrypted artifacts, policy, CLI, service, and IPC.
- PR #34 repaired quoted TOML gateway backend discovery.
- PR #32 produced the reproducible unsigned development alpha and exact Phase 4 evidence.
- Phase 4 closure is recorded in [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json).
- PR #36 closed P5-001 workspace/client registry convergence; evidence is recorded in [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json).

## Phase 3 — deferred claims proof

Phase 3 does not block implementation. It remains mandatory before quantified efficacy claims or a reviewed change to `productionClaimAllowed`.

Before any live call, freeze three identical-task/model/client arms:

| Arm | Question |
|---|---|
| `control_no_soleaux` | Does Soleaux beat ordinary selected-client repository access at equal-or-better correctness and lower waste context? |
| `historical_python` | Did native unification retain useful Python/FastMCP behavior? |
| `native_treatment` | Does the locked native product satisfy both gates? |

All attempts and failures remain in the dataset. Context economy cannot compensate for lower correctness.

## Phase 4 — closed native alpha foundation

Phase 4 delivered:

- closed request schemas;
- watcher-backed freshness and hash revalidation;
- transactional edits, rollback, preview claims, and audit receipts;
- idempotency reservation before side effects;
- comprehensive redaction;
- transactional provisioning;
- truthful bounded coverage and continuations;
- typed SQL validation;
- canonical state, migrations, leases, recovery, backup, restore, and repair;
- encrypted content-addressed artifacts and capability policy;
- stable CLI, per-user service, typed local IPC, and peer checks;
- reproducible unsigned alpha packaging and deterministic Cargo SBOM;
- exact operational smoke from extracted package;
- independent verification and synchronized documentation.

Evidence:

- [`PHASE4-ALPHA-CLOSURE-RECEIPT.json`](PHASE4-ALPHA-CLOSURE-RECEIPT.json)
- [`PHASE4-INDEPENDENT-VERIFICATION.json`](PHASE4-INDEPENDENT-VERIFICATION.json)
- [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json)

## Phase 5 — current

P5-001 is closed. The next open task is **P5-002**, followed by the remaining platform matrices and Phase 5 lifecycle and intelligence work.

### Platform adapters

- Claude Code capability probe, SDK execution host, external SessionStore, hooks, permissions, compaction, and restart reconciliation.
- Claude Desktop supported local connector and user-facing export/import boundary.
- Codex CLI/Desktop app-server schemas, generated client, approvals, steering, compaction, archive, cursors, reconnect, and safe mode.
- OpenCode OpenAPI/SSE/plugin schemas, generated client, persistent cursor/reconciliation, permissions, and session lifecycle.
- Cursor and generic MCP-host verification.

### Canonical lifecycle and materialization

- same-platform resume, fork, and archive;
- full memory lifecycle, scope, confidence, sensitivity, expiry, conflict, provenance, import/export, and compaction survival;
- signed handoffs with objective, decisions, tasks, Git/code state, artifacts, exclusions, permissions, and target-native lineage;
- durable runs/subagents with budgets, worktree leases, approvals, attenuation, recovery, cancellation, and aggregation;
- cross-host rules/skills/agents materializer with compatibility/degradation reporting, diff, backup, atomic apply, rollback, echo guards, and load verification.

### Intelligence depth

- complete Oxc extraction;
- Tree-sitter query packs, injections, damaged-file corpus, and incremental behavior;
- LibCST Python writes and `mvdan.cc/sh` semantics/sandbox;
- real LSP multi-language/version matrix;
- Turbo static graph plus version-probed documented CLI;
- Next Oxc static index plus capability-driven DevTools integration and multi-app merge.

### Extensibility and delivery surfaces

- versioned provider interfaces;
- stable Rust API and generated Python/TypeScript daemon SDKs;
- deterministic `soleaux ci`;
- editor extension MVP;
- capability-gated webhook/SIEM export;
- optional licensed/checksummed hybrid search after core gates.

### Phase 5 exit

- declared client, LSP, Turbo, and Next matrices green;
- unknown versions enter safe/read-only mode;
- `anilize` plus two approved design partners validated;
- no direct vendor-store writes;
- exact beta receipt and independent verification.

## Phase 6 — desktop, mobile, remote control, installers, operations

- Tauri/React desktop using the daemon as sole state owner.
- One Expo/React Native mobile application; parsers remain server-side.
- Onboarding, repository trust, indexing progress/cancel/partial availability.
- Workspaces, sessions/transcripts/lineage, Context Inspector, memory/catalog, runs/subagents, approvals/conflicts, health, Turbo/Next views.
- Short-lived pairing, device certificates, hardware-backed keys, LAN, E2E relay fallback, replay-safe commands, push, revoke, and audit.
- macOS/Windows/Linux development installers, updates, repair, rollback, backup/restore, uninstall, and native-file restoration.
- Support bundles, diagnostics, accessibility/i18n-ready design.

## Phase 7 — assurance, scale, parity, enterprise readiness

- defined-hardware cold/warm p50/p95/p99;
- parser/LSP corpus and protocol fuzzing;
- large repositories, generated/minified/multi-MB files, concurrent clients, memory pressure, and crash recovery;
- path jail, shell policy, redaction, prompt injection, pairing, MCP, update, and cross-workspace security;
- production relay queues, expiry/DLQ, abuse controls, replay defense, SLOs, self-hosting, and DR;
- external penetration, privacy, retention, deletion, legal/license, accessibility, and internationalization review;
- signed SBOM/provenance, OS/architecture parity, and incident/rollback exercises.

## Phase 8 — release and rollout

- freeze `1.0.0-rc.1` only after Phase 7;
- sign and notarize desktop artifacts and sign Windows packages;
- TestFlight and Play internal/staged delivery;
- design-partner then public staged rollout with rollback thresholds;
- release notes, support/compatibility/privacy/known-limitations material;
- explicit reviewed `productionClaimAllowed` decision;
- GA verification and `1.0.0`.

## Historical proposal handling

Historical plans remain research only. Do not reintroduce agent-OS positioning, premature RC labels, a production Node daemon, parallel SwiftUI/Compose products, standalone SSE as primary MCP transport, GPL parser dependencies in core, direct vendor database writes, false cross-platform native resume, or root-tool inflation.
