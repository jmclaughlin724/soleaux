# Soleaux Boundary

## Identity

`soleaux` is a standalone FastMCP repository-intelligence server with one generic, zero-backend gateway. It is not an app surface, client library, or alias. The package entrypoint and protocol or isolated tests retain stdio; root `scripts/soleaux/` owns this workspace's access-controlled, stateless HTTP composition and macOS service controller. The same composition root adds only explicitly configured, namespaced MCP proxy providers through `[mcp.<name>]` entries in `soleaux.toml`, so the packaged default remains zero-proxy.

The locked runtime graph is `fastmcp==4.0.0b1`, `fastmcp-slim==4.0.0b1`, `mcp==2.0.0`, and `mcp-types==2.0.0`. The supported `fastmcp` meta-package selects the client/server feature set used by Soleaux; its `fastmcp-slim` implementation ships the CLI consumed by `fastmcp.json`. The three implementation/protocol packages are pinned directly only to make every transitive pre-release explicit to uv's default pre-release resolver. Application code imports `fastmcp` and `mcp_types`, never `fastmcp-slim` or `fastmcp.server.dependencies.get_context`; use `fastmcp.dependencies.CurrentContext` for injected context. `fastmcp.types` is reserved for UI parameter annotations. `Client.list_tools()` is the cross-protocol liveness probe; `Client(..., mode="legacy").ping()` is legacy-only.

The gateway uses public `Client`, `ProxyProvider`, and `StatefulProxyClient` lifecycle APIs. `on_demand` creates a fresh client per uncached provider operation; the upstream `cache_ttl` bounds component-catalog reuse so protocol-mandated `tools/list` calls do not restart command-backed providers before every local tool call. Legacy command-backed `session` delegates connection keying, caching, and exit-stack cleanup to `StatefulProxyClient`; Soleaux overrides only its public `new()` clone boundary to construct a fresh stdio transport per front connection. `shared` is valid only for an explicitly declared stateless HTTP backend.

## Session Freedom

Any AI agent in any session may call any Soleaux tool at any time. The workspace deployment serves stateless HTTP: every request is independent, the service keeps no per-host MCP session state, and a service restart is invisible to in-flight agents beyond the in-progress request. Product state lives only on the service side — lifecycle-published catalog generations, service-owned LSP sessions, and hash-bound edit preimages — never in per-host MCP sessions. Where a configured backend is itself stateful, the repository-owned client bounds recovery to one re-initialize plus one retry per request before failing typed. The `describe` and `service:status` boot epoch exists for operational correlation, not staleness detection.

## Fixed Catalog

The registered FastMCP components in `server.py` (standalone `@tool`/`@resource` decorators) are the canonical catalog owner; `soleaux/surface.py` serializes them for every non-wire discovery surface and `scripts/generate_guidance.py` derives the packaged documentation blocks. `[mcp.*]` proxy entries and the explicitly configured skills provider add namespaced components without mutating or duplicating the local catalog.

Local MCP tool identities are bare actions because the configured host already supplies the `soleaux` server namespace. A host may therefore render `describe` as `soleaux.tools.describe` or flatten `navigate` to `soleaux_navigate`; never register a second `soleaux_` prefix in the local catalog.

**Tools**

| Tool | What it does | Owner |
| --- | --- | --- |
| `describe` | Returns product, catalog, configuration, provider, storage, and transport identity. | `analysis/service.py` |
| `search` | Ranked repository facts from the currently published SQLite generation with kind/path filters and truthful semantic coverage; it never waits or launches structural or LSP work. | `analysis/service.py` |
| `context` | Returns one typed, relation-complete task packet with source, owners, consumers, constraints, conflicts, validation routes, requested resources, and explicit gaps selected through ranked SQLite full-text retrieval and relation expansion. | `analysis/service.py` |
| `query` | Executes explicit batches over the fixed typed table catalog with truthful request coverage. | `analysis/service.py` |
| `owners` | Explains one canonical consumer record, authored field relationships, neutral evidence, conflicts, and redundant claims. | `analysis/service.py` |
| `navigate` | Performs LSP-backed definitions, references, implementations, hover, and call-hierarchy operations. | `analysis/service.py` |
| `inspect` | Performs LSP-backed diagnostics, completion, signature-help, and code-action operations. | `analysis/service.py` |
| `preview` | Normalizes rename, format, code-action, and structural-rewrite edits into hash-bound patches. Never writes. | `editor/preview.py` |
| `edit` | Applies a preview after revalidating all preimage hashes. Mutating, requires confirmation. | `editor/apply.py` |
| `restart_lsp` | Restarts explicitly selected language-server sessions. Process-mutating. | `lsp/sessions.py` |

Structural lint is CLI-only (`soleaux lint`) and projects its findings through `quality.standards`; it is not an additional MCP tool.

**Resources**

| Resource | What it returns | Owner |
| --- | --- | --- |
| `soleaux://about` | Product identity, schema versions, and the component-derived tool/resource catalog. | `server.py:about()` |
| `soleaux://guide` | Agent workflow over the fixed ten-tool catalog. | `server.py:guide()` |
| `soleaux://quickstart/v1` | First-run package, host, and request workflow. | `server.py:quickstart()` |
| `soleaux://tables/v1` | The 42 table descriptors, prerequisites, availability, and coverage semantics. | `server.py:tables()` |
| `soleaux://health/v1` | Configured retention thresholds from `[health]` config. | `server.py:health()` |
| `soleaux://providers/v1` | Built-in LSP provider catalog with versions and install hints. | `server.py:providers()` |
| `soleaux://skills/v1` | Resolved workspace skill roots and discovery state. | `server.py:skills()` |

**The fixed local catalog has zero prompts and zero resource templates.** Explicitly configured providers, including `[skills]`, may add namespaced resources or templates without changing that local catalog.

## Module Ownership

| Package | Responsibility | Key files |
| --- | --- | --- |
| `lsp/` | Lazy JSON-RPC LSP broker, sessions, resolvers, providers, operations, generation barrier | `broker.py`, `sessions.py`, `resolvers.py`, `providers.py`, `operations.py`, `generation.py`, `contracts.py` |
| `structural/` | Lazy ast-grep worker lifecycle, the three structural engines (python/napi/rust), workspace rule loading and standards projection, fragments, rules, snapshot | `worker.py`, `supervisor.py`, `engines.py`, `workspace_rules.py`, `standards.py`, `rust_runtime.py`, `projections.py`, `fragments.py`, `snapshot.py` |
| `catalog/` | Typed catalog facts, generation-time structural and PostgreSQL promotion, SQLite store with `facts_fts` and `context_fts`, ranked search and context reads | `contracts.py`, `projects.py`, `structural.py`, `postgresql.py`, `search.py`, `store.py`, `generation.py` |
| `frameworks/` | Framework registration detectors and config-fact interpretation; parser execution stays in `structural/` | `contracts.py`, `registrations.py`, `nextjs.py` |
| `authority/` | Manifest, native governance, ownership, delivery, verifier, and conflict resolution | `resolver.py`, `governance.py`, `contracts.py` |
| `relations/` | Module dependency graph, derived topology, impact analysis | `resolver.py`, `materializer.py`, `modules.py` |
| `editor/` | Hash-bound editor previews and one-shot apply with preimage validation | `preview.py`, `apply.py`, `contracts.py` |
| `contracts/` | Closed request/result/context/evidence/config/structural contracts (Pydantic, `extra="forbid"`) | `config.py`, `context.py`, `requests.py`, `results.py`, `evidence.py`, `coverage.py`, `structural.py`, `cursor.py` |
| `analysis/` | Service entry point, analysis frame builder, search hydration, doctor/benchmark reports | `service.py`, `frame.py`, `hydration.py`, `budgets.py`, `task_registry.py` |
| `tables/` | Typed relation tables, evidence binding, table planner | `planner.py`, `evidence.py` |
| `suggestions.py` | MCP server suggestion catalog + repo-content detection | — |
| `skills.py` | Explicit workspace skill-root resolution + namespaced `SkillsDirectoryProvider` attach | — |
| `gateway.py` | MCP proxy provider lifecycle (on_demand, session, shared) | — |
| `server.py` | FastMCP server composition — the canonical tool/resource catalog owner via decorated components | — |
| `surface.py` | Serializes the registered components for docs, fixtures, and drift tests | — |
| `provisioning/` | `soleaux adopt` workflow: detect running LSPs, editor config, and competing MCP registrations; plan, apply, and revert writes to workspace configs | `adopt.py`, `detect_processes.py`, `detect_editor.py`, `detect_mcp.py`, `editor_writer.py`, `mcp_writer.py`, `backup.py`, `contracts.py` |
| `cli.py` | Product administration and delivery adapters (non-stdio commands) | — |
| `rust/` | Cargo workspace for the pinned Rust ast-grep worker (built only via `soleaux install ast-grep-rust`) | `Cargo.toml`, `Cargo.lock` |
| `scripts/` | Package-owned documentation and fixture generators | `generate_guidance.py`, `generate_zero_mcp_fixture.py` |

Root `scripts/soleaux/` owns this repository's optional HTTP deployment, credential distribution, host bridges, and macOS LaunchAgent lifecycle. None of those workspace activation choices are package defaults. `src/soleaux/resources/structural/napi_worker.mjs` is a supported artifact: consuming repositories may spawn it directly for structural-policy enforcement, and its `soleaux.structural/v1` wire contract is stable product surface.

## Catalog Lifecycle

`CatalogIndexer` is the sole owner of capture, catalog construction, enrichment, and publication. `SoleauxService.search()`, `context()`, `query()`, and `ownership()` are pure reads of the currently published SQLite generation: never add request-path waiting, capture, scanning, parsing, building, enrichment, or publication to those methods.

Tests that require enriched tables must explicitly call `await service._catalog_indexer.settle()` or poll the already-published catalog. Never make a production request wait for enrichment to satisfy a test. Full projection preparation stays off the MCP event loop; PostgreSQL facts are rebound, resolved, and merged once as an immutable batch before atomic publication.

## Configuration (`soleaux.toml`)

Validated by `ResolvedConfig` (Pydantic, `extra="forbid"`). Unknown keys fail at load.

### `[providers.<name>]` — LSP provider overrides

When any enabled provider row is present with a `command`, config augments the built-in defaults (merge semantics). Empty or all-disabled falls back to TypeScript, Python, and Go defaults.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `command` | list[str] | — | Language-server argv |
| `extensions` | tuple[str, ...] | `()` | File extensions (without dot) |
| `initialization_options` | object | `{}` | LSP `initializationOptions` |
| `root_dir` | str | `"."` | Provider root, relative to workspace |
| `enabled` | bool | `true` | Set `false` to disable |

### `[mcp.<name>]` — MCP proxy backends

Each enabled entry attaches one lazy, namespaced `ProxyProvider`. Three lifecycle modes: `on_demand` (fresh client per uncached upstream operation), `session` (one client per legacy downstream connection; rejected on the sessionless modern protocol), and `shared` (one client across requests in one protocol era). `cache_ttl_seconds` bounds reuse of discovered tool catalogs, including the `tools/list` request that MCP clients issue before a tool call. Backends set exactly one of `command` (stdio) or `url` (HTTPS).

### `[structural]` — Structural engine selection

Selects exactly one ast-grep engine per workspace; there is no automatic fallback, and version or capability mismatches fail closed with typed errors.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `backend` | `"python"` \| `"napi"` \| `"rust"` | `"python"` | The one engine that serves structural matchers, lint, and rewrites |
| `project_config` | str \| null | `null` | Contained workspace sgconfig path; absent means no workspace lint rules |
| `languages` | dict[str, str] | `{}` | napi-only dynamic language registrations, name → package resolved from the packaged worker |

Repository configuration cannot select structural executable or package paths. The NAPI worker resolves its exact engine and configured language packages relative to the packaged worker module; Rust executes only the exact installer-owned managed binary produced by `soleaux install ast-grep-rust`.

### `[health]` — Retention thresholds

Surfaced through `soleaux://health/v1` and `soleaux check health`. Fields: `logs_retention_days` (7), `temp_retention_hours` (24), `archived_sessions_retention_days` (14), `max_logs_db_size_mb` (500).

### `[skills]` — Workspace agent-skills discovery

Discovery is disabled until a repository explicitly enables it and lists contained workspace-relative `roots`; Soleaux does not choose platform or user skill directories. Resolved paths are de-duplicated, configured order determines duplicate-name precedence, and the upstream `SkillsDirectoryProvider` owns discovery, manifests, integrity hashes, and resource-template behavior. The repository's root `soleaux.toml` dogfoods this boundary by selecting its canonical shared skill root explicitly.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Set `true` to attach the configured provider |
| `roots` | tuple[str, ...] | `()` | Workspace-relative skill roots (contained, no `..`/absolute) |
| `reload` | bool | `false` | Re-scan on each request (dev only) |
| `main_file_name` | str | `SKILL.md` | Bare filename identifying a valid skill dir |
| `supporting_files` | `"resources"` \| `"template"` | `"template"` | `template` keeps catalogs compact and fetches selected supporting files on demand |

## LSP Providers

Built-in defaults derive from `BUILTIN_PROVIDERS` in `lsp/providers.py`. Ten catalog entries use a detection-based Deno/TypeScript partition:

| Language | Provider | Extensions |
| --- | --- | --- |
| TypeScript/JavaScript | `typescript-language-server` | `.ts .tsx .js .jsx .mjs .cjs .mts .cts` |
| Python | `pyright-langserver` | `.py .pyi` |
| Go | `gopls` | `.go` |
| Rust | `rust-analyzer` | `.rs` |
| Shell | `bash-language-server` | `.sh .bash .zsh` |
| Deno | `deno lsp` | `.ts .tsx .js .jsx .mjs` (when `deno.json` detected) |
| Astro | `astro-ls` | `.astro` |
| Prisma | `prisma-language-server` | `.prisma` |
| YAML | `yaml-language-server` | `.yaml .yml` |
| PostgreSQL (optional provider) | `postgres-language-server lsp-proxy` | `.sql` |

When `deno.json` or `deno.jsonc` is present, deno claims `.ts/.tsx/.js/.jsx/.mjs` and typescript-language-server retains `.cjs/.mts/.cts` only.

Bounded PostgreSQL source extraction, repository resolution, diagnostics, and promotion into generic repository, semantic, quality, and derived tables are integrated and work offline. Optional connected-database enrichment remains separately experimental and is not part of the default release claim.

`executable_available()` is a pure probe (`shutil.which`) — it never downloads or invokes the server. The gated installer (`lsp/install.py`, `soleaux install <name>`) requires the `SOLEAUX_AUTO_INSTALL` env var. `from_cclsp()` parses the retired CCLSP manifest format and is test-only.

`build_provider_registry(root, config)` in `analysis/frame.py` merges built-in defaults with `[providers.*]` config: config overrides replace built-ins by name, custom providers are appended, disabled built-ins are removed. The one-provider-per-extension invariant is enforced by `ProviderRegistry.__init__`.

`SemanticResolver.navigate()` in `lsp/resolvers.py` includes a position fallback: when the primary line/column returns empty results, it retries adjacent positions before returning empty.

## CLI Subcommands

`describe`, `search`, `context`, `query`, `navigate`, `inspect`, `doctor`, `benchmark`, `lint`, `check` (mcp/health), `suggest`, `generate` (soleaux-toml), `adopt`, and `install` (built-in providers, `typescript-runtime`, `postgresql-parser`, `ast-grep-rust`) are the supported CLI adapters. Invoking `soleaux` with no subcommand runs the stdio server; the FastMCP CLI owns generic run/list/call/inspect orchestration.

`lint` runs the configured workspace structural rules and exits `0` (clean), `1` (findings), or `2` (request error) — the CI delivery surface. `check mcp --probe` connects to each enabled `[mcp.*]` backend via `Client.ping()` + `Client.list_tools()`. `suggest` scans package.json/pyproject.toml deps and config files against a packaged catalog of 15 known MCP servers.

### `adopt` — consolidate competing language servers under soleaux

`soleaux adopt [--dry-run] [--yes] [--revert] [--target editor,mcp,providers] [--language python,typescript,...] [--force]` detects running LSP processes (psutil), editor config that selects a language server (`.vscode/settings.json` via json5), and competing MCP launch registrations (`.mcp.json`, `.codex/config.toml` via tomlkit, `opencode.json`). The default plan disables the editor's language-server selection, registers a portable `uvx soleaux` entry in each MCP launch config, and emits a commented `[providers.<name>]` block to `soleaux.toml`. Every modified file is backed up to `.soleaux-backups/`; `--revert` restores from the manifest. Refuses to apply without a TTY unless `--yes` is passed.

`adopt` requires the optional `[adopt]` extra (`pip install 'soleaux[adopt]'`, which pulls `psutil`, `tomlkit`, `json5`). The base install footprint is unchanged. Editor-config and MCP-config writes from the adopt orchestrator are the only writers for those files outside the existing `edit` tool path; the two authorities do not overlap.

The first `soleaux serve` invocation in a workspace with no `soleaux.toml` emits a one-time stderr nudge pointing at `soleaux adopt`, so new users learn the workflow. The nudge never blocks and only fires when the `[adopt]` extra is importable and stderr is a TTY.

## Standards

This package is the r074 implementation owner: uv lock/env, Ruff lint and format, strict Pyright, pytest with pytest-asyncio, and a valid `fastmcp.json`.

All Soleaux source, command, configuration, and document parsing must use an AST or the owning format parser. Regular-expression engines, literals, validator patterns, ast-grep `regex` constraints, and regex-backed test assertions are prohibited throughout `src/`, `scripts/`, maintained resources, and tests. Scalar protocol validation may use explicit character/state logic when no syntax tree exists. `tests/test_no_regex.py` is the executable package invariant.

`requires-python = ">=3.14"` (no upper bound, per PyPA guidance). The `[adopt]` optional extra pulls `psutil`, `tomlkit`, `json5`; the dev group mirrors it so the test suite can exercise the full adopt workflow.

Mutate dependencies only through `uv add`, `uv remove`, and `uv lock`. Never hand-edit `uv.lock`, `.venv/`, or installed packages.

## Commands

From the repository root:

- `pnpm soleaux:lint` — Ruff lint and format check
- `pnpm soleaux:typecheck` — strict Pyright
- `pnpm soleaux:test` — pytest behavior contracts
- `pnpm soleaux:dev` — run the server over stdio
- `uv run --directory tools/soleaux soleaux suggest --json` — scan workspace for MCP server suggestions
- `uv run --directory tools/soleaux soleaux check mcp --probe --json` — verify MCP backend liveness
- `uv run --directory tools/soleaux soleaux adopt --dry-run` — detect competing LSPs and MCP registrations, print the plan
- `uv run --directory tools/soleaux soleaux adopt --yes` — apply the plan non-interactively (writes `.vscode/settings.json`, `.mcp.json`, `.codex/config.toml`, `opencode.json`; backs up originals)
- `uv run --directory tools/soleaux soleaux adopt --revert` — restore the most recent set of backups from `.soleaux-backups/`
- `uv build --no-sources --package soleaux` — build the wheel and sdist (use `--no-sources` so the workspace `[tool.uv.sources]` reference does not leak in)
