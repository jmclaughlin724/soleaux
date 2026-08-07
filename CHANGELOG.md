# Changelog

All notable changes to the unified native Soleaux product are recorded here. Exact phase receipts and independent verification remain the evidence owners.

The project is licensed under MIT.

## [Unreleased] — `0.4.0-dev.5`

### Phase 4 — correct, durable unsigned alpha closed — 2026-08-05

- Closed P4-001 through P4-026 and synchronized all authoritative default-branch documentation.
- Merged strict MCP request-schema validation, watcher-backed freshness, transactional edit rollback, atomic idempotency, comprehensive redaction, transactional provisioning, truthful gaps/continuations, typed PostgreSQL validation, truthful LSP capability advertisement, and atomic preview claims.
- Added canonical accounts, mappings, sessions, turns, messages, content, memory claims, rules, skills, agents, handoffs, runs, subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones, and retention.
- Added serialized persistence, migrations, operation leases, replay, crash recovery, backup, restore, integrity repair, and schema-downgrade refusal.
- Added encrypted content-addressed artifacts, workspace-separated keys, rotation, redaction, and deny-by-default capability policy.
- Added the stable operations CLI, per-user daemon, typed local IPC, same-user peer checks, concurrent clients, and hardened service manifests.
- Fixed quoted TOML gateway backend-key decoding for the repository's `openai-docs` configuration.
- Produced two byte-identical unsigned development-alpha archives with normalized metadata and a path-independent Cargo SBOM.
- Passed clean install, daemon launch/restart, doctor, backup, export, repair, offline restore, and state-preserving uninstall from the extracted archive.
- Persisted `PHASE4-ALPHA-CLOSURE-RECEIPT.json`, `PHASE4-INDEPENDENT-VERIFICATION.json`, and `PHASE4-CLOSURE-RECEIPT.json`.
- Pruned fully merged short-lived branches and retained unique-commit branches only as documented non-authoritative archival lineage.
- Kept the exact twelve-tool surface, locked contract digests, version `0.4.0-dev.5`, and `productionClaimAllowed=false`.

### Phase 5 — P5-001 registry convergence closed — 2026-08-05

- Merged PR #36 with one daemon-owned canonical workspace/client registry across CLI, desktop, editor, and adapter clients.
- Added atomic lease, heartbeat, binding, disconnect, forget, revival, trust-downgrade, compatibility-safe-mode, pagination, and mutation-response bounds.
- Unified public `attach` and `adopt --revert` with the canonical registry, canonical UUID markers, rollback recovery, and legacy manifest-scope compatibility.
- Passed the complete Python conformance suite, full native Rust gates, Linux/macOS lifecycle smokes, and Cargo audit while retaining ceiling 12 and `productionClaimAllowed=false`.
- Persisted [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json).

### Phase 5 — P5-002 through P5-006 client capability matrices closed — 2026-08-07

- Merged PR #38's six-platform client capability matrix (Claude Code, Claude Desktop, Codex, OpenCode, Cursor, and the generic MCP host fixture) with pinned artifact verification and bounded signal-oracled probes.
- Merged PR #40's security and provenance remediation: revision-guarded client revalidation, client-scoped heartbeat bindings, verified Claude native-binary installation, and removal of the temporary PR40 repair automation.
- Kept every external client read-only with an empty mutation-eligible set pending a daemon-trusted receipt verifier.
- Deleted three owner-approved fully merged branches, archived the two never-pushed local lineages as tags, and recorded the inventory in [`docs/operations/BRANCH-CONSOLIDATION-2026-08-07.json`](docs/operations/BRANCH-CONSOLIDATION-2026-08-07.json).
- Persisted [`P5-002-P5-006-CLOSURE-RECEIPT.json`](P5-002-P5-006-CLOSURE-RECEIPT.json).

### Phase 5 — remaining work consolidated into an executable plan — 2026-08-07

- Added [`docs/plans/PHASE5-IMPLEMENTATION-PLAN.md`](docs/plans/PHASE5-IMPLEMENTATION-PLAN.md) and [`docs/plans/PHASE5-DEPENDENCIES.json`](docs/plans/PHASE5-DEPENDENCIES.json): per-task acceptance criteria, verified code entry states, waves, parallel-lane justification, and the machine-readable dependency graph for all remaining phases.
- Registered the investigation-discovered pre-tasks P5-W1 (wire `soleaux-mcp` to `soleaux-state`; first `soleaux-vault` consumer) and P5-V1 (daemon-trusted admission receipt verifier, previously an unowned dependency of every external write path).
- Repaired documentation drift: GAP-007 closure state and P5-006/011/012/013 gap mapping, superseded branch-report pointers, and the rollout-plan alpha entry; defined design-partner approval.
- Upgraded the consistency checker from hardcoded next-task literals to three-way validation across `TASKS.md`, `PROJECT-STATUS.json`, and the dependency graph.
- Added the `load-tasks` project skill and dependency-graph group entries (Phases 6–8, external gates, Phase 3) so any session reconstructs the complete remaining-work tracker from repository state alone.

### Phase 5 — P5-W1 crate wiring closed — 2026-08-07

- Merged PR #44: `soleaux-mcp` gains the `soleaux-state` edge with an attach-only-if-exists canonical-state surface, and the IPC daemon constructs the deny-by-default capability policy engine and the OS keychain vault key-store handle at boot; key material stays on-demand.
- Persisted [`P5-W1-CLOSURE-RECEIPT.json`](P5-W1-CLOSURE-RECEIPT.json); the next open implementation task is P5-007.

### Phase 5 — P5-007 canonical session/history service closed — 2026-08-07

- Merged PRs #46 and #47: the daemon-owned session/history service with a validated active/archived state machine enforced at every canonical write, typed IPC for sessions/turns/messages with bounded pages, race-free turn ordinals via the idempotency unique index, bounded lineage traversal, adapter-idempotent native-identity upserts, the memory.search session-scope canonical section (locked scope enum untouched), the `soleaux://sessions` MCP resources, and attach-only-if-exists canonical-state wiring in serve.
- Persisted [`P5-007-CLOSURE-RECEIPT.json`](P5-007-CLOSURE-RECEIPT.json); the next open implementation task is P5-008.

### Phase 3 prerequisites — measurement schema and checker unpin — 2026-08-07

- Fixed the Phase 3 measurement schema's arm enum from the superseded two-arm design to the frozen three arms (`control_no_soleaux`, `historical_python`, `native_treatment`), so control-arm run records can validate.
- Relaxed the consistency checker's Phase 3 pins to the closed set {deferred, frozen-ready}, making owner reactivation possible without editing the enforcement script; every other value still fails.

### Phase 5 — activated

- Activated P5-001 through P5-029 for live client matrices, canonical lifecycle, materializers, memory/handoffs/runs, intelligence depth, SDK/provider interfaces, editor integration, optional hybrid search, real repositories, and an exact beta receipt.
- Phase 3 remains deferred as the required three-arm efficacy-claims gate.

### Prior history

See Git history and phase receipts for Phase 0–3 and earlier Phase 4 execution details.
