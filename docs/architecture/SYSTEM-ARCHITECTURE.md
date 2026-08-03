# System Architecture

## Production topology

```text
MCP clients · CLI · Tauri desktop · Expo mobile
                         │
                         ▼
                 soleaux / soleauxd
┌──────────────────────────────────────────────────────────┐
│ MCP: stdio + authenticated loopback Streamable HTTP      │
│ Public profile: exactly 12 slots                         │
│ Context compiler · registry · gateway · policy           │
│ SQLite WAL · serialized writes · structural index        │
│ Native intelligence                                      │
│ ├── Oxc                                                  │
│ ├── Tree-sitter                                          │
│ ├── pg_query                                             │
│ ├── shell structure/semantics                            │
│ ├── LSP broker (800 ms soft deadline)                    │
│ ├── Turborepo provider                                   │
│ └── Next.js provider                                     │
│ Safe edit: preview → preimage validation → atomic apply  │
└──────────────────────────────────────────────────────────┘
```

## Ownership

| Object | Owner |
|---|---|
| Native agent transcripts and threads | Native client |
| Soleaux context, memory, handoffs, audit | Soleaux |
| Public MCP catalog | Unified MCP contract |
| Skills, agents, rules, ownership, backends | Soleaux registry |
| Repository structural index | Soleaux native intelligence |
| Materialized native files | Soleaux, with origin/revision/rollback metadata |
| Credentials | Per-user secure store; never worktree |
| Mobile parsing | None; mobile consumes daemon APIs |

## Request routing

```text
Request
→ workspace/path sandbox
→ profile/capability validation
→ structural index
→ native parser or LSP provider
→ framework/package provider when applicable
→ bounded result
→ redaction
→ provenance/trust/coverage
→ MCP response
```

## Public-versus-control-plane separation

The model sees exactly 12 root tools. The following remain outside root `tools/list`:

- gateway backend tools;
- skills and agent resources;
- rules and ownership tables;
- remote runs and approvals;
- devices, backups, updates, service administration;
- desktop/mobile operations.

## Native selection

If workspace configuration selects a parser or LSP, the selected production implementation must be native. Unknown, disabled, incompatible, or non-native selections fail before successful tool execution.

## Storage

SQLite WAL owns durable local state with serialized writes. Repository indexes and artifacts remain outside vendor internal stores. Path containment, symlink safety, redaction, and content hashes apply before model exposure.
