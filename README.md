# Soleaux

> **One MCP. One governed catalog. Accurate repository context.**

Soleaux is a local-first repository-intelligence layer for the AI coding tools teams already use. It turns a repository into one bounded MCP server, compiles structurally verified context, and exposes shared skills, rules, agents, ownership, and gateway capabilities without recreating a sprawling model-facing tool list.

```bash
soleaux serve .
```

## Current development status

| Field | Value |
|---|---|
| Version | `0.4.0-dev.5` |
| Closed implementation phases | Phase 0, Phase 1, Phase 2, and Phase 4 |
| Current implementation phase | Phase 5 — adapters, lifecycle, intelligence depth, and extensibility |
| Deferred claims gate | Phase 3 — three-arm live product proof |
| Public MCP | Exactly 12 canonical slots |
| Unsigned alpha | Reproducible and independently verified |
| Production claim | **Not allowed** |
| Signed distribution | Not yet available |

Phase 4 is closed. The default branch now contains the native correctness wave, canonical state and recovery, encrypted artifact vault and policy, stable operations CLI, per-user daemon and typed local IPC, and a reproducible unsigned development-alpha package. The alpha was tested through clean installation, daemon launch and restart, doctor, backup, export, repair, offline restore, and state-preserving uninstall.

P5-001 is also closed: the daemon-owned workspace/client registry, restart persistence, trust and compatibility safe mode, bounded IPC responses, and transactional attach/revert convergence passed Linux and macOS gates. P5-002 through P5-006 are closed as well: the six-platform client capability matrix, pinned artifact verification, and safe-mode read-only admission landed through PRs #38 and #40. The next task is P5-007.

Evidence:

- [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json)
- [`P5-002-P5-006-CLOSURE-RECEIPT.json`](P5-002-P5-006-CLOSURE-RECEIPT.json)
- [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json)
- [`PHASE4-ALPHA-CLOSURE-RECEIPT.json`](PHASE4-ALPHA-CLOSURE-RECEIPT.json)
- [`PHASE4-INDEPENDENT-VERIFICATION.json`](PHASE4-INDEPENDENT-VERIFICATION.json)

Soleaux remains a development product. Phase 5–8 product work and the deferred Phase 3 efficacy proof are still open, and `productionClaimAllowed` remains false.

See [PROJECT-STATUS.md](PROJECT-STATUS.md) for the authoritative current state.

## Product purpose

Soleaux addresses three recurring problems in agent-assisted development:

1. **MCP sprawl** — many overlapping servers expose large schemas and inconsistent data.
2. **Context waste** — agents repeatedly read whole files or rediscover repository structure.
3. **Fragmented governance** — skills, rules, agents, ownership, and validation knowledge live in client-specific files.

Soleaux provides:

- One repository MCP attachment.
- A hard-capped public tool surface.
- Native AST/CST/LSP and framework intelligence.
- A bounded `soleaux.context/v2` packet with provenance, trust, coverage, gaps, and redaction.
- One catalog for skills, agents, rules, ownership, tables, and namespaced MCP backends.
- Hash-bound preview/edit safety.
- Canonical local state, operation leases, recovery, backup, restore, repair, and audit.
- Encrypted content-addressed artifacts and deny-by-default capability policy.
- A per-user daemon, typed local IPC, and a stable operations CLI.
- Reproducible unsigned development-alpha packaging.

Soleaux does **not** replace Claude Code, Claude Desktop, Codex, OpenCode, Cursor, or the IDE. It provides shared intelligence and governed lifecycle services to them.

## Canonical public tool surface

The active profile contains exactly these 12 slots:

```text
context.compile
code.search
memory.search
get_symbols
registry.list
registry.read
repo_info
navigate
inspect
preview
edit
restart_lsp
```

Workspace configuration may replace one canonical slot with one of these native optional providers, but the active count remains 12:

```text
parse_and_validate_postgres_sql
turborepo.packages
next.get_routes
```

Gateway backends, skills, agents, rules, registry domains, remote controls, and administrative operations never inflate the root `tools/list`.

The binding contract is [UNIFIED-MCP-PROFILE.md](UNIFIED-MCP-PROFILE.md).

## Context compilation

`context.compile` is the sole public context compiler. It returns a bounded `soleaux.context/v2` packet containing sources, owners, consumers, constraints, conflicts, validation routes, supporting facts, requested resources, explicit coverage gaps, native engine identity, trust, provenance, token and byte accounting, and redaction counts.

The binding contract is [CONTEXT-PACKET-V2.md](CONTEXT-PACKET-V2.md).

## Native architecture

```text
soleaux / soleauxd (Rust + Tokio)
├── stdio MCP
├── authenticated loopback Streamable HTTP
├── Oxc + Tree-sitter + pg_query + shell intelligence
├── native LSP broker with an 800 ms soft deadline
├── SQLite WAL structural and canonical state
├── migrations, leases, replay, backup, restore, and repair
├── encrypted artifact vault and capability policy
├── typed peer-checked local IPC and per-user service lifecycle
├── Context Packet V2 compiler
├── transactional preview → edit pipeline
├── namespaced MCP gateway
└── skills / agents / rules / governance registry
```

Python remains permitted for fixtures, conformance, packaging, and release verification only; clients do not choose between Python and Rust product modes.

## Documentation

Start here:

1. [PROJECT-STATUS.md](PROJECT-STATUS.md)
2. [ROADMAP.md](ROADMAP.md)
3. [TASKS.md](TASKS.md)
4. [HANDOFF.md](HANDOFF.md)
5. [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md)
6. [docs/README.md](docs/README.md)

## Development and release posture

The program remains fail-closed:

- No public profile above 12 tools.
- No contract-digest drift without reviewed contract changes.
- No successful context packet with silent truncation or false complete coverage.
- No non-native parser/LSP fallback on a selected production path.
- No quantified efficacy claim before the deferred Phase 3 proof.
- No signed-release, store-publication, or general-availability claim from an unsigned alpha.

## License

Soleaux is licensed under the MIT License.
