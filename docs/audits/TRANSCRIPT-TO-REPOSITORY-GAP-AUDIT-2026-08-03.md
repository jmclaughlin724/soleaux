# Soleaux Transcript-to-Repository Gap Audit

**Date:** 2026-08-03  
**Status:** reviewed planning input for the unified native product  
**Repository reviewed:** `jmclaughlin724/soleaux`  
**Current product version:** `0.4.0-dev.5`

## 1. Scope and evidence

This audit reconciles:

1. the full system-design transcript;
2. the parser/intelligence and repository-consolidation transcript;
3. the current GitHub branches, pull requests, phase receipts, and independently verified Phase 2 artifact;
4. the normal source tree extracted from the successful Phase 2 artifact.

The transcripts contain research, user requirements, exploratory proposals, superseded implementation claims, and later corrections. They are not all equal authorities. Current authority remains:

```text
locked JSON contracts
→ exact workflow receipts
→ independent verification
→ PROJECT-STATUS.json
→ ROADMAP.md / TASKS.md
→ current phase package
→ public copy
→ historical transcripts
```

A transcript item is treated as a product requirement only when it is consistent with the locked product definition and later corrective decisions.

## 2. Product definition retained

Soleaux is **unified repository intelligence**, not an agent operating system and not a replacement for host agents.

It must provide:

- one lean MCP server through `soleaux serve .`;
- one governed catalog for skills, rules, agents, ownership, registered backends, and materialized projections;
- native repository intelligence using Oxc, Tree-sitter, `pg_query`, shell parsing, LSPs, framework providers, and an on-disk index;
- bounded, provenance-tagged Context Packet V2 output;
- app, CLI, and service access to non-model operations;
- shared sessions, memory, handoffs, policy, approvals, artifacts, and audit behind the public MCP surface;
- safe integration with Claude Code, Claude Desktop, Codex, OpenCode, and supported MCP hosts without writing vendor internal databases.

The exact model-facing public catalog remains twelve slots. Broader capabilities belong behind existing tools, MCP resources, the gateway, daemon API, CLI, desktop, or mobile surfaces.

## 3. Repository state found

### Proven native capabilities

The Phase 2 artifact proves:

- Rust release binaries for `soleaux` and `soleauxd`;
- exact canonical twelve-tool and substituted twelve-tool profiles;
- `soleaux.context/v2` validation;
- SQLite WAL repository index and event append;
- native Oxc parse validation, Tree-sitter structural extraction, `pg_query`, and static shell extraction;
- LSP initialize/cached/pending semantics with an 800 ms soft deadline;
- stdio and authenticated loopback Streamable HTTP;
- hash-bound preview/edit;
- static Turborepo and Next.js providers;
- namespaced gateway, native catalog domains, adopt/attach, and governance materialization.

### Normal source tree actually present in the successful artifact

```text
apps/cli
daemon/engine
daemon/intelligence
daemon/mcp
daemon/storage
```

The following production systems are not present as complete normal-source implementations in that artifact:

```text
apps/desktop
apps/mobile
daemon/adapters
daemon/runs
daemon/sessions
daemon/memory lifecycle service
daemon/policy service
daemon/artifact vault
daemon/remote gateway
materializers
installers
editor extensions
stable SDK packages
plugin provider packages
```

The current GitHub native lineage also stores the proven Rust source through hash-bound CI carriers. Phase 4 must replace carrier-only development with a normal, reviewable Rust source tree.

## 4. Feature coverage matrix

| Capability | Current evidence | Status | Required owner / next phase |
|---|---|---|---|
| Exact twelve-tool public MCP | Native gate and independent smoke | **Proven** | Locked contract |
| Context Packet V2 | Native gate and schema validation | **Proven** | Locked contract |
| Oxc primary JS/TS intelligence | Oxc validates parse; structural ranges still primarily Tree-sitter and the stored program summary is not a full extraction model | **Partial** | Phase 5 intelligence completion |
| Tree-sitter incremental CST | Native parse and incremental paths exist | **Partial** | Query packs, language injections, corpus and watcher integration in Phase 5/7 |
| PostgreSQL parser | `pg_query` core is present | **Proven core** | Add full validate/fingerprint/relation modes and real corpus in Phase 5/7 |
| Shell structure and policy | Tree-sitter command extraction exists | **Partial** | `mvdan.cc/sh`, ShellCheck option, approval/sandbox/process tree in Phase 4/5/7 |
| LSP broker | 800 ms pending/cached path exists | **Partial** | Real server matrix, multi-root, lifecycle/resource limits, completion events in Phase 5/7 |
| On-disk structural index | SQLite files/symbols index exists | **Partial** | Watcher, routes/packages/boundaries, progress/cancel, degraded mode, large-repo gates in Phase 4/5/7 |
| Turborepo | Static packages/tasks/tags and simple affected calculation | **Partial** | Documented CLI probes, boundaries/affected parity, optional LSP matrix in Phase 5 |
| Next.js | Static filesystem route discovery and string-based server-action discovery | **Partial** | Oxc static index plus capability-driven DevTools `init`/discovery/merge in Phase 5 |
| Safe edit | Hash-bound single-file preview/apply and backup | **Partial** | Formatter, diagnostics rollback, LibCST Python path, audit, then multi-file transaction |
| Gateway/catalog/adopt/attach/governance | Native Phase 2 smoke | **Proven foundation** | Live backend/OAuth/materializer testing in Phase 5 |
| Per-user daemon/service and typed local IPC | Per-repository daemon commands exist; no complete installed per-user service/typed client IPC | **Missing** | Phase 4/5 |
| Canonical sessions/history | No session/turn/message/native-mapping projection in proven source | **Missing** | Phase 4 data model + Phase 5 adapters |
| Memory lifecycle | `memory.search` scans configured files; no Proposed→Validated→Active→Superseded/Tombstoned service | **Missing** | Phase 4/5 |
| Cross-platform handoffs with code state | No complete signed handoff/session creation service in proven source | **Missing** | Phase 5 |
| Run and subagent orchestration | No durable run/command/approval/subagent service in proven source | **Missing** | Phase 4/5 |
| Claude Code adapter | No native SDK/SessionStore adapter in proven source | **Missing** | Phase 5 |
| Claude Desktop integration | No complete native extension/export-import path in proven source | **Missing** | Phase 5; hosted CRUD remains a non-goal |
| Codex adapter | No schema-generated app-server adapter in proven source | **Missing** | Phase 5 |
| OpenCode adapter | No OpenAPI/SSE cursor/plugin adapter in proven source | **Missing** | Phase 5 |
| Artifact vault | No production encrypted content-addressed vault/key wrapping in proven source | **Missing** | Phase 4/6/7 |
| Policy/capability engine | Tool annotations and path controls exist; no complete RBAC/ABAC/capability service | **Missing** | Phase 4/5/7 |
| Rules/skills/agents materializer compiler | Registry and adopt managed blocks exist; no full cross-host compatibility compiler/load verification | **Partial** | Phase 5 |
| Desktop application | No complete Tauri/React product in proven source | **Missing** | Phase 6 |
| Mobile application | No authoritative Expo app in proven source | **Missing** | Phase 6 |
| Pairing/LAN/relay/push | Not in proven native source | **Missing** | Phase 6/7 |
| Stable SDKs and plugin API | No stable Rust/Python/TS SDK or provider ABI | **Missing** | Phase 5 |
| Editor extension | Not present | **Missing** | Phase 5/6 |
| Webhook/SIEM event export | Not present | **Missing** | Phase 5/7 |
| Hybrid semantic and graph search | FTS/structural search only | **Missing expansion** | Phase 5/7, license- and sensitivity-gated |
| Install/upgrade/repair/uninstall | Native download helper exists in a later branch, but no full product lifecycle | **Partial** | Phase 4/6/7 |
| Signed production distribution | Not produced | **Missing** | Phase 8 |

## 5. Capabilities that must not become new root tools

The transcripts name more conceptual operations than the twelve-slot public profile. These capabilities are still required, but must be absorbed as defined in `docs/architecture/CAPABILITY-ABSORPTION-MAP.md`.

Examples:

- `history.search`, `session.read`, and `session.handoff` become daemon services, resources, CLI/app operations, and registry/session domains—not three new root tools.
- `memory.propose` and `memory.correct` belong to capability-gated daemon/app/CLI APIs while `memory.search` remains the root read tool.
- SQL fingerprint/relation extraction are modes of the optional PostgreSQL provider and CLI/API.
- Turbo tasks, boundaries, and affected are modes/resources behind the optional Turbo slot and CLI/API.
- Next route detail, server actions, and boundary analysis are modes/resources behind `next.get_routes` and CLI/API.
- run, approval, device, backup, update, and remote operations never enter `tools/list`.

## 6. Phase 3 experiment conflict

Two incompatible Phase 3 designs currently exist:

1. `native/0.4.0-dev.5/phase3/experiment-design.json` fixes GitHub Models, six synthetic single-turn tasks, and a historical Python baseline.
2. `docs/experiments/phase3/` fixes three real `anilize` tasks but leaves the model/client unselected.

Neither design alone proves the complete north-star claim:

> same model and task, with Soleaux versus without Soleaux, at equal-or-better correctness and lower waste context.

Before any live call, Phase 3 must be re-frozen as a three-arm experiment:

| Arm | Purpose |
|---|---|
| `control_no_soleaux` | Prove market value versus the selected client's ordinary repository tools and no Soleaux MCP |
| `historical_python` | Prove native unification does not regress the useful Python/FastMCP lineage |
| `native_treatment` | Prove the locked native twelve-tool product |

The old GitHub Models carrier experiment is retained as historical development evidence only. It is not authorized as the current Phase 3 product-proof run.

## 7. Branch audit

Current long-lived branches:

```text
main
native-wedge/0.4.0-dev.4
phase2/native-lineage-a-0.4.0-dev.5
phase3/live-wedge-0.4.0-dev.5
native/0.4.0-dev.5
docs/unified-project-system-0.4.0-dev.5
```

Findings:

- `native/0.4.0-dev.5` is the most complete linear native lineage and already contains the Phase 3 branch changes plus installer/verifier work.
- `phase3/live-wedge-0.4.0-dev.5` is a strict ancestor/subset of `native/0.4.0-dev.5` and should not remain an independent source of truth.
- `phase2/...` and `native-wedge/...` are evidence branches. They should be frozen, tagged, and retained until their receipts and artifacts are safely referenced from the canonical tree.
- `main` remains the historical Python default at the time of audit. The reviewed receipt-preserving native/docs consolidation may make it the canonical project branch before Phase 4 closes; Phase 4 still must check in the normal Rust source and direct native CI before alpha/release claims.
- draft PR #3 is superseded by the proven Phase 0–2 lineage and must be closed.
- documentation PR #4 was correctly retargeted and merged into `native/0.4.0-dev.5` at `7af28901a67d7909a3442b0d22801ab3fe619293`.

The binding cleanup sequence is in `docs/operations/BRANCH-CONSOLIDATION-PLAN.md`.

## 8. Superseded or unsafe transcript proposals

These requirements must not be reintroduced:

| Superseded proposal | Binding correction |
|---|---|
| “Agent operating system” positioning | Unified repository intelligence |
| `1.0.0-rc.1` before proof and assurance | Stay in `0.4.0-dev.x` until gates allow progression |
| Node daemon as production | Rust/Tokio daemon; Node only fixtures/build tooling where explicitly approved |
| Separate SwiftUI and Compose products | One Expo/React Native application with native modules |
| Standalone SSE as primary MCP transport | stdio + Streamable HTTP |
| Oxc AST codegen as default edit path | Source-range patches, formatter, diagnostics, reindex |
| `pglast` / `bashlex` bundled in core | `pg_query` and permissive shell parser; GPL paths optional only after legal review |
| Optional MCP | `soleaux serve .` is a primary product path |
| Large model-facing operation catalog | Twelve-slot public profile; depth behind resources/services/CLI/app |
| Bidirectional Claude Desktop hosted-memory sync | Explicit non-goal; supported MCP and export/import only |
| False cross-platform native resume | New target session from signed handoff plus canonical lineage |

## 9. Release-blocking next work

The complete executable registry is `TASKS.md`. The highest-priority additions from this audit are:

1. merge this audit/expanded roadmap into `native/0.4.0-dev.5`, close obsolete PR #3, and merge the receipt-preserving consolidated lineage to `main`;
2. keep Phase 3 deferred; before any efficacy claim or live call, reconcile it into the three-arm real-client experiment;
3. materialize the proven Rust source as normal files and make native CI run directly from checkout;
4. expand the canonical data model and implement durable sessions, memory, handoffs, runs, approvals, artifacts, policy, and recovery;
5. implement and live-test the four platform adapters;
6. complete Oxc/Tree-sitter/LSP/shell/Turbo/Next production depth;
7. implement materializers, SDK/provider APIs, editor integration, and exact CLI/service lifecycle;
8. build the Tauri desktop and Expo mobile clients on the same typed daemon API;
9. complete scale, performance, security, accessibility, compatibility, packaging, and staged release gates.

This audit adds requirements to the roadmap; it does not claim those features are implemented.
