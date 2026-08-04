# Soleaux Capability Absorption Map

The public MCP catalog is fixed at twelve slots. This does **not** remove the broader capabilities required by the reviewed transcripts. Each capability must have a stable owner behind an existing root slot, MCP resource, namespaced gateway, daemon API, CLI, desktop/mobile operation, hook/plugin, or generated native file.

## Public slots

| Canonical slot | Absorbed capabilities |
|---|---|
| `context.compile` | Context compilation and explanation, owners, consumers, constraints, conflicts, validation routes, supporting facts, requested resources, gaps, trust, provenance, token/byte budgets, secret redaction, target-specific rendering |
| `code.search` | Ranked lexical/structural search, bounded reads, node/source lookup, Tree-sitter queries, call sites, package/route scoping, coverage semantics |
| `memory.search` | Search active/proposed/superseded memory, prior compiled context, session summaries, architectural decisions, procedures, preferences, and team memory under policy |
| `get_symbols` | File skeleton, symbols, signatures, node ranges, imports/exports, components/hooks/actions, document symbols and bounded snippets |
| `registry.list` | Registry domains, catalog objects, gateway namespaces, ownership/governance tables, providers, resources, compatibility and capability summaries |
| `registry.read` | Table batches, ownership records, rules/skills/agents definitions, backend records, materialization records, compatibility/degradation reports, artifacts/session metadata where authorized |
| `repo_info` | Workspace identity, repository/worktree mappings, frameworks, package manager, index health, cache/index/provider status, active profile, capability matrix, doctor/status resource links |
| `navigate` | Definition, references, implementation, hover, call/type hierarchy, semantic navigation with cached/pending LSP semantics |
| `inspect` | Diagnostics, completion, signature help, code actions, semantic tokens/inlay hints where supported, parser/LSP health and exact gaps |
| `preview` | Hash-bound edit/rename/format/code-action/structural-rewrite/materialization/adopt/integration preview, risk, diff, formatter/diagnostic plan and expiry |
| `edit` | Apply one confirmed preview, atomic backup/rollback, reindex, diagnostics, audit, single-file first and transactional multi-file later |
| `restart_lsp` | Restart selected semantic providers; related provider/service restart remains CLI/app/daemon API rather than root-tool inflation |

## Optional substitution slots

| Optional tool | Required modes/resources without additional root tools |
|---|---|
| `parse_and_validate_postgres_sql` | validate, fingerprint, normalize, extract relations/columns/operations, provenance, version and diagnostics |
| `turborepo.packages` | packages, tasks, boundaries for path, affected, search scope, CLI/static evidence and provider health |
| `next.get_routes` | route list/detail, layouts, handlers/methods, server actions, client/server boundaries, runtime diagnostics, static/runtime evidence |

Optional tools replace one declared canonical slot in the selected profile. They never append.

## Capabilities outside `tools/list`

| Conceptual capability from transcripts | Production owner |
|---|---|
| `history.search`, `session.read` | Canonical session/history daemon service, MCP resources, CLI/app APIs; `memory.search` may search indexed session summaries by explicit mode |
| `session.handoff` | Signed handoff daemon operation and CLI/app workflow; source packet may be requested through `context.compile`/registry resources |
| `memory.propose`, `memory.correct`, validate/supersede/tombstone | Capability-gated daemon/CLI/app operations; not ordinary model root tools |
| `rules.resolve` | `registry.read` mode plus target-platform context/materializer service |
| `skills.list/load`, `agents.list/invoke` | `registry.list/read`, namespaced gateway, daemon/CLI/app operations, and native materialized catalogs |
| `artifacts.read`, `provenance.explain` | MCP resources and `registry.read`/`context.compile` modes with capability/sensitivity checks |
| SQL fingerprint/relations | Modes of the PostgreSQL optional slot plus CLI/API |
| Turbo tasks/boundaries/affected | Modes/resources of the Turbo optional slot plus CLI/API |
| Next route detail/actions/boundaries | Modes/resources of the Next optional slot plus CLI/API |
| Runs, subagents, approvals, interrupts, compaction, archive | Durable daemon control API, desktop/mobile operation contract, CLI and native adapter capabilities |
| Gateway backend tools | Namespaced gateway invocation; never copied into local root catalog |
| Rules/skills/agents materialization | Preview/edit or daemon/CLI/app materializer operations with compatibility reports and load verification |
| Backups, restore, repair, uninstall, updates | CLI/desktop/mobile administrative operations with risk and capability gates |
| Device pairing, relay, push, revoke | Remote-control API and desktop/mobile UI; never model root tools |
| `soleaux ci` | Deterministic non-interactive CLI/SDK surface with machine-readable results |
| Editor integration | Editor extension using typed daemon API/MCP resources; no alternate engine/runtime |
| Webhook/SIEM export | Capability-gated, redacted event export service |

## Ownership boundaries

| Object | Authoritative owner |
|---|---|
| Vendor-native transcripts/threads and hidden runtime state | Claude/Codex/OpenCode; Soleaux ingests through supported APIs, exports, hooks/plugins and read-only observation |
| Canonical session graph, memory, handoffs, runs, policy, audit and artifacts | Soleaux daemon |
| Repository structural and semantic intelligence | Soleaux intelligence core and selected native providers |
| Generated `CLAUDE.md`, `AGENTS.md`, rules, skills and agents | Soleaux catalog/materializer, with native files as projections and user edits reconciled explicitly |
| Hosted Claude Desktop chats/memory | Anthropic; unrestricted hosted CRUD remains a non-goal |

## Safety rule

When a transcript capability cannot be represented without exceeding twelve root tools, it must be implemented behind one of the owners above or raised as a reviewed contract conflict. It must never be silently dropped or added as a thirteenth tool.
