# Soleaux Roadmap

<!-- soleaux-docs:roadmap current_phase=4 version=0.4.0-dev.5 -->

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
| 3 | Three-arm real-client product proof | **Deferred; reconciliation required before use** | Market-value + historical-compatibility gates and independent verification |
| 4 | Correct, durable native alpha foundation | **In progress** | Correctness gates, durable core/service/CLI, alpha receipt and independent verification |
| 5 | Adapters, lifecycle, intelligence depth, materializers, extensibility | Blocked by Phase 4 | Live client/LSP/framework/design-partner matrix and beta receipt |
| 6 | Desktop, mobile, remote control, installers, operations | Blocked by Phase 5 | Complete app/device/install E2E |
| 7 | Assurance, scale, parity, enterprise readiness | Blocked by Phase 6 | Benchmarks, external assurance, OS/security/scale matrix |
| 8 | Release candidate and general availability | Blocked by Phase 7 | Signed staged release and explicit production-claim decision |

## Completed convergence

- documentation PR #4 established the authority system;
- consolidation PR #5 made `main` canonical while preserving receipt ancestry;
- PR #6 checked the verified Rust workspace into `native/` and established direct native CI;
- PR #7 repaired LSP capability truthfulness;
- PRs #8 and #10 established a secured telemetry API and daemon-served dashboard foundation;
- historical phase branches were removed only after durable receipt/archive tags were created.

## Phase 3 — deferred three-arm product proof

Phase 3 does not block implementation. It remains mandatory before quantified efficacy claims or a reviewed change to `productionClaimAllowed`.

Before any live call, freeze three identical-task/model/client arms:

| Arm | Question answered |
|---|---|
| `control_no_soleaux` | Does Soleaux beat the selected client's ordinary repository access at equal-or-better correctness and lower waste context? |
| `historical_python` | Did native unification retain useful Python/FastMCP behavior? |
| `native_treatment` | Does the locked native twelve-tool product satisfy both gates? |

All attempts and failures remain in the dataset. Context economy cannot compensate for lower correctness. Exact receipt and independent verification are required.

## Phase 4 — correct, durable native alpha foundation

### Workstream 1: correctness closure

- closed JSON request schemas before dispatch;
- fresh index/context and content-hash revalidation after external or applied changes;
- transactional edit, rollback, preview consumption and audit receipts;
- transactional idempotency reservation before side effects;
- comprehensive secret redaction independent of variable names;
- transactional adopt/apply/revert;
- truthful bounded coverage, gaps, truncation and continuations;
- typed invalid-SQL semantics;
- complete preview binding and concurrency guarantees;
- permanent LSP capability-regression tests from PR #7.

### Workstream 2: canonical durable core

- accounts, workspaces, native mappings, sessions, turns, messages and structured parts;
- memory claims/lifecycle, handoffs, runs, subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones and retention;
- serialized writer/read pool, migrations, replay, backup, repair and downgrade refusal;
- durable operation reservations, execution leases, process/native-session reconciliation, cancellation and pending-approval recovery;
- encrypted content-addressed artifact vault, OS-keychain master keys, per-workspace separation, redaction and policy/capability foundation.

### Workstream 3: CLI, service and alpha operations

- exact commands: `serve`, `install`, `service`, `doctor`, `ci`, `cache`, `index`, `integrate`, `handoff`, `backup`, `restore`, `export`, `repair`, `uninstall --restore-native`;
- `--json` on all commands and `--dry-run` on mutators;
- per-user service and typed local IPC with peer checks;
- clean install/service/restart/backup/repair/uninstall smoke;
- reproducible unsigned alpha, exact receipt and independent artifact verification.

## Phase 5 — adapters, lifecycle, intelligence depth and extensibility

### Platform adapters

- Claude Code Agent SDK execution host, external SessionStore, hooks, permission events, compaction/subagent events and restart reconciliation;
- Claude Desktop supported local connector/MCP and explicit export/import boundary;
- Codex generated app-server client, approvals, steering, compaction, archive, cursors, reconnect and safe mode;
- OpenCode generated OpenAPI client, persistent SSE cursor/reconciliation, permissions, plugin and session lifecycle;
- Cursor and generic MCP-host verification.

### Canonical lifecycle and orchestration

- same-platform resume/fork/archive where supported;
- full memory lifecycle and contradiction resolution;
- signed cross-platform handoffs with Git/code state and destination-native lineage;
- durable runs/subagents with budgets, worktree leases, capability attenuation, recovery, cancellation propagation and aggregation.

### Intelligence depth

- full Oxc symbol/module/JSX/route/action extraction;
- Tree-sitter query packs, language injection, incremental watcher integration and damaged-file corpus;
- LibCST Python writes and `mvdan.cc/sh` semantics/sandbox;
- real LSP matrix, multi-root/versioning/diagnostics/workspace edits/resource limits;
- Turbo static graph plus version-probed documented CLI and optional LSP after probe;
- Next Oxc static index plus capability-driven DevTools `init`/index/advertised calls and multi-app evidence merge.

### Materializers and extensibility

- compatibility/degradation compiler for rules, skills and agents;
- diff/backup/atomic apply/rollback/origin/revision/idempotency/echo guards and load verification;
- versioned parser/workspace/route/context/materializer/gateway provider interfaces;
- stable Rust API and generated Python/TypeScript daemon SDKs;
- deterministic `soleaux ci`;
- editor extension MVP and redacted webhook/SIEM export;
- optional licensed/checksummed hybrid search after core correctness.

### Exit

- declared adapter and LSP/framework matrices green;
- `anilize` plus two additional design partners validated;
- unknown versions enter safe/read-only mode;
- no direct vendor-store writes;
- beta receipt and independent verification.

## Phase 6 — desktop, mobile, remote control, installers and operations

- Tauri/React desktop using the daemon as sole state owner;
- one Expo/React Native mobile app; no parser stack on device;
- onboarding, repository trust, indexing progress/cancel/partial availability;
- workspaces, sessions/transcripts/lineage, Context Inspector, memory/catalog, run/subagent graph, approvals/conflicts, intelligence health, Turbo/Next views;
- short-lived pairing, device certificates, hardware-backed keys, LAN, E2E relay fallback, replay-safe commands, capabilities/risk tiers, biometrics, opaque push, revoke and audit;
- macOS/Windows/Linux development installers, updates, repair, rollback, backup/restore, uninstall and native-file restoration;
- support bundle, diagnostics, accessibility/i18n-ready design;
- optional offline mobile library only after encrypted replication/tombstone/conflict rules pass.

## Phase 7 — assurance, scale, parity and enterprise readiness

- defined-hardware cold/warm p50/p95/p99 for parse, incremental update, LSP, routes, context, policy, sessions and search;
- parser/LSP corpus, malformed input and protocol fuzzing;
- large repositories, generated/minified/multi-MB files, concurrent clients, memory pressure and worker-crash recovery;
- path jail, shell policy, redaction, prompt injection, pairing, MCP, update and cross-workspace security tests;
- production relay scaling, queue/expiry/DLQ, abuse controls, replay defense, push rotation, SLOs, self-host package and disaster recovery;
- external penetration, privacy, retention, deletion, legal/license, accessibility and internationalization reviews;
- signed SBOM/provenance, OS/architecture matrix and incident/rollback exercises;
- enterprise audit/retention/air-gap/SSO only after local gates.

## Phase 8 — release and rollout

- freeze `1.0.0-rc.1` only after Phase 7;
- sign/notarize desktop and sign Windows packages;
- TestFlight and Play internal/staged delivery;
- design-partner then public staged rollout with rollback thresholds;
- release notes, support/compatibility/privacy/known-limitations material;
- explicit reviewed `productionClaimAllowed` decision;
- GA verification and `1.0.0`.

## Historical proposal handling

Historical plans remain research only. Do not reintroduce agent-OS positioning, premature RC labels, a production Node daemon, parallel SwiftUI/Compose product trees, standalone SSE as primary MCP transport, GPL parser dependencies in core, direct vendor database writes, false cross-platform native resume, or a large root tool catalog.
