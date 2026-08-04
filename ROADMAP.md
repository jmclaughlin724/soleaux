# Soleaux Roadmap

<!-- soleaux-docs:roadmap current_phase=4 version=0.4.0-dev.5 -->

This is the sole phase model for the unified native product. The transcript gap audit and machine registry are part of this roadmap:

- [`docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md`](docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md)
- [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)
- [`docs/architecture/CAPABILITY-ABSORPTION-MAP.md`](docs/architecture/CAPABILITY-ABSORPTION-MAP.md)

## Program objective

Deliver one local-first product that:

- turns a repository into one lean twelve-slot MCP server;
- compiles accurate bounded context with trust, provenance, ownership, constraints, validation routes, and explicit gaps;
- governs one catalog of skills, rules, agents, ownership, artifacts, and registered backends;
- uses native parsers, LSPs, and framework providers when selected;
- provides canonical sessions, memory, handoffs, runs, approvals, artifacts, policy, and audit without mutating vendor internal stores;
- exposes non-model operations through the native app, CLI, typed API, editor integration, and mobile control plane;
- ships only after live product proof and release assurance.

## Phase overview

| Phase | Name | Status | Exit evidence |
|---:|---|---|---|
| 0 | Contract lock and native foundation | **Closed** | Exact native gate receipt |
| 1 | Unified public surface and Context Packet V2 | **Closed** | Exact twelve-tool smoke + schema validation |
| 2 | Gateway, catalog, provisioning, governance | **Closed** | Exact native gate + independent artifact verification |
| 3 | Three-arm real-client product proof | **Deferred; reconciliation required before use** | Market-value + historical-compatibility gates and independent verification |
| 4 | Canonical source, durable core, and alpha foundation | **In progress** | Consolidated lineage, normal Rust tree, durable state/service/CLI, alpha artifact |
| 5 | Live adapters, data lifecycle, intelligence depth, and extensibility | Blocked by Phase 4 | Client/LSP/framework/design-partner matrices and beta receipt |
| 6 | Desktop, mobile, remote control, installers, and operations | Blocked by Phase 5 | Full app/device/install E2E |
| 7 | Assurance, scale, parity, and enterprise readiness | Blocked by Phase 6 | Benchmarks, external assurance, OS/security/scale matrix |
| 8 | Release candidate and general availability | Blocked by Phase 7 | Signed staged release and explicit production-claim decision |

## Immediate branch convergence

Before the next Phase 4 implementation batch:

1. preserve PR #4 as the completed standardized-docs merge at `7af28901...`;
2. merge this transcript gap audit into `native/0.4.0-dev.5`;
3. close obsolete PR #3;
4. use `native/0.4.0-dev.5` as the sole working branch;
5. retain Phase 0–2 evidence branches read-only until Phase 4 tags and references preserve them;
6. merge the consolidated lineage to `main` through a reviewed merge commit, then keep Phase 4 open until normal source and direct native CI pass.

See the branch consolidation plan.

## Phase 3 — deferred three-arm product proof

**Status:** deferred by owner direction on 2026-08-03. It does not block Phase 4–8 implementation. It remains mandatory before quantified efficacy claims or a reviewed change to `productionClaimAllowed`.

Two incompatible preparations exist: the removed/superseded GitHub Models synthetic carrier and the real `anilize` task package. Before any live call, the experiment must be re-frozen with three arms:

| Arm | Question answered |
|---|---|
| `control_no_soleaux` | Does Soleaux beat ordinary client repository access at equal-or-better correctness and lower waste context? |
| `historical_python` | Did native unification preserve useful Python/FastMCP behavior? |
| `native_treatment` | Does the locked native twelve-tool surface satisfy both gates? |

Execution requires one exact authenticated model/client/build, identical parameters/budgets, model-free oracles, all attempts retained, exact twelve-tool and Context V2 integrity, no secret leakage, exact receipt, and independent verification.

## Phase 4 — canonical source and durable core

This phase consolidates the working lineage and establishes the source, durability, service, security, and operational foundation required by all later product surfaces.

Workstreams:

1. **Source and branches:** reviewed native-to-main merge, normal checked-in Rust workspace, direct native CI, durable receipt/archive references, redundant branch cleanup.
2. **Canonical state:** accounts, workspaces, native mappings, sessions, turns, messages, content parts, memory, catalog, runs/subagents, approvals, conflicts, artifacts, materializations, cursors, audit, tombstones, retention.
3. **Durability:** serialized writes, migrations, replay, backup/repair, idempotency reservation, execution leases, process/session reconciliation, approval recovery.
4. **Security foundation:** encrypted artifact vault, keychain, per-workspace keys, peer credentials, policy/capability service, redaction.
5. **Operations:** exact CLI contract, per-user service, typed IPC, install/doctor/backup/restore/uninstall, unsigned alpha artifact.

## Phase 5 — adapters, lifecycle, intelligence, and extensibility

Workstreams:

1. **Adapters:** Claude Code SDK/SessionStore/hooks; Claude Desktop connector/export-import; Codex schema-generated app-server; OpenCode generated OpenAPI/SSE/plugin.
2. **Canonical lifecycle:** memory validation/supersession/tombstones, session history, native resume/fork, signed cross-platform handoffs, durable runs/subagents/approvals.
3. **Materializers:** compatibility analysis, diff/backup/atomic apply/rollback, origin/revision/idempotency/echo guards, load verification.
4. **Intelligence depth:** real Oxc extraction, Tree-sitter query/injection corpus, LibCST, `mvdan.cc/sh`, real LSP matrix, Turbo documented CLI/probes, Next static/runtime merge.
5. **Extensibility:** versioned provider APIs, native daemon SDKs, CI mode, editor extension, webhook/event export.
6. **Search and consumers:** optional licensed hybrid search, `anilize` plus two additional design partners.

## Phase 6 — product applications and remote operations

- Tauri/React desktop with onboarding, workspaces, sessions, Context Inspector, memory/catalog, run/subagent graph, approvals/conflicts, intelligence health, Turbo/Next views, devices, backups, updates, diagnostics.
- One Expo/React Native mobile app using the same typed daemon/remote API; no parsers on device.
- Pairing, hardware-backed identity, direct LAN, encrypted relay fallback, replay-safe commands, capability/risk tiers, biometrics, push, revocation, audit.
- Development installers, update/repair/rollback/uninstall, keychain/keystore, support bundle, opt-in redacted crash reporting.
- Offline mobile library remains optional until encrypted replication and tombstone/conflict rules pass.

## Phase 7 — assurance, scale, parity, and enterprise readiness

- native cold/warm performance against all declared p95 targets;
- real client, LSP, parser, framework, OS, architecture, large-repository, pathological-file, concurrent-client, and worker-crash matrices;
- protocol/parser fuzzing and prompt-injection/redaction/path/shell/capability penetration tests;
- production relay hardening and outage/recovery exercises;
- external security, privacy, licensing, accessibility, internationalization, and incident-response reviews;
- signed SBOM/provenance and compatibility table;
- air-gap, SSO, audit export, and retention only after local gates.

## Phase 8 — release and rollout

- `1.0.0-rc.1` only after Phase 7;
- signed/notarized desktop and signed Windows artifacts;
- TestFlight and Play internal/staged delivery;
- design-partner then public staged rollout with rollback thresholds;
- release notes, support policy, privacy disclosures, known limitations;
- explicit reviewed `productionClaimAllowed` decision;
- GA verification and `1.0.0`.

## Historical proposal handling

Historical plans are retained for research but cannot advance status. Superseded choices—agent-OS positioning, premature RC labels, production Node daemon, parallel native mobile products, standalone SSE, GPL parser dependencies in core, and a large root tool catalog—remain prohibited.
