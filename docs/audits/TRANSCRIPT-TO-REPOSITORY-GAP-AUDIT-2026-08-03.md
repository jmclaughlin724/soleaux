# Soleaux Transcript-to-Repository Gap Audit

**Reviewed:** both complete 2026-08-03 transcripts, current `main`, Phase 0–2 receipts, independently verified Phase 2 source artifact, PR #6 native-source consolidation, and active PR #7 LSP capability fix.  
**Product:** Soleaux `0.4.0-dev.5`  
**Production claim:** prohibited (`productionClaimAllowed=false`).

## 1. Method and authority

The transcripts contain research, user requirements, exploratory proposals, interim package claims, and later corrections. They are not uniformly authoritative. This audit applies the repository authority order:

```text
locked contracts
→ exact receipts
→ independent verification
→ PROJECT-STATUS.json
→ roadmap/tasks/current phase package
→ public copy
→ historical transcripts
```

A transcript claim that something was “built” is not accepted unless current source or an exact independently verified artifact supports it. A transcript requirement that survives the locked product corrections is added to the gap registry and executable task list.

## 2. Product definition retained

Soleaux is **unified repository intelligence**, not an agent operating system and not a replacement for Claude Code, Claude Desktop, Codex, OpenCode, Cursor, or the IDE.

The finished product must provide:

- `soleaux serve .` as one lean, repository-scoped MCP attach path;
- exactly twelve public root slots, with optional providers replacing rather than appending;
- one governed catalog of rules, skills, agents, ownership, registered MCP backends, and materialized native projections;
- bounded `soleaux.context/v2` output with provenance, trust, owners, consumers, constraints, conflicts, validation routes, and explicit gaps;
- native Oxc, Tree-sitter, PostgreSQL, shell, LSP, Turborepo, and Next.js intelligence when selected;
- canonical memory, sessions, handoffs, runs, subagents, approvals, artifacts, policy, and audit behind the public MCP surface;
- CLI, service, desktop, mobile, editor, SDK, and automation access to the same daemon capabilities;
- supported adapters without direct writes to vendor internal databases.

## 3. What current source and receipts prove

Phases 0–2 prove:

- Rust `soleaux` and `soleauxd` binaries;
- exact canonical and substituted twelve-tool profiles;
- Context Packet V2 schema conformance;
- SQLite WAL structural index and canonical event append foundation;
- Oxc parse validation, Tree-sitter structural/incremental foundation, `pg_query`, and basic shell extraction;
- LSP live/cached/pending foundation with an 800 ms soft deadline;
- stdio and authenticated loopback Streamable HTTP;
- hash-bound single-file preview/edit foundation;
- static Turborepo and Next.js providers;
- namespaced gateway, catalog domains, adopt/attach, and governance materialization.

PR #5 consolidated the receipt-bearing lineage to `main`. PR #6 placed the verified Rust workspace in normal source under `native/` and added direct-checkout native CI. These are real completed consolidation milestones.

## 4. What is not yet the full product

The current normal Rust tree does not yet prove the complete requested system:

- canonical session/turn/message/native-mapping storage and history service;
- full memory proposal/validation/supersession/tombstone lifecycle;
- signed cross-platform handoff with Git/code state and destination-native session creation;
- durable run, command, approval, subagent, lease, recovery, and cancellation services;
- production artifact vault, OS-keychain wrapping, per-workspace keys, and complete policy/capability service;
- live Claude Code, Claude Desktop, Codex, and OpenCode adapters;
- complete rules/skills/agents compatibility compiler and live load verification;
- complete Oxc/Tree-sitter/LSP/shell/Turbo/Next depth and real compatibility matrices;
- stable provider interfaces, daemon SDKs, non-interactive CI mode, editor extension, and event export;
- Tauri desktop, one Expo mobile app, secure pairing/LAN/relay/push, and full installer lifecycle;
- measured scale/performance, external assurance, OS parity, signing, stores, and staged release.

Those requirements are now mapped to explicit Phase 4–8 tasks rather than being left in transcripts.

## 5. Concrete Phase 4 defects found during source review

PR #6 review exposed correctness issues that must be fixed before alpha, independently of the broader missing feature set:

1. **Closed request schemas:** missing required and unknown tool arguments must fail before handlers run.
2. **Fresh projections:** edits and external file changes must invalidate/reindex before search or context claims freshness.
3. **Transactional edit:** filesystem write and index/database/event update must not diverge; rollback and receipt are mandatory.
4. **Idempotency reservation:** reserve operation keys transactionally before any side effect so concurrent duplicates cannot both execute.
5. **Redaction:** cover common provider token prefixes, PEM/private keys, authorization headers, environment assignments, structured secrets, and logs.
6. **Transactional provisioning:** adopt/apply/revert must restore all prior files when any write or bookkeeping step fails.
7. **Honest coverage:** empty search/memory and partial/unavailable index results must emit typed gaps rather than asserting complete absence.
8. **SQL semantics:** invalid PostgreSQL input must return the declared typed validation result/error semantics.
9. **LSP capability advertisement:** expose methods only after successful initialize confirms support; active PR #7 owns this repair.
10. **Preview concurrency:** preview/apply must explicitly bind workspace, path, preimage hash, source revision, expiration, format/diagnostic plan, and one-time consumption.

Every defect has an executable task (`P4-017` through `P4-026`) and must have a regression test plus full native gate.

## 6. Intelligence gaps and required implementation

### JavaScript/TypeScript and React/Next

Oxc must become the full extraction owner for symbols, imports/exports, module graph, JSX/components, Next routes, server actions, and source ranges. Tree-sitter remains the incremental/damaged-buffer/query substrate. User edits remain source-range patches plus formatter and diagnostics, not arbitrary whole-file AST regeneration.

### Python

Tree-sitter and BasedPyright serve reads/semantics. LibCST is required for formatting-preserving Python writes. Ruff is used where the repository selects it.

### PostgreSQL

`pg_query`/libpg_query remains the permissively licensed core. Validate, fingerprint, and relation extraction need complete modes and real corpus coverage. GPL `pglast` is not a bundled core dependency.

### Shell

Tree-sitter Bash alone is insufficient for execution policy. Integrate `mvdan.cc/sh` or another approved permissive semantic parser, optional ShellCheck, command/effect classification, sandbox, process-tree capture, resource limits, redaction, changed-file reconciliation, and approval receipts.

### LSP

Complete initialize/capability storage, real server matrix, multi-root/versioned documents, push/pull diagnostics, cancellation, workspace edits, cached/pending completion events, crash-loop quarantine, RSS/concurrency/idle limits, and truthful method advertisement.

### Turborepo and Next.js

Turbo correctness must rely on static graph plus version-probed documented CLI (`ls`, dry run, boundaries, affected); optional LSP only after compatibility probe. Next must use Oxc static routes/actions/boundaries and capability-driven DevTools `init` → `nextjs_index` → advertised calls, merging runtime and static evidence for multi-app repos.

## 7. Canonical data, adapters, and orchestration gaps

The transcripts require a canonical event/projection model for platform accounts, native mappings, sessions, turns, messages, content parts, memory, rules/skills/agents, runs/subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones, and retention.

Adapters must use supported interfaces:

- Claude Code Agent SDK/CLI, external SessionStore, hooks, permission events, and restart reconciliation;
- Claude Desktop local connector/MCP and user-authorized export/import only; hosted CRUD remains a non-goal;
- Codex schema-generated app-server with approvals, steering, compaction, archive, cursor/reconnect, and safe mode;
- OpenCode generated OpenAPI client, persistent SSE cursor/reconciliation, plugin events, permissions, and session lifecycle.

Cross-platform continuation is a signed handoff that creates a new target-native session and records canonical lineage. It is never represented as false native resume.

## 8. Catalog, SDK, editor, and automation gaps

The twelve-tool ceiling is not permission to omit capabilities. `history.search`, `session.read/handoff`, memory mutation, SQL fingerprint/relations, Turbo tasks/boundaries/affected, Next route details/actions/boundaries, artifacts/provenance, run/approval/device/backup/update operations must be exposed through modes of existing slots, resources, gateway namespaces, daemon APIs, CLI, desktop, mobile, hooks/plugins, or generated native files as defined in the capability absorption map.

Still required:

- platform compatibility and degradation reports for rules/skills/agents;
- diff/backup/atomic apply/rollback/origin/revision/idempotency/echo guards and load verification;
- versioned parser/workspace/route/context/materializer/gateway provider APIs;
- stable Rust API and generated Python/TypeScript SDKs that call the native daemon;
- `soleaux ci` deterministic non-interactive mode;
- editor extension and capability-gated webhook/SIEM event export.

## 9. Desktop, mobile, and release gaps

The requested GUI is part of the product, not merely future marketing. The desktop must be Tauri/React and control the per-user daemon. The mobile product must be one Expo/React Native client using the same typed daemon/remote API; parsers remain server-side.

Required UX includes onboarding, repository trust and indexing progress, workspaces, sessions/transcripts/lineage, Context Inspector, memory/catalog lifecycle, live runs/subagents, approvals/conflicts, intelligence health, Turbo/Next views, devices, backups, updates, diagnostics, repair, and uninstall.

Remote control requires short-lived pairing, device certificates, hardware-backed keys, direct LAN, E2E relay fallback, replay-safe signed commands, capability/risk tiers, biometrics, opaque push notifications, revocation, audit, and expiry. Offline mobile library is optional until encrypted replication/tombstone/conflict rules pass.

Release still requires measured p50/p95/p99, pathological/large-repo tests, fuzzing, cross-workspace and prompt-injection security tests, external security/privacy/license/accessibility review, OS parity, signed SBOM/provenance, production relay hardening, signed/notarized/store artifacts, staged rollout, and explicit production-claim decision.

## 10. Phase 3 reconciliation

Phase 3 is deferred by owner direction and does not block implementation. Before it is used for any efficacy claim, it must replace the obsolete GitHub Models/synthetic harness with a three-arm same-model/same-task design:

```text
control_no_soleaux
historical_python
native_treatment
```

The market-value gate compares native treatment to no-Soleaux control. The compatibility gate compares native treatment to the historical Python lineage. Context savings cannot compensate for lower correctness. All attempts/failures remain in the dataset, and exact receipts plus independent verification are required.

## 11. Branch state and finish line

Completed:

- PR #4 standardized documentation;
- PR #5 merged the receipt-bearing lineage to `main`;
- PR #3 closed as superseded;
- receipt/archive tags preserved evidence before historical branches were deleted;
- PR #6 materialized native source under `native/` and direct native CI.

Active:

- PR #7 repairs truthful LSP capability advertisement and must be reviewed and merged/closed on evidence.
- this audit PR adds the complete gap registry and finish-line work.

The exact remaining implementation sequence is:

1. close Phase 4 correctness defects and canonical state/durability/security/CLI/service foundation;
2. produce and independently verify the unsigned alpha;
3. implement/live-test adapters, memory/session/handoff/run/subagent/materializer/intelligence/provider/SDK/editor surfaces;
4. build desktop/mobile/remote/installers;
5. complete assurance, parity, signing, staged rollout, and GA.

This audit does not claim the missing capabilities are implemented. It makes them explicit, owned, testable, and release-blocking.
