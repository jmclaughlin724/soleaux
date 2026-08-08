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

### Phase 5 — foundations wave closed: P5-008, P5-010, P5-021; P5-009/P5-V1 milestones — 2026-08-07

- Merged PR #51 (P5-009 milestone A): the seventeen-family language-server table with truthful degradation and the `lsp-capability-matrix-v1` contract; the P5-009 checkbox closes at matrix v2 after P5-023.
- Merged PR #52 (P5-010): the `turbo-next-matrix-v1` contract with repository-evidence-only version pins, the section-aware pnpm workspace parser with the regression falsified in both directions, and real-repository self-validation. Evidence: [`P5-010-CLOSURE-RECEIPT.json`](P5-010-CLOSURE-RECEIPT.json).
- Merged PR #53 (P5-008): the materializer compatibility compiler with per-platform compatibility/degradation reports, bounded diffs, preimage backups, atomic apply, fail-closed rollback, echo prevention, and native load verification. Evidence: [`P5-008-CLOSURE-RECEIPT.json`](P5-008-CLOSURE-RECEIPT.json).
- Merged PR #54 (P5-V1 verifier milestone): daemon-trusted admission receipts with keyed-BLAKE3 MACs, fail-closed keystore handling, and the state-layer admission marker; external ReadWrite binds now require a verified receipt. The separately reviewed per-client lifecycle oracle lands with adapter enablement, so the P5-V1 checkbox stays open and `mutationEligible` stays empty.
- Merged PR #55 (P5-021): real Oxc 0.142.0 AST extraction with typed import/export envelope fields, five embedded tree-sitter query packs with ts/tsx tagged-template injections, the damaged-file corpus, and the Ruff exclusion for deliberately damaged fixtures. Evidence: [`P5-021-CLOSURE-RECEIPT.json`](P5-021-CLOSURE-RECEIPT.json).
- Closed P5-011: attached and validated `jmclaughlin724/anilize` at `2b7a0fab88dbc202f75b5e443725c825f7dc4fa2` end-to-end — canonical-registry attach with fail-closed negative path, doctor at exactly twelve tools, index, `context.compile` with truthful truncation, `get_symbols`, and complete-coverage LSP-backed `navigate`. Evidence: [`docs/operations/P5-011-ANILIZE-VALIDATION.json`](docs/operations/P5-011-ANILIZE-VALIDATION.json).
- Recorded honestly: PR #50 was merged while its python check was red — a sequencing error; the failure was subsequently adjudicated a pre-existing process-test flake by the identical tree passing the full suite locally twice and main run `31227451859` concluding with `python=success`.

### Phase 5 — adapter and depth wave closed: P5-016, P5-017, P5-018, P5-024, P5-025 — 2026-08-08

- Merged PR #57 (P5-018): the memory lifecycle state machine with transition guards at every canonical write, capability-gated propose/correct/validate/supersede/tombstone over daemon IPC and CLI, the conflict writer, idempotent export/import, compaction survival, and the entity-backed `memory.search` canonicalClaims section riding the locked scopes. Evidence: [`P5-018-CLOSURE-RECEIPT.json`](P5-018-CLOSURE-RECEIPT.json).
- Merged PR #58 (P5-024): the version-probed documented Turbo CLI (ls, dry-run, boundaries, affected), read-only and offline, degrading as data with the matrix digest bound per run; Turbo LSP omitted with recorded reason. Evidence: [`P5-024-CLOSURE-RECEIPT.json`](P5-024-CLOSURE-RECEIPT.json).
- Merged PR #59 (P5-017): the OpenCode adapter — vendored OpenAPI 3.1 spec pinned to 1.18.14, typed client, persistent SSE cursor reconciliation over AdapterCursor, safe-mode posture. Evidence: [`P5-017-CLOSURE-RECEIPT.json`](P5-017-CLOSURE-RECEIPT.json).
- Merged PR #61 (P5-016): the Codex adapter — all 39 JSON Schemas vendored at tag rust-v0.146.1 with a digest manifest, hand-derived schema-validated wire types, stdio JSON-RPC client with thread/turn/approval lifecycle and durable cursors, deterministic local safe-mode refusals, permanent downgrade on foreign CLI versions. Evidence: [`P5-016-CLOSURE-RECEIPT.json`](P5-016-CLOSURE-RECEIPT.json).
- Merged PR #62 (P5-023): LSP subsystem depth — versioned documents, push and pull diagnostics, cancellation, per-document workspace-edit batches with resource-operation refusal, parameter-aware cache keys, crash-loop quarantine, enforced RSS/CPU/concurrency/idle limits (CPU measured over counter-resolvable windows after whole-second `ps` granularity starved the strike counter on CI), and reconnect/recovery. Evidence: [`P5-023-CLOSURE-RECEIPT.json`](P5-023-CLOSURE-RECEIPT.json).
- Merged PR #63 (P5-025): real Oxc route/action/boundary extraction with exact spans replacing string matching (legacy detectors retained as regression oracles), matrix version gates, cross-app route reconciliation, and spawn-free capability-driven DevTools probing with explicit gateway invocation only. Evidence: [`P5-025-CLOSURE-RECEIPT.json`](P5-025-CLOSURE-RECEIPT.json).
- Recorded honestly: the P5-016, P5-017, and P5-018 authoring agents' sessions were killed by host restarts; the integrator verified full gates on the final trees, finished the remaining work (workspace registration, safe-mode refusal ordering, final lint passes), and published — noted in each pull request.

### Phase 5 — activated

- Activated P5-001 through P5-029 for live client matrices, canonical lifecycle, materializers, memory/handoffs/runs, intelligence depth, SDK/provider interfaces, editor integration, optional hybrid search, real repositories, and an exact beta receipt.
- Phase 3 remains deferred as the required three-arm efficacy-claims gate.

### Prior history

See Git history and phase receipts for Phase 0–3 and earlier Phase 4 execution details.
