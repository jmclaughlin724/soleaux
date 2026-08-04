# Changelog

All notable changes to the unified native Soleaux product are recorded here. Exact phase receipts and independent verification remain the evidence owners.

## [Unreleased] — `0.4.0-dev.5`

### Transcript-to-repository reconciliation — 2026-08-04

- Reviewed both complete design transcripts against current `main`, Phase 0–2 receipts, the independently verified Phase 2 artifact, and merged PRs through #10.
- Added a binding human audit, machine-readable gap registry, and capability absorption map.
- Expanded the executable finish line for canonical sessions/history/memory/handoffs/runs/subagents/approvals/artifacts/policy, live adapters, intelligence depth, materializers, provider/SDK/editor/automation surfaces, desktop/mobile/remote operations, assurance and release.
- Converted Phase 3's future proof requirement to a deferred three-arm design: no-Soleaux control, historical Python baseline and native treatment.
- Added explicit Phase 4 correctness tasks for request-schema validation, index freshness, edit rollback, idempotency reservation, comprehensive redaction, transactional provisioning, truthful coverage, typed SQL errors, LSP capability truthfulness and preview concurrency.
- Recorded `P4-025` closed by PR #7 and made `P4-017` the next implementation task.
- Kept version `0.4.0-dev.5`, public ceiling twelve and `productionClaimAllowed=false`.

### Native source and correctness

- PR #6 materialized the independently verified Rust workspace as normal source under `native/`, restored direct-checkout CI and retired source carriers.
- PR #7 made LSP capability advertisement truthful for `inspect` and `navigate`, added real capability combinations to regression tests and kept semantic-required behavior fail-closed.

### Telemetry and dashboard foundation

- PR #8 added bearer authentication, restricted Origin/CORS handling, secure token-file behavior and process-argument redaction to the telemetry daemon API.
- PR #10 added a Next static dashboard export served same-origin by the daemon and preserved the authenticated API listener. PR #9 was closed as an unsafe worktree-gitlink version of the change.
- Consumer token propagation and full product integration remain open; these foundations do not imply completion of the desktop/mobile product.

### Consolidation — 2026-08-03

- PR #5 consolidated every receipt-bearing lineage into `main` through a merge commit.
- PR #4 established one documentation and claims authority system.
- PR #3 was closed as superseded.
- Receipt/archive tags were created before historical branches were removed.
- Phase 3 was deferred by owner direction as an efficacy-claims gate rather than an implementation blocker.

### Phase 2 — closed

- Added native namespaced MCP gateway registration and CLI-mediated credentials.
- Added native skills, agents, rules, ownership, tables and backend registry domains.
- Added native adopt/attach planning, application, backup, reversal and registration.
- Added governance ownership, constraints and validation-route materialization.
- Preserved exact twelve-tool canonical and optional-substitution profiles.
- Passed exact native gate and independent artifact verification on `6768d9de2aa8a61ba90356409033c0d69b2d5afc`.

### Phase 1 — closed

- Unified the public catalog at exactly twelve ordered tools.
- Implemented `soleaux.context/v2`.
- Added native search, memory, symbols, registry, repo identity, LSP navigation/inspection, hash-bound preview/edit and LSP restart.
- Passed exact native compilation, lint, test, build, audit, MCP and schema gates on `d3eecd45867e82d5777e57753c581483971214dd`.

### Phase 0 — closed

- Locked the unified MCP profile and Context Packet V2.
- Locked version `0.4.0-dev.5`, hard ceiling twelve and `productionClaimAllowed=false`.
- Established native Rust compilation and exact receipt evidence on `a31820d26f46d258175b52fe30fdbecf7b650265`.

### Pending

- Remaining Phase 4 correctness and durable-core work.
- Unsigned alpha package, operational smoke, exact receipt and independent verification.
- Live adapters, canonical lifecycle, intelligence and materializer depth.
- Tauri desktop, Expo mobile, remote control and installers.
- External assurance, signed distribution and staged release.

## Historical Python lineage — `0.1.0` unreleased

The Python/FastMCP lineage supplied typed context, LSP behavior, governance, gateway/OAuth semantics, skills, adopt, editor safety, framework discovery and PostgreSQL analysis. Its history remains in Git and [`docs/history/PYTHON-LINEAGE.md`](docs/history/PYTHON-LINEAGE.md); it is not the current product/version authority.

The project is licensed under MIT.
