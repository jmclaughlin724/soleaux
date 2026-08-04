# Soleaux Phase 2 gap inventory

Phase 1 already owns the fixed twelve-tool catalog, Context Packet V2, native
index, native LSP broker, safe preview/edit, registry core, and optional
provider substitution. Phase 2 closes the remaining Lineage A gaps without
adding root tools:

| Lineage A surface | Native Phase 2 owner | Public exposure |
|---|---|---|
| Namespaced MCP gateway | `daemon/mcp/src/gateway.rs` | CLI + registry only |
| OAuth/bearer storage | CLI foreground `soleaux mcp login/logout` | Never worktree; never a root tool |
| Skills, agents, rules | Workspace/user/team native registry scan | `registry.list` / `registry.read` |
| Adopt / revert | `daemon/mcp/src/provisioning.rs` | CLI only |
| Attach | Native workspace and per-user attachment records | CLI only |
| Authority/governance graph | `daemon/intelligence/src/governance.rs` | Context Packet V2 + registry table |
| Offline PostgreSQL | Existing `pg_query` optional substitution | One-for-one substitution |
| Next.js discovery | Existing static native provider | One-for-one substitution |

No second public catalog or Python production runtime is introduced. The only
Python files in the native source are conformance smokes.
