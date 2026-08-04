# Changelog

All notable changes to the unified native Soleaux product are recorded here. Phase receipts are the evidence owners; this file is a readable release narrative.

## [Unreleased] — `0.4.0-dev.5`

### Consolidation (2026-08-03)

- Consolidated every lineage into one branch bound for `main` through a single reviewed merge-commit pull request: the native phase lineage, the unified documentation system, the Python-lineage Stage D attach onboarding and Stage E1 machine service registry, the ast-grep 0.45.0 rule catalog, and the Codex hooks-to-rules migration.
- Deferred the Phase 3 live same-model experiment by owner direction; it remains a frozen optional claims-gate and no longer blocks source consolidation, release work, or the merge to `main`. Removed the superseded Grok-era Phase 3 fixture harness, its unpacker, and the executed one-shot phase gate workflows; receipts and annotated tags preserve exact-commit provenance.
- Preserved the independently verified Phase 2 evidence artifact (`8858165328`, SHA-256 `3fa99fa2de889c7eb081e8ff2a913e66cb7c2027a1696f6ad4eb1c0d0b963ebe`) ahead of its 2026-08-17 retention expiry as the Phase 4 materialization source.
- Scoped the local pre-commit gate to the change under commit and removed the full-suite post-commit hook; continuous integration remains the full-suite owner. Retired the local Claude bash-policy guard scripts by owner direction.
- Routed the next-devtools MCP backend through the soleaux gateway in `soleaux.toml`; host configurations register only the single soleaux server.

### Documentation and governance

- Established one authoritative documentation hierarchy for product purpose, status, roadmap, tasks, testing, rollout, experiments, releases, and public claims.
- Replaced root documentation that still described the historical Python/FastMCP `0.1.0` product.
- Added a machine-readable current status and fail-closed documentation consistency gate.
- Added the pre-registered Phase 3 live experiment package.
- Preserved the locked MCP and Context Packet V2 contracts without modification.

### Phase 2 — closed

- Added native namespaced MCP gateway registration.
- Added CLI-mediated credentials stored outside the worktree.
- Added native skills, agents, rules, ownership, tables, and backend registry domains.
- Added native adopt/attach planning, application, backup, reversal, and registration.
- Added governance ownership, constraint, and validation-route materialization.
- Preserved exact 12-tool canonical and optional-substitution profiles.
- Passed the exact native gate and independent artifact verification on source commit `6768d9de2aa8a61ba90356409033c0d69b2d5afc`.

### Phase 1 — closed

- Unified the public catalog at exactly 12 ordered tools.
- Implemented `soleaux.context/v2`.
- Added native code search, memory search, symbols, registry, repository identity, LSP navigation/inspection, hash-bound preview/edit, and LSP restart.
- Passed exact native compilation, lint, test, build, audit, MCP, and schema gates on `d3eecd45867e82d5777e57753c581483971214dd`.

### Phase 0 — closed

- Locked the unified MCP profile and Context Packet V2.
- Locked version `0.4.0-dev.5`, hard ceiling 12, and `productionClaimAllowed=false`.
- Established native Rust compilation and exact receipt evidence on `a31820d26f46d258175b52fe30fdbecf7b650265`.

### Pending

- Phase 3 live same-model / same-task product proof.
- Canonical native source/default-branch consolidation.
- Live client, LSP, framework, and design-partner matrices.
- Desktop/mobile/installers.
- External assurance and signed distribution.

## Historical Python lineage — `0.1.0` unreleased

The Python/FastMCP lineage supplied important capabilities that were absorbed into the native product: typed context, LSP navigation/inspection, governance, gateway/OAuth behavior, skills, adopt, editor safety, framework discovery, and PostgreSQL analysis.

Its complete change history remains available in Git history and is indexed in [`docs/history/PYTHON-LINEAGE.md`](docs/history/PYTHON-LINEAGE.md). It is not the current product/version authority.

The project is licensed under MIT.
