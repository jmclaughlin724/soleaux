# Soleaux Transcript-to-Repository Gap Audit

**Date:** 2026-08-04  
**Status:** binding planning and completion input for the unified native product  
**Repository reviewed:** `jmclaughlin724/soleaux`  
**Current product version:** `0.4.0-dev.5`  
**Production claim:** prohibited (`productionClaimAllowed=false`)

## 1. Scope and authority

This audit reconciles:

1. the complete system-design transcript;
2. the complete parser/intelligence and repository-consolidation transcript;
3. current `main` through merged PR #10;
4. Phase 0–2 exact receipts and the independently verified Phase 2 artifact;
5. merged PRs #4–#10 and their review evidence;
6. the normal Rust source now checked in under `native/`.

The transcripts contain research, requirements, exploratory proposals, interim package claims, and later corrections. A transcript statement that something was built is not accepted unless current source or exact independently verified evidence supports it. A requirement is retained only when it remains consistent with the locked product definition and later corrective decisions.

Authority order:

```text
locked JSON contracts
→ exact workflow receipts
→ independent verification
→ PROJECT-STATUS.json
→ ROADMAP.md / TASKS.md / current phase package
→ public copy
→ historical transcripts
```

## 2. Product definition retained

Soleaux is **unified repository intelligence**, not an agent operating system and not a replacement for Claude Code, Claude Desktop, Codex, OpenCode, Cursor, or an IDE.

The finished product must provide:

- `soleaux serve .` as one lean repository-scoped MCP attachment;
- exactly twelve public root slots, with optional providers replacing rather than appending;
- one governed catalog for rules, skills, agents, ownership, registered backends, and generated native projections;
- bounded `soleaux.context/v2` output with provenance, trust, owners, consumers, constraints, conflicts, validation routes, and explicit gaps;
- native Oxc, Tree-sitter, PostgreSQL, shell, LSP, Turborepo, and Next.js intelligence when selected;
- canonical sessions, history, memory, handoffs, runs, subagents, approvals, artifacts, policy, and audit behind the public MCP surface;
- CLI, service, desktop, mobile, SDK, editor, and automation access to the same daemon capabilities;
- supported platform adapters without direct writes to vendor internal databases.

The twelve-tool ceiling is a model-context constraint, not permission to omit the broader product.

## 3. Repository state actually proven

### Completed consolidation milestones

- PR #4 established the unified documentation and claims system.
- PR #5 merged the complete receipt-bearing lineage to `main` with preserved ancestry.
- PR #3 was closed as superseded.
- receipt and archive tags were created before historical phase branches were removed.
- PR #6 materialized the independently verified Rust workspace as normal source under `native/`, restored direct-checkout native CI, and retired source carriers.
- PR #7 fixed LSP capability truthfulness for `inspect` and `navigate`, including regression tests under real advertised-capability combinations.
- PR #8 added the telemetry daemon bearer/CORS/redaction baseline.
- PR #10 added a same-origin static dashboard export served by the daemon; PR #9 was correctly closed as an unsafe worktree-gitlink version of the same change.

### Proven native foundations

Phases 0–2 and the subsequent checked-in source prove:

- Rust `soleaux` and `soleauxd` binaries;
- exact canonical and substituted twelve-tool profiles;
- Context Packet V2 validation;
- SQLite WAL structural-index and canonical-event foundations;
- Oxc parse validation, Tree-sitter structural/incremental foundations, `pg_query`, and basic shell extraction;
- LSP live/cached/pending semantics with an 800 ms soft deadline;
- stdio and authenticated loopback Streamable HTTP;
- hash-bound single-file preview/edit foundations;
- static Turborepo and Next.js providers;
- namespaced gateway, catalog domains, adopt/attach, and governance materialization;
- direct native CI from the checked-in workspace;
- a separately secured telemetry API/dashboard foundation.

These are meaningful implemented foundations. They do not prove the complete product below.

## 4. Full-product gaps

Current source does not yet prove:

- complete canonical session/turn/message/content/native-mapping storage and history service;
- memory proposal, validation, conflict, supersession, expiry, tombstone, retention, import/export, and compaction-survival lifecycle;
- signed cross-platform handoffs with Git/code state and destination-native session creation;
- durable run, command, approval, subagent, lease, budget, recovery, cancellation, worktree-isolation, and aggregation services;
- encrypted content-addressed artifact vault, OS-keychain wrapping, per-workspace keys, rotation, and complete policy/capability service;
- complete live Claude Code, Claude Desktop, Codex, and OpenCode adapters;
- complete rules/skills/agents compatibility compiler and native load verification;
- full Oxc/Tree-sitter/LSP/shell/Turbo/Next production depth and real compatibility matrices;
- stable provider interfaces, native-daemon SDKs, deterministic `soleaux ci`, editor integration, and event export;
- complete Tauri desktop and one Expo/React Native mobile product;
- secure pairing, LAN, E2E relay, push, revoke, remote expiry, and offline replication rules;
- complete install, service, upgrade, backup, restore, repair, rollback, and uninstall lifecycle;
- measured performance/scale, fuzzing, cross-workspace and prompt-injection assurance, external security/privacy/license/accessibility review, OS parity, signed artifacts, stores, or staged release.

All retained gaps are mapped to `TASKS.md` and `docs/audits/TRANSCRIPT-GAP-REGISTRY.json`.

## 5. Phase 4 correctness blockers

PR #6 review identified concrete defects in the now-normal native source. They are release-blocking even before broader feature work:

1. **Request-schema enforcement:** reject missing required, unknown, and out-of-range tool arguments before dispatch.
2. **Fresh index/context:** external edits and post-edit state must invalidate or revalidate indexed hashes before search/context claims freshness.
3. **Transactional edits:** a filesystem write, index update, database mutation, preview state, and audit receipt must not diverge; rollback or durable reconciliation is mandatory.
4. **Idempotency reservation:** reserve operation keys atomically before side effects so concurrent duplicates cannot both execute.
5. **Comprehensive redaction:** detect provider token prefixes, authorization headers, PEM/private keys, environment assignments, structured secrets, and log/output secrets independent of variable names.
6. **Transactional provisioning:** render and validate every adopt/apply/revert action before mutation and roll back all prior writes on failure.
7. **Honest coverage:** bounded search/memory/index operations must report typed gaps, truncation, and continuation state instead of false completeness.
8. **SQL semantics:** invalid PostgreSQL must return the declared typed validation result rather than a generic operational error.
9. **LSP capability advertisement:** completed by PR #7; keep its tests as permanent regression coverage.
10. **Preview concurrency and binding:** atomically claim one-time previews and bind workspace, path, preimage/source revision, expiry, formatting/diagnostic plan, and consumption receipt.

The exact task owners are `P4-017` through `P4-026`; `P4-025` is closed by PR #7.

Additional review findings remain tracked within the appropriate tasks: LSP cache keys must include normalized request parameters; gateway HTTP backends cannot report available while invocation is unimplemented; bounded memory/search coverage must remain truthful; and repository refresh should use incremental CST paths rather than unconditional full walks.

## 6. Intelligence implementation still required

### JavaScript, TypeScript, React, and Next.js

Oxc must own full symbols, imports/exports, module graph, JSX/components/props/hooks, Next routes, route handlers, server actions, client/server boundaries, and exact source ranges. Tree-sitter remains the incremental, damaged-buffer, query, and language-injection substrate. User writes remain source-range patches followed by formatter, diagnostics, and reindex—not arbitrary whole-file AST regeneration.

### Python

Tree-sitter and BasedPyright own structural and semantic reads. LibCST is required for formatting-preserving writes. Ruff is used where selected by the repository.

### PostgreSQL

`pg_query`/libpg_query remains the permissively licensed core. Complete validate, fingerprint, normalize, relations/columns/operations, version, error, and real-corpus behavior. GPL `pglast` is not bundled core.

### Shell

Tree-sitter Bash is insufficient for execution policy. Add `mvdan.cc/sh` or another approved permissive semantic parser, optional ShellCheck, executable/argument provenance, pipeline/redirection/substitution semantics, effect classification, approval preview, sandboxing, process-tree capture, resource/output limits, redaction, changed-file reconciliation, diagnostics, and audit receipts.

### LSP

PR #7 fixes truthful capability advertisement, but the complete subsystem still needs real-server matrices, multi-root/versioned documents, push/pull diagnostics, cancellation, workspace edits, completion events, request-parameter-aware caches, crash-loop quarantine, RSS/CPU/concurrency/idle limits, and reconnect/recovery behavior.

### Turborepo and Next.js

Turbo correctness must use static workspaces plus version-probed documented CLI (`ls`, dry run, boundaries, affected); optional LSP only after compatibility probing. Next must combine Oxc static routes/actions/boundaries with capability-driven DevTools `init` → `nextjs_index` → advertised calls, preserving source/confidence and supporting multi-app repositories.

## 7. Canonical data, adapters, orchestration, and materializers

Required canonical entities include platform accounts, native mappings, sessions, turns, messages, content parts, memory claims, rules, skills, agents, handoffs, runs, subagents, approvals, conflicts, materializations, artifacts, adapter cursors, audit records, tombstones, and retention policies.

Adapters must use supported interfaces:

- Claude Code Agent SDK/CLI, external SessionStore, hooks, permission events, compaction/subagent events, and restart reconciliation;
- Claude Desktop local MCP/connector and user-authorized export/import only; unrestricted hosted CRUD remains a non-goal;
- Codex schema-generated app-server client with approvals, steering, compaction, archive, cursors, reconnect, and unknown-version safe mode;
- OpenCode generated OpenAPI client, persistent SSE cursor/reconciliation, plugin events, permissions, and session lifecycle.

Cross-platform continuation is a signed handoff that creates a new target-native session and records canonical lineage. It is never represented as a false native resume.

Rules, skills, and agents require compatibility/degradation reports, guidance-versus-enforcement distinction, diff, backup, atomic apply, rollback, object/revision/origin/idempotency metadata, echo prevention, and native load verification.

## 8. Capability absorption and extensibility

Capabilities named in the transcripts but not present as root slots must remain implemented behind existing slot modes, MCP resources, namespaced gateway operations, daemon APIs, CLI, desktop/mobile operations, hooks/plugins, or generated native files. The binding mapping is `docs/architecture/CAPABILITY-ABSORPTION-MAP.md`.

Still required:

- versioned parser, workspace-graph, route, context-source, materializer, and gateway provider interfaces;
- stable Rust API and generated Python/TypeScript SDKs that call the native daemon;
- deterministic non-interactive `soleaux ci`;
- editor-extension MVP;
- capability-gated redacted webhook/SIEM export;
- optional licensed/checksummed hybrid lexical/vector/graph search only after core correctness and sensitivity controls.

## 9. Desktop, mobile, remote, and operational product

The GUI is part of the requested product. The desktop must be Tauri/React and control the per-user daemon. The mobile product must be one Expo/React Native client using the same typed daemon/remote API; parsers and LSPs remain server-side.

First-class UX must include onboarding, repository trust, indexing progress/cancel/partial availability, workspaces, sessions/transcripts/lineage, Context Inspector, memory/catalog lifecycle, live runs/subagents, approvals/conflicts, intelligence health, Turbo/Next views, devices, backups, updates, diagnostics, repair, and uninstall.

Remote control requires short-lived pairing, device certificates, hardware-backed keys, direct LAN, E2E relay fallback, replay-safe signed commands, capability/risk tiers, biometric step-up, opaque push notifications, revocation, audit, and expiry. Offline mobile library remains optional until encrypted replication, tombstones, quotas, key rotation, and conflict rules pass.

## 10. Phase 3 reconciliation

Phase 3 remains deferred and does not block implementation. Before any quantified efficacy claim, replace the conflicting synthetic GitHub Models design with a frozen three-arm same-model/same-task experiment:

```text
control_no_soleaux
historical_python
native_treatment
```

The market-value gate compares native treatment to the no-Soleaux control. The compatibility gate compares native treatment to the historical Python lineage. Context savings cannot compensate for lower correctness. Retain every attempt and failure and require exact receipts plus independent verification.

## 11. Current finish line

Immediate sequence after this audit merges:

1. close `P4-017` through `P4-024` and `P4-026` with focused regression tests and the full native gate;
2. implement `P4-010` through `P4-015`: canonical data, durability/recovery, operation reservation/leases, artifact/policy foundation, exact CLI, per-user service, and typed local IPC;
3. complete alpha packaging, install/doctor/service/backup/repair/uninstall smoke, Phase 4 receipt, and independent artifact verification;
4. execute expanded Phase 5 adapter/lifecycle/intelligence/materializer/extensibility work;
5. build Phase 6 desktop/mobile/remote/installers;
6. complete Phase 7 assurance and Phase 8 signed staged release.

This audit does not claim missing capabilities are implemented. It makes every retained requirement explicit, owned, testable, and release-blocking.
