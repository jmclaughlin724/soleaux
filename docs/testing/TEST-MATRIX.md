# Test Matrix

## Current proven matrix

| Area | Phase 0 | Phase 1 | Phase 2 | Phase 4 |
|---|---:|---:|---:|---:|
| Rust format/check/Clippy/test/build | Green | Green | Green | Green |
| Cargo audit | Green | Green | Green | Green |
| Binary help/version | Green | Green | Green | Green |
| Contract digests | Green | Green | Green | Green |
| Exact 12-tool profile | Contract | Green | Green | Green |
| Context Packet V2 | Contract | Green | Green | Green |
| Native LSP/search/editor | Foundation | Green | Regression green | Transactional/freshness green |
| Gateway/catalog/provisioning/governance | — | — | Green | Transactional regression green |
| Canonical state/migrations/leases/recovery | — | — | — | Green |
| Encrypted artifacts and capability policy | — | — | — | Green |
| Stable CLI/per-user service/typed IPC | — | — | — | Green |
| Reproducible unsigned package/SBOM | — | — | — | Green |
| Extracted install/restart/backup/restore/repair/uninstall | — | — | — | Green |
| Independent artifact verification | Green | Green | Green | Green |

## Current Phase 5 matrices

- Claude Code SDK/CLI versions and SessionStore behavior.
- Claude Desktop supported local connector and export/import behavior.
- Codex CLI/Desktop app-server protocol schemas and lifecycle.
- OpenCode OpenAPI/SSE/plugin versions and cursor reconciliation.
- Cursor and generic MCP hosts.
- TypeScript/VTSLS, BasedPyright, Bash, Rust, Go, SourceKit, clangd, Kotlin, JDT, Vue, Svelte, Astro, MDX, YAML, JSON, HTML, and CSS.
- Turborepo versions and repository layouts.
- Next.js applications, route modes, and DevTools capability sets.
- Memory lifecycle, materializer compatibility, handoffs, runs/subagents, SDKs, CI, and editor integration.
- `anilize` plus two additional approved design partners.

## Deferred Phase 3 matrix

- Same authenticated model/client and identical tasks across no-Soleaux, historical Python, and native arms.
- Correctness, tool-schema/file-read/context measurements, retries, time, cost, and failures.
- Secret leakage, fallback, tool-ceiling, truncation, and task-drift checks.

## Phase 6–8 matrices

- macOS/Windows/Linux and architectures.
- Desktop installers and mobile device/store builds.
- Pairing, LAN, relay, push, revoke, replay, and outage behavior.
- Upgrade, rollback, repair, backup/restore, and uninstall.
- Performance, scale, fuzzing, security, privacy, accessibility, and incident response.
- Signing, notarization, provenance, staged rollout, and rollback thresholds.
