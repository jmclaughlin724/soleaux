# Soleaux Capability Absorption Map

The public MCP catalog is fixed at twelve slots. This does **not** remove the broader capabilities required by the reviewed transcripts. Each capability must have a stable owner behind an existing root slot, MCP resource, namespaced gateway, daemon API, CLI, desktop/mobile operation, hook/plugin, generated SDK, or generated native file.

## Public slots

| Canonical slot | Absorbed capabilities |
|---|---|
| `context.compile` | Context compilation/explanation, owners, consumers, constraints, conflicts, validation routes, supporting facts, requested resources, gaps, trust, provenance, token/byte budgets, redaction, target rendering |
| `code.search` | Ranked lexical/structural search, bounded reads, node/source lookup, Tree-sitter queries, call sites, package/route scoping, coverage and continuation semantics |
| `memory.search` | Search active/proposed/superseded memory, prior compiled context, session summaries, decisions, procedures, preferences, and team memory under policy |
| `get_symbols` | File skeleton, symbols, signatures, node ranges, imports/exports, components/hooks/actions, document symbols, bounded snippets |
| `registry.list` | Domains, catalog objects, gateway namespaces, governance tables, providers, resources, compatibility and capability summaries |
| `registry.read` | Table batches, ownership, rules/skills/agents definitions, backend/materialization records, compatibility/degradation reports, authorized artifact/session metadata |
| `repo_info` | Workspace/repository/worktree identity, frameworks, package manager, index/cache/provider health, active profile, capability matrix, doctor/status resource links |
| `navigate` | Definition, references, implementation, hover, call/type hierarchy, semantic navigation with cached/pending LSP semantics |
| `inspect` | Diagnostics, completion, signature help, code actions, semantic tokens/inlay hints when supported, parser/LSP health and exact gaps |
| `preview` | Hash-bound edit/rename/format/code-action/structural-rewrite/materialization/adopt/integration preview, risk, diff, formatter/diagnostic plan, expiry |
| `edit` | Apply one confirmed preview, atomic backup/rollback, reindex, diagnostics, audit; single-file first and transactional multi-file later |
| `restart_lsp` | Restart selected semantic providers; broader service restart remains CLI/app/daemon API |

## Optional substitution slots

| Optional tool | Required modes/resources without additional root tools |
|---|---|
| `parse_and_validate_postgres_sql` | validate, fingerprint, normalize, extract relations/columns/operations, provenance, version, diagnostics |
| `turborepo.packages` | packages, tasks, boundaries for path, affected, search scope, CLI/static evidence, provider health |
| `next.get_routes` | route list/detail, layouts, handlers/methods, server actions, client/server boundaries, runtime diagnostics, static/runtime evidence |

Optional tools replace one declared canonical slot in a selected profile. They never append.

## Capabilities outside `tools/list`

| Conceptual capability | Production owner |
|---|---|
| `history.search`, `session.read` | Canonical session/history service, MCP resources, CLI/app APIs; `memory.search` may search indexed summaries only through an explicit mode |
| `session.handoff` | Signed handoff daemon operation and CLI/app action; handoff context may be compiled through existing slots/resources |
| `memory.propose`, `memory.correct`, validate/supersede/tombstone | Capability-gated daemon/CLI/app operations |
| `rules.resolve` | `registry.read` mode plus context/materializer service |
| `skills.list/load`, `agents.list/invoke` | `registry.list/read`, namespaced gateway, daemon/CLI/app operations, and native materialized catalogs |
| `artifacts.read`, `provenance.explain` | MCP resources and authorized `registry.read`/`context.compile` modes |
| SQL fingerprint/relations | PostgreSQL optional-slot modes plus CLI/API |
| Turbo tasks/boundaries/affected | Turbo optional-slot modes/resources plus CLI/API |
| Next route detail/actions/boundaries | Next optional-slot modes/resources plus CLI/API |
| Runs, subagents, approvals, interrupt, compaction, archive | Durable daemon control API, desktop/mobile operation contract, CLI, native adapter capabilities |
| Gateway backend tools | Namespaced gateway invocation; never copied into root catalog |
| Rules/skills/agents materialization | Preview/edit or daemon/CLI/app operations with compatibility reports and load verification |
| Backups, restore, repair, uninstall, updates | CLI/desktop/mobile administrative operations with risk/capability gates |
| Device pairing, relay, push, revoke | Remote-control API and desktop/mobile UI |
| `soleaux ci` | Deterministic non-interactive CLI/SDK surface |
| Editor integration | Editor extension using typed daemon API/MCP resources; no alternate engine/runtime |
| Webhook/SIEM export | Capability-gated, redacted event-export service |

## Ownership boundaries

| Object | Authoritative owner |
|---|---|
| Vendor-native transcripts/threads and hidden runtime state | Claude/Codex/OpenCode; Soleaux uses supported APIs, exports, hooks/plugins, and read-only observation |
| Canonical session graph, memory, handoffs, runs, policy, audit, artifacts | Soleaux daemon |
| Repository structural/semantic intelligence | Soleaux intelligence core and selected native providers |
| Generated `CLAUDE.md`, `AGENTS.md`, rules, skills, agents | Soleaux catalog/materializer; native files are projections reconciled explicitly |
| Hosted Claude Desktop chats/memory | Anthropic; unrestricted hosted CRUD remains a non-goal |

## Required implementation consequence

Every absorbed capability requires:

- a versioned schema;
- a daemon/API or resource owner;
- policy and risk classification;
- provenance and sensitivity labels;
- size, time, and continuation limits;
- tests and compatibility evidence;
- an explicit CLI, app, SDK, or materialized-file access path.

When a transcript capability cannot be represented without exceeding twelve root tools, it must be implemented behind an owner above or raised as a reviewed contract conflict. It must never be silently dropped or added as a thirteenth tool.
