# Soleaux Capability Absorption Map

**Purpose:** preserve the full requested feature set without expanding the locked twelve-slot model-facing MCP catalog.

## Rules

- The public root catalog remains exactly twelve slots.
- A capability may be exposed through a root-tool mode, MCP resource, namespaced gateway, daemon API, CLI, desktop, mobile, hook, plugin, or generated native file.
- “Not a root tool” does not mean “not part of Soleaux.”
- All write or execution capabilities require policy and audit.

## Structural and semantic capabilities

| Requested capability/tool | Unified exposure |
|---|---|
| `get_file_skeleton` | `get_symbols` with `view=skeleton` |
| `get_symbols` / `document_symbols` | `get_symbols` |
| `get_node_source` | `get_symbols` with exact range/body request, or `navigate` |
| `run_treesitter_query` | `code.search` with `mode=structural_query` |
| `goto_definition` | `navigate` operation |
| `find_references` | `navigate` operation |
| `hover` / implementation / call hierarchy | `navigate` operation |
| diagnostics / completion / signature / code actions | `inspect` operations |
| code-action/rename/format/structural rewrite preview | `preview` operations |
| confirmed patch application | `edit` |
| language-server restart | `restart_lsp` |

## Framework and language providers

| Requested capability/tool | Unified exposure |
|---|---|
| SQL parse/validate | optional `parse_and_validate_postgres_sql` slot |
| `fingerprint_sql` | PostgreSQL provider operation plus CLI/API |
| `extract_sql_relations` | PostgreSQL provider operation plus registry evidence |
| Bash command/redirection extraction | `inspect`/`code.search` operation plus shell CLI/API |
| shell execution | capability-gated daemon run service; never a new root tool |
| `turborepo.packages` | optional slot |
| `turborepo.tasks` | mode/resource/CLI behind Turbo provider |
| `turborepo.boundaries_for_path` | mode/resource/CLI behind Turbo provider |
| `turborepo.affected` | mode/resource/CLI behind Turbo provider |
| `next.get_routes` | optional slot |
| `next.get_route_detail` | mode/resource/CLI behind Next provider |
| `next.list_server_actions` | mode/resource/CLI behind Next provider |
| `next.analyze_boundary` | mode/resource/CLI behind Next provider |
| proxied Next DevTools operations | namespaced capability-driven gateway, never automatic root inflation |

## Context, history, memory, and sessions

| Requested capability/tool | Unified exposure |
|---|---|
| `context.compile` | root tool |
| `context.explain` | `context.compile` explanation and selection-policy fields |
| `history.search` | session/history daemon service, resource, CLI/app; optionally a `memory.search` scope |
| `session.read` | session resource and daemon/CLI/app API |
| `session.handoff` | capability-gated handoff service and CLI/app action |
| `memory.search` | root tool |
| `memory.propose` / `memory.correct` | capability-gated daemon/CLI/app API |
| memory validation/supersession/tombstone | memory lifecycle service and review UI |

## Catalog, agents, and materials

| Requested capability/tool | Unified exposure |
|---|---|
| `rules.resolve` | `registry.read`, context compile, materializer API |
| `skills.list` | `registry.list` / `registry.read` |
| `skills.load` | MCP resource/gateway/daemon API |
| `agents.list` | `registry.list` / `registry.read` |
| `agents.invoke` | run/orchestration service with capability attenuation |
| `artifacts.read` | content-addressed artifact resource/API |
| `provenance.explain` | evidence/provenance resource and envelope fields |
| gateway MCP tools | namespaced gateway only |

## Administration and operations

| Requested capability/tool | Unified exposure |
|---|---|
| `cache_stats` | `repo_info`, health resource, `soleaux cache stats` |
| `repo_info` | root tool |
| `doctor_snippet` | `soleaux doctor --json`, diagnostics API/resource |
| install/service/update/backup/uninstall | CLI/app/daemon admin API |
| runs, approvals, devices, remote control | control-plane protocol, never model-facing root tools |
| webhooks/SIEM | event-export API/plugin |

## Required implementation consequence

Every row above needs a versioned daemon/API schema, tests, policy classification, provenance, size limits, and UI/CLI owner where applicable. The twelve-tool contract is not an excuse to omit these capabilities.
