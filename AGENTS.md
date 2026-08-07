# Soleaux Agent Instructions

## Product boundary

Soleaux is **unified repository intelligence**, not an agent runtime or an agent operating system. Its primary product path is:

```text
soleaux serve .
→ one bounded MCP attachment
→ context.compile
→ structurally verified, provenance-tagged, capped context
```

Soleaux does not replace Claude Code, Claude Desktop, Codex, OpenCode, Cursor, or an IDE. Gateway, service, desktop, mobile, memory, sessions, and handoffs support the intelligence core.

## Authority order

When documents conflict, use this order:

1. Normative JSON contracts.
2. Locked human contracts.
3. Exact phase receipts and independent verification JSON.
4. `PROJECT-STATUS.json`.
5. `PROJECT-STATUS.md`.
6. `ROADMAP.md`.
7. `TASKS.md`.
8. Current phase implementation plan.
9. `HANDOFF.md`.
10. Public README and marketing material.
11. Historical lineage documentation.

Never advance a phase from prose when the receipt is absent or red.

## Locked invariants

```text
Version:                     0.4.0-dev.5
productionClaimAllowed:      false
Public hard ceiling:         12
Unified profile SHA-256:     89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc
Context V2 SHA-256:          3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f
```

Canonical tools:

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

Optional providers replace one slot and never append.

## Current scope

Phase 4 is closed. Phase 5 is the active implementation phase.

P5-001 through P5-006 are closed. Execute P5-007 through P5-029 top-down unless a dependency requires a documented parallel workstream. Phase 3 remains deferred and does not block implementation; it must be frozen and completed before quantified efficacy claims or a reviewed change to `productionClaimAllowed`.

## Native requirements

A selected production parser or LSP must be native. The intended path includes:

- Oxc for JS/TS analysis;
- Tree-sitter for incremental CST;
- `pg_query` for PostgreSQL;
- approved permissive shell parsing;
- native LSP sessions with an 800 ms soft deadline;
- SQLite WAL and persistent structural/canonical state;
- hash-bound source-range edits;
- typed local IPC with daemon-owned writes.

Python is permitted for fixtures, conformance, packaging, and verification only. Do not introduce a client-visible Python/Rust product mode.

## Hard stops

Stop immediately when:

1. `tools/list` would exceed 12.
2. Tool names/order, optional candidates, contract digests, version, or production claim drift without review.
3. A selected parser/LSP falls back to a non-native production implementation.
4. Context Packet V2 silently truncates, omits required sections, or falsely claims complete coverage.
5. A second public catalog or product mode is introduced.
6. A phase is declared closed without a green exact receipt and required independent verification.
7. A live Phase 3 task, model, budget, or rubric changes after the first run.
8. Work collides with another session's unique unmerged branch or evidence.
9. An unknown adapter/provider version is used in a mutating mode without a passing capability probe.

Stop report:

```text
STOP: <rule violated>
Evidence: <receipt, command, log, digest, or score>
Current phase: <phase> — OPEN
productionClaimAllowed: false
Next action needed: <exact fix or decision>
```

## Validation

Native changes require:

```bash
cargo fmt --all --check
cargo check --workspace --all-targets --all-features
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo build --release --workspace --all-features
cargo audit --deny warnings
./target/release/soleaux --help
./target/release/soleaux --version
./target/release/soleauxd --help
./target/release/soleauxd --version
```

Python and repository changes require the full locked CI sequence, including Ruff, Pyright, Pytest, telemetry, distribution, and documentation consistency.

Documentation changes require:

```bash
python3 scripts/check_documentation_consistency.py
```

Phase changes additionally require exact MCP/schema/capability smokes, an exact receipt, artifact upload, and independent verification.

## Documentation update protocol

When status changes, update in one reviewed change:

- `PROJECT-STATUS.json`
- `PROJECT-STATUS.md`
- `ROADMAP.md`
- `TASKS.md`
- `HANDOFF.md`
- `CHANGELOG.md`
- current phase status/results
- transcript gap registry and audit
- release checklist and release gates
- public claims when evidence changes

Do not create competing status documents.

## Collaboration boundaries

- Use a dedicated branch.
- No force-push.
- Do not delete branches with unique commits.
- Prune fully merged short-lived branches after evidence is durable.
- Do not delete another session's work.
- Preserve receipts and evidence immutably.
- Prefer stop-and-report over inferred success.

## Completion reporting

Every report distinguishes implemented, locally validated, exact-gate proven, independently verified, and blocked/not run. A passing unit test or merged PR is not a release claim.
