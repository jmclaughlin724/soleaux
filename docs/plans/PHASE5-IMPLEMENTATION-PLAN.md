# Phase 5 Implementation Plan

<!-- soleaux-docs:phase5-plan current_phase=5 version=0.4.0-dev.5 -->

**Status:** active — this is the current-phase implementation plan in the [`AGENTS.md`](../../AGENTS.md) authority order.
**Registry owner:** [`TASKS.md`](../../TASKS.md) remains the sole owner of the task registry and completion checkboxes.
This document owns acceptance detail, verified entry state, evidence plans, and dependency structure, and links back to the registry per task.
Never read completion state from this document.
**Machine-readable edges:** [`PHASE5-DEPENDENCIES.json`](PHASE5-DEPENDENCIES.json) (`soleaux.plan-dependency-graph/v1`); prose here owns semantics, that file owns edges and the declared `nextOpen` pointer validated by `scripts/check_documentation_consistency.py`.

Inputs joined per task: the [`TASKS.md`](../../TASKS.md) definition, the [gap registry](../audits/TRANSCRIPT-GAP-REGISTRY.json), the deep requirement lists in the [gap audit](../audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md) §6–§9, and the mandated owners in the [capability absorption map](../architecture/CAPABILITY-ABSORPTION-MAP.md).
Entry-state claims below were verified against source on 2026-08-07 at main `7c350e9`.

## Universal definition of done

Every task below inherits all of this; per-task sections state only deltas.

1. The seven-point capability checklist from the [absorption map](../architecture/CAPABILITY-ABSORPTION-MAP.md): versioned schema; daemon/API or resource owner; policy and risk classification; provenance and sensitivity labels; size, time, and continuation limits; tests and compatibility evidence; explicit CLI, app, SDK, or materialized-file access path.
2. The ten-command native gate ([`TEST-STRATEGY.md`](../testing/TEST-STRATEGY.md)): fmt, check, clippy `-D warnings`, test, release build, `cargo audit --deny warnings`, and the four binary `--help`/`--version` interface checks.
3. Canonical and substituted MCP smokes at exactly twelve tools; the eleven fail-closed cases in `TEST-STRATEGY.md` stay intentionally failing.
4. A receipt shaped like `soleaux.phase5-task-group-receipt/v1` (precedent: [`P5-002-P5-006-CLOSURE-RECEIPT.json`](../../P5-002-P5-006-CLOSURE-RECEIPT.json)) with pre- and post-merge workflow run IDs.
5. Coordinated doc-sync per `AGENTS.md`, including this plan's `nextOpen` pointer and the consistency checker's expectations.
6. Locked invariants untouched: twelve-tool ceiling, contract digests, `0.4.0-dev.5`, `productionClaimAllowed=false`; GAP-016 prohibitions respected.

## Waves and parallel workstreams

Per `AGENTS.md`, Phase 5 executes top-down unless a dependency requires a documented parallel workstream.
This section is that documentation.
Lanes B–E touch crates disjoint from lane A (materializer vs LSP subsystem vs turborepo/nextjs modules vs oxc/tree-sitter modules vs state/ipc/mcp); every cross-lane dependency is a declared edge in [`PHASE5-DEPENDENCIES.json`](PHASE5-DEPENDENCIES.json), and no task starts with an unmet edge.
Early lane starts are dependency-driven: P5-021 blocks P5-025 (Next extraction must move from string matching to real Oxc), and P5-008/021/022/024/025 jointly block P5-026 (provider traits are abstracted from working instances, not invented ahead of them).
The adapter wave starts during P5-011/P5-012 because partner validation is calendar-bound external work sharing no files with the adapter lane.
Phase 3 is claims-only by decree and blocks nothing except the P8-006 production-claim decision.

| Wave | Name | Tasks | Entry |
|---|---|---|---|
| 0 | Consolidation + drift fixes | this plan, dependency graph, gap-registry repairs, pointer fixes, checker three-way validation | none (landed by the plan PR) |
| 1 | Foundations | P5-W1 → P5-007; P5-008 ∥; lane starts P5-009a, P5-010, P5-021, P5-022 | none |
| 2 | Beta validation | P5-009a and P5-010 close; P5-011 → P5-012 → P5-013 beta receipt | P5-007, P5-008, P5-009a, P5-010 |
| 3 | Admission + adapters | P5-V1 → P5-014, P5-016, P5-017; P5-015 ∥ | P5-007, P5-W1 (P5-013 not required — documented deviation above) |
| 4 | Memory, handoffs, orchestration | P5-018; P5-019; P5-020 | P5-007, P5-W1; P5-014 for the P5-019 import side and the P5-020 execution host |
| 5 | Depth | P5-021, P5-022 close; P5-023 → P5-009b; P5-024; P5-025 | P5-009a; P5-010; P5-021 for P5-025 |
| 6 | Extensibility + close | P5-026 → P5-027 → P5-028; P5-029 optional | P5-008, P5-021, P5-022, P5-024, P5-025 |
| — | Continuous | G0 external-evidence receipt schema + P7-010 readiness definition; Phase 3 prerequisites and execution | none; claims-only |

Lanes: **A** lifecycle (W1 → 007 → V1 → adapters → 018/019/020) · **B** materializer (008) · **C** LSP (009a → 023 → 009b) · **D** build tools (010 → 024/025) · **E** parsers (021, 022) · **F** docs/claims (wave 0, G0, Phase 3).

## Dependency table

| Task | blockedBy | Reason |
|---|---|---|
| P5-W1 | — | mechanical wiring, no prerequisites |
| P5-007 | P5-W1 | session smokes need mcp→state visibility |
| P5-008 | — | rides preview/edit + provisioning machinery |
| P5-009a | — | wires servers against current subsystem |
| P5-010 | — | static providers exist |
| P5-011 | P5-007, P5-008, P5-009a, P5-010 | partner validation exercises lifecycle + matrices |
| P5-012 | P5-011 | anilize first; approval process defined in wave 0 |
| P5-013 | P5-011, P5-012 | beta receipt needs partner evidence |
| P5-V1 | P5-W1, P5-007 | verifies admission into session state using the keystore |
| P5-014 | P5-V1 | Claude adapter write path needs admission |
| P5-015 | P5-007 | permanently read-only; no verifier needed |
| P5-016 | P5-V1 | Codex write path needs admission |
| P5-017 | P5-V1 | OpenCode write path needs admission |
| P5-018 | P5-W1, P5-007 | entity-backed memory + session provenance |
| P5-019 | P5-007, P5-014 | handoff export needs sessions; import side needs an adapter host |
| P5-020 | P5-W1, P5-007, P5-014 | orchestration drives an execution host; attenuation needs the vault wiring |
| P5-021 | — | parser lane |
| P5-022 | — | parser lane |
| P5-023 | P5-009a | subsystem depth builds on the wired matrix harness |
| P5-009b | P5-023 | full-depth matrix re-run closes the P5-009 checkbox |
| P5-024 | P5-010 | version-probed CLI extends the matrix contract |
| P5-025 | P5-010, P5-021 | real Oxc route extraction replaces string matching |
| P5-026 | P5-008, P5-021, P5-022, P5-024, P5-025 | traits abstracted from working instances |
| P5-027 | P5-026 | SDKs and `soleaux ci` sit on the provider surface |
| P5-028 | P5-027 | extension + export consume the stable API |
| P5-029 | P5-018 | optional; sensitivity exclusions need the memory model |

## Per-task plan

### P5-W1 — wire soleaux-mcp→soleaux-state and give soleaux-vault its first consumer

Intent: registered in [`TASKS.md`](../../TASKS.md) as a Phase 5 pre-task discovered by the 2026-08-07 consolidation.
Entry state: `native/daemon/mcp/Cargo.toml` has no `soleaux-state` dependency (memory.search is filesystem-only); `soleaux-vault` is a dependency of no crate despite complete `ArtifactVault`/`KeyRing`/`PolicyEngine` implementations.
Acceptance: the dependency edges exist; the daemon constructs the keystore and policy engine at boot; behavior otherwise unchanged; compile-and-smoke proven.
Evidence: native gate + both MCP smokes; receipt `P5-W1-CLOSURE-RECEIPT.json`.

### P5-007 — canonical session/history service, same-platform resume/fork/archive

Entry state: all entities exist (`native/daemon/state/src/model.rs` — `SessionPayload` with `parent_session_id`/`lineage_root_id`, `TurnPayload`, `MessagePayload`, `ContentPartPayload`; `RelationshipKind::{Lineage, Contains}`); `native/daemon/state/examples/state_smoke.rs` is a working walkthrough; **no session IPC methods, no service module, no CLI subcommand, no MCP resource exist**; `session_state` is an unconstrained `String`.
Acceptance: typed session/turn/message IPC surface in a new `native/daemon/ipc/src/session.rs` mirroring `registry.rs` conventions (`bounded_response`, cursors, `REGISTRY_PAGE_LIMIT` bounds, 1 MiB frame); a validated state machine for `session_state` including archive; lineage traversal and ordinal-ordered turn pagination; resume/fork/archive are same-platform only — cross-platform continuation is exclusively a P5-019 handoff, never a false native resume (GAP-016); `history.search`/`session.read` land behind the absorption-map owners (MCP resources + an explicit `memory.search` mode), never as new root tools.
Evidence: restart-persistence smoke extended to sessions; receipt `P5-007-CLOSURE-RECEIPT.json`.

### P5-008 — materializer compatibility compiler with atomic apply/rollback

Entry state: `native/daemon/mcp/src/provisioning.rs` (982 lines) already implements plans, preimage hashes, backups, managed regions, and revert for host config; `MaterializationPayload` is defined and never constructed; registry domains project skills/agents/rules read-only.
Acceptance — the nine-item list from the gap audit §7: compatibility/degradation reports; guidance-versus-enforcement distinction; diff; backup; atomic apply; rollback; object/revision/origin/idempotency metadata; echo prevention (a materialized file must not re-enter the registry scan as a source object); native load verification.
Target surfaces per client are enumerated in [`CLIENT-CAPABILITY-MATRIX.md`](../testing/CLIENT-CAPABILITY-MATRIX.md) §Mechanism map.
Rides the `preview`/`edit` slots and daemon operations per the absorption map; writes `MaterializationPayload` records with `RelationshipKind::Materializes`.
Evidence: materialize → verify → mutate-by-hand → detect-echo → rollback smoke; receipt.

### P5-009 — real LSP matrix (split: a wires, b closes)

Entry state: `native/daemon/intelligence/src/lsp.rs` supervises 3 of 17 families (vtsls/typescript-ls, basedpyright/pyright, bash-ls) with P4-025 capability truthfulness and the 800 ms soft deadline; `rss_limit_bytes`/`idle_timeout_ms`/`maximum_open_documents` are declared but unenforced; no push diagnostics; the historical catalog `src/soleaux/lsp/providers.py` documents ten providers with install hints.
**P5-009a** (wave 1): wire all 17 families — TypeScript/VTSLS, BasedPyright, Bash, Rust, Go, SourceKit, clangd, Kotlin, JDT, Vue, Svelte, Astro, MDX, YAML, JSON, HTML, CSS — against current subsystem capability; publish capability matrix **v1** with explicit not-yet columns for push diagnostics, multi-root, and workspace edits; unknown or absent servers degrade truthfully.
**P5-009b** (wave 5, after P5-023): re-run the harness at full depth, publish matrix **v2**, close the registry checkbox.
Evidence: per-server conformance runs in CI with pinned server versions; matrix contract file with digest, mirroring the client-matrix precedent.

### P5-010 — Turborepo and Next.js version matrices on real repositories

Entry state: `turborepo.rs` static graph works but `documented_cli_probed` is hardcoded false; `nextjs.rs` detects route methods by string matching; pnpm workspace parsing is line-based and mishandles `catalog:`/`allowBuilds:` blocks (this repository's own `pnpm-workspace.yaml` triggers it).
Acceptance: milestone 1 authors `native/contracts/turbo-next-matrix-v1.json` (pinned Turborepo and Next.js versions, repository layouts, digests) mirroring the client-matrix contract; fix the pnpm `catalog:` parsing defect with a regression test against this repository's workspace file; validate the matrix against real repositories including this one.
Evidence: matrix contract + per-version probe runs; receipt.

### P5-011 / P5-012 — design partners

P5-011: attach and validate `jmclaughlin724/anilize` end-to-end (attach, index, context.compile, semantic operations, doctor) with recorded evidence.
P5-012: two additional approved partners; the approval definition (owner-approved organization, granted repository access, recorded opt-in, support channel, rollback contact — see [`ROLLOUT-PLAN.md`](../release/ROLLOUT-PLAN.md) alpha controls) landed with this plan; partner acquisition is owner-owned external work.
Evidence: per-partner validation records inside the Phase 5 receipt chain.

### P5-013 — Phase 5 beta receipt and independent verification

Acceptance: the nine-condition independent-verification list in [`EVIDENCE-AND-RECEIPTS.md`](../operations/EVIDENCE-AND-RECEIPTS.md); verification must not reuse the producing workflow environment; closes the phase only with every checklist row in [`RELEASE-CHECKLIST.md`](../../RELEASE-CHECKLIST.md) §Phase 5 green.

### P5-V1 — daemon-trusted admission receipt verifier

Intent: registered in `TASKS.md` as a Phase 5 pre-task; this is the unowned dependency named at `native/daemon/ipc/src/compatibility.rs:231` and `PROJECT-STATUS.json` `externalRuntimeWriteAdmission`.
Entry state: every external client is read-only; `mutationEligible` is empty; a caller-computed hash is never accepted as authorization.
Acceptance: a daemon-side verifier that admits an external client to write mode only on a cryptographically bound receipt the daemon itself can verify (keystore-backed), plus the reviewed lifecycle oracle the matrix doc requires; matrix `mutationEligible` transitions become possible but remain per-client reviewed decisions; fail-closed on any verification error.
Evidence: admission-refusal and admission-grant smokes; adversarial forged-receipt test; receipt.

### P5-014..P5-017 — deep adapters

Entry state: no adapter code exists; the capability-matrix contract, compatibility enforcement, and unused `AdapterCursor` persistence are complete.
Common constraints: unknown versions stay read-only/safe mode (`AGENTS.md` hard stop 9); version bumps follow the five-step matrix protocol; vendor-native stores are never written directly.
- **P5-014** Claude Code: SDK execution host with external SessionStore, hooks, permission events, compaction/subagent events, restart reconciliation; pinned `2.1.223`.
- **P5-015** Claude Desktop: user-authorized export/import and supported local connector only; permanently read-only by documentation contract; no hosted CRUD.
- **P5-016** Codex: **schema-generated** app-server client (JSONL over stdio) with approvals, steering, compaction, archive, cursors (use `AdapterCursor`), reconnect, safe mode; pinned `0.146.1`.
- **P5-017** OpenCode: **OpenAPI-generated** client with persistent SSE cursor reconciliation, permissions, fork/abort/summarize/revert, plugin compatibility; pinned `1.18.14`.
Evidence: per-adapter live capability probes upgrading the matrix from documentation-verified to runtime-verified; receipts.

### P5-018 — memory lifecycle

Entry state: `MemoryClaimPayload` is complete (validated confidence, supersedes, sensitivity, expiry) but `memory_state` is unconstrained; `memory.search` (`native/daemon/mcp/src/memory.rs`) is filesystem-based and never reads claim entities; no propose/validate operation exists on any surface.
Acceptance: the `Proposed→Validated→Active→Superseded/Tombstoned/Rejected` machine with transition guards; scopes, conflicts (`ConflictPayload` writer), provenance, import/export, compaction survival; capability-gated propose/correct/validate/supersede/tombstone daemon+CLI operations (never root tools); `memory.search` gains an explicit entity-backed mode joining claims with the existing filesystem scopes.
Evidence: lifecycle transition tests incl. rejected/expired paths; compaction-survival smoke; receipt.

### P5-019 — signed handoffs

Entry state: `soleaux handoff create` exists but the signature is caller-supplied and never verified; `artifact_ids` is always empty; no accept/import side.
Acceptance: daemon-held signing keys (keystore) with canonicalized manifest hashing; the ten manifest fields from `TASKS.md` including captured git branch/commit and dirty patch; vault-stored artifacts referenced by id; an accept/import operation creating a destination-native session and recording canonical lineage — never a false native resume.
Evidence: sign → tamper → verify-fails test; round-trip handoff between two platforms via the P5-014 host; receipt.

### P5-020 — durable run/subagent orchestration

Entry state: leases with exact-result replay (P4-012) and delegable capability grants (P4-013, the attenuation primitive) are complete; nothing constructs `RunPayload`/`SubagentPayload`/`ApprovalPayload`; the vault is unwired (fixed by P5-W1).
Acceptance: an orchestrator constructing runs/subagents/approvals joined to operation leases via `operation_key`; git worktree leases; enforced budgets; approval gating through `PolicyEngine` with attenuated child grants; cancellation propagation; recovery via `recover_expired_operations`; aggregation.
Evidence: crash-recovery orchestration smoke; budget-exceeded and cancellation tests; receipt.

### P5-021 / P5-022 — parser depth

P5-021 entry state: Oxc runs only for diagnostics (`program` is a stub; structure comes from tree-sitter); no query packs, injections, or damaged-file corpus.
P5-021 acceptance: real Oxc extraction (symbols, imports/exports, module graph, JSX/components/props/hooks, exact source ranges); tree-sitter query packs + injections + incremental damaged-file corpus; writes remain source-range patches, never whole-file AST regeneration.
P5-022 entry state: zero LibCST and zero `mvdan.cc/sh` references exist.
P5-022 acceptance: LibCST formatting-preserving Python writes; the fourteen-item shell list from the gap audit §6 (mvdan semantics, optional ShellCheck, provenance, pipeline/redirection/substitution semantics, effect classification, approval preview, sandboxing, process-tree capture, limits, redaction, reconciliation, diagnostics, audit receipts); GPL parsers stay out of core.
Evidence: corpus-based extraction tests; shell-policy fail-closed tests; receipts.

### P5-023 — LSP subsystem depth

Acceptance — the ten-item list from the gap audit §6: real-server matrices, multi-root/versioned documents, push and pull diagnostics, cancellation, workspace edits, completion events, request-parameter-aware cache keys, crash-loop quarantine, enforced RSS/CPU/concurrency/idle limits (currently declared-only), reconnect/recovery.
Evidence: per-feature conformance against live servers; then P5-009b closes the matrix.

### P5-024 / P5-025 — build-tool integration

P5-024: Turbo static graph plus version-probed documented CLI (`ls`, dry run, boundaries, affected) gated on the P5-010 matrix; optional Turbo LSP only after a compatibility probe.
P5-025: Next route/action/boundary extraction moves to real Oxc (needs P5-021); capability-driven DevTools `init` → `nextjs_index` → advertised calls using the already-registered `next-devtools-mcp` gateway backend as the discovery seed; multi-app merge with cross-app route reconciliation.
Evidence: matrix-versioned probe runs; receipts.

### P5-026 / P5-027 / P5-028 — extensibility surface

P5-026: versioned provider traits for the six families (parsers, workspace graphs, routes, context sources, materializers, gateway backends) abstracted from the working implementations; no trait exists today beyond `CanonicalPayload` and `KeyStore`.
P5-027: stable Rust API; **generated** Python and TypeScript SDKs that are daemon clients only (never a second product mode — GAP-016 bans a production Node daemon and `AGENTS.md` bans a client-visible Python mode); `soleaux ci` (today two lines) becomes deterministic and non-interactive with a machine-readable report and exit-code contract.
P5-028: editor-extension MVP over the typed daemon API (no alternate engine); capability-gated, redaction-passed webhook/SIEM export sourced from the hash-chained audit log.
Evidence: SDK conformance tests generated from the same schemas; deterministic `soleaux ci` double-run byte-identical report; receipts.

### P5-029 — optional hybrid search

Optional (`GAP-013` `open_optional`); enters only after core correctness.
Acceptance: pinned model/license/hash; sensitivity exclusions from entity labels; lexical arm on the existing FTS5, graph arm on the governance graph; migrations, rebuild, corruption recovery.

## Phases 6–8 (grouped; expand to per-task sections when Phase 5 closes)

**Phase 6** (P6-001..016; entry: Phase 5 receipt): Tauri/React desktop with the daemon as sole state owner, one Expo/React Native mobile client (parsers stay server-side; parallel SwiftUI/Compose prohibited), the eighteen-item UX surface and eleven-item remote-control security list from the gap audit §9, installers/updates/rollback, device+desktop E2E evidence.
Seed note: `telemetry/dashboard` (Next 16 static-export mode) is the plausible Tauri shell seed but is currently unwired to `native/`; `native/Cargo.toml` already excludes the not-yet-existing `apps/desktop/src-tauri`.
External inputs: Apple/Google push credentials for P6-014.

**Phase 7** (P7-001..013; entry: Phase 6 receipt): internal hardening (benchmarks, fuzzing, security suite, OS/arch parity incl. case-insensitive paths and symlink cycles, scale tests, incident exercises, relay hardening) plus the externally-gated tasks — P7-004 pen test, P7-005 legal/privacy/license, P7-006 a11y/i18n audit, P7-013 enterprise SSO.
Prerequisite **G0**: an external-evidence receipt schema (vendor identity, engagement scope, report SHA-256, retention location, severity counts, remediation status, assessed commit) — no format exists today — and a written definition replacing the undefined "Stage-17-style" term in P7-010.
No numeric performance/SLO/rollout thresholds exist anywhere yet; P7-001 and P7-012 must propose them for review before measuring.

**Phase 8** (P8-001..008; entry: Phase 7 receipt): RC freeze, signing/notarization (Apple Developer Program + Windows certificate — owner-provided), TestFlight/Play rollout (store accounts — owner-provided), design-partner then public staged release, the explicit P8-006 `productionClaimAllowed` decision (additionally gated by Phase 3 for any efficacy claim), release documentation, GA `1.0.0`.

## Deferred Phase 3 lane (claims only; fully parallel)

Blocks no implementation phase; blocks quantified efficacy claims and feeds the P8-006 review.
Before the first live call: fix `docs/experiments/phase3/MEASUREMENT-SCHEMA.json` — its `arm` enum still lists two arms while the frozen design has three (`control_no_soleaux`, `historical_python`, `native_treatment`), so control-arm records cannot validate; unpin the checker's phase3 status assertion in the same reviewed change; owner reactivation; recorded model/client/sampling/budgets; credentials; model-free oracle dry-run.
Scale: 3 tasks × 3 arms, 9 runs minimum / 27 preferred, against `jmclaughlin724/anilize` at the frozen commit.
Vacated IDs P3-002/003/004/019 are recorded as superseded by the P3-005 tombstone task.

## Appendix — wave-0 drift backlog (landed by the PR introducing this plan)

1. GAP-007 repaired: `closedTasks` P5-002..P5-005, P5-006 mapped, status advanced; GAP-017 added for P5-011/012/013; P5-W1/P5-V1 registered in GAP-004/GAP-007; `reviewedSources` extended through PRs #38/#40/#41.
2. Superseded branch-report pointers fixed in [`BRANCH-AND-RELEASE-POLICY.md`](../operations/BRANCH-AND-RELEASE-POLICY.md), `TASKS.md` (BR-007 evidence), and [`docs/README.md`](../README.md).
3. [`ROLLOUT-PLAN.md`](../release/ROLLOUT-PLAN.md) alpha entry corrected: the Phase 4 development alpha ships under Phase 4 gates; Phase 3 gates claims, and the design-partner alpha requires the approval definition above.
4. `scripts/check_documentation_consistency.py` upgraded from hardcoded next-task literals to three-way validation: first unchecked Phase 5 task in `TASKS.md` == `PROJECT-STATUS.json` phase-5 `currentTask` == this plan's `nextOpen`.
Outstanding (tracked, not landed here): G0 schema + P7-010 definition; the P3 measurement-schema fix (task-owned).
