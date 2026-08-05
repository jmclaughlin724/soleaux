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
| Closed native phases | Phase 0, Phase 1, and Phase 2 |
| Current implementation phase | Phase 4 — reproducible unsigned alpha closure |
| Deferred claims gate | Phase 3 — live same-model / same-task product proof |
| Public MCP | Exactly 12 canonical slots |
| Production claim | **Not allowed** |
| Signed distribution | Not yet available |

Soleaux remains a development product. The native MCP foundation, unified catalog, Context Packet V2, correctness wave, canonical state, recovery, encrypted artifact vault, operational CLI, per-user service, and typed local IPC are implemented and exact-gated. The current release branch is proving a reproducible unsigned alpha through clean-home install, daemon restart, doctor, backup, export, repair, restore, and uninstall smokes. That gate must finish successfully before Phase 4 is closed.

The deferred live model comparison still gates quantified efficacy claims. It does not authorize a production claim, and `productionClaimAllowed` remains false.

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
- CLI-mediated gateway credentials outside the worktree.
- Canonical local state, recovery, backup, repair, and audit-chain validation.
- Encrypted content-addressed artifacts with workspace-separated key material.
- A per-user daemon, typed local IPC, and concurrent local-client support.

Soleaux does **not** replace Claude Code, Codex, OpenCode, Cursor, or the IDE. It provides shared intelligence to them.

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

`context.compile` is the sole public context compiler. It returns a bounded `soleaux.context/v2` packet containing:

- sources;
- canonical owners;
- consumers;
- constraints;
- conflicts;
- validation routes;
- supporting facts;
- requested resources;
- explicit coverage gaps;
- native engine and provider identity;
- trust and provenance;
- token/byte accounting;
- secret-redaction counts.

The binding contract is [CONTEXT-PACKET-V2.md](CONTEXT-PACKET-V2.md).

## Native architecture

```text
soleaux / soleauxd (Rust + Tokio)
├── stdio MCP
├── authenticated loopback Streamable HTTP
├── Oxc + Tree-sitter + pg_query + shell intelligence
├── native LSP broker with an 800 ms soft deadline
├── SQLite WAL structural index
├── canonical state, migrations, replay, backup, restore, and repair
├── encrypted content-addressed artifact vault and capability policy
├── typed peer-checked local IPC and per-user service lifecycle
├── Context Packet V2 compiler
├── hash-bound preview → edit pipeline
├── namespaced MCP gateway
├── skills / agents / rules / governance registry
└── adopt / attach / doctor / catalog / mcp CLI workflows
```

Python remains permitted for fixtures, conformance, packaging, and release-verification scripts only; clients do not choose between Python and Rust product modes.

## Documentation

Start here:

1. [PROJECT-STATUS.md](PROJECT-STATUS.md) — current phase and evidence.
2. [ROADMAP.md](ROADMAP.md) — completed and remaining phases.
3. [TASKS.md](TASKS.md) — executable work items.
4. [HANDOFF.md](HANDOFF.md) — compact cold-start instructions for another agent.
5. [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) — release and claims gates.
6. [docs/README.md](docs/README.md) — full documentation map.

Public positioning and claim constraints live under [docs/marketing](docs/marketing/MESSAGING.md). Historical Python-lineage material is indexed under [docs/history](docs/history/README.md) and remains available through Git history.

## Development and release posture

The current program is intentionally fail-closed:

- No public profile above 12 tools.
- No contract-digest drift without reviewed contract changes.
- No successful context packet with silent truncation or false complete coverage.
- No non-native parser/LSP fallback on a selected production path.
- No production claim before the deferred Phase 3 proof and later release gates.
- No signed-release, store-publication, or general-availability claim from an unsigned alpha.

See [CONTRIBUTING guidance in AGENTS.md](AGENTS.md) for the required validation and documentation update process.

## License

The project is licensed under the MIT License. Release packaging retains the repository license and generates a deterministic Cargo dependency inventory, but signed distribution and external publication remain separate reviewed gates.
