---
title: Configure providers and MCP backends
description: Configure Soleaux's lazy language-server providers, health thresholds, semantic modes, and opt-in namespaced MCP server backends.
sidebar:
  label: Providers and MCP backends
  order: 5
---

Soleaux works without a language server or MCP backend. Structural search, source context, and syntax-owned tables remain available when semantic providers are absent.

## Use the built-in defaults

Version `0.1.0` recognizes ten lazy provider contracts. Deno is excluded unless `deno.json` or `deno.jsonc` is detected at the workspace root. Bounded PostgreSQL source extraction, repository resolution, diagnostics, and generic semantic/table promotion are integrated without a database connection. Optional connected-database enrichment remains experimental and is not part of the default release claim.

| Language              | Command                              |
| --------------------- | ------------------------------------ |
| TypeScript/JavaScript | `typescript-language-server --stdio` |
| Python                | `pyright-langserver --stdio`         |
| Go                    | `gopls`                              |
| Rust                  | `rust-analyzer`                      |
| Shell                 | `bash-language-server start`         |
| Deno                  | `deno lsp` (requires `deno.json`)    |
| Astro                 | `astro-ls --stdio`                   |
| Prisma                | `prisma-language-server`             |
| YAML                  | `yaml-language-server --stdio`       |
| PostgreSQL (optional) | `postgres-language-server lsp-proxy` |

TypeScript is available when the provider executable resolves under the target root's `node_modules/.bin` or on `PATH`. Python requires `pyright-langserver` on `PATH` or in the virtual environment. Go requires `gopls` on `PATH`. Other servers resolve via `shutil.which` against the same paths.

Language-server configuration is inert until a semantic request selects a matching file. Startup, resource reads, syntax-only requests, and structural work start no language-server process. The service closes every selected provider during shutdown. PostgreSQL source promotion is parser-owned and does not require the language server or a database connection; connected-database enrichment is separately gated. See the [PostgreSQL security boundary](/guides/postgresql-security).

## Override providers from `soleaux.toml`

When `soleaux.toml` declares any enabled `[providers.<name>]` row with a `command`, those rows merge with the built-in defaults: matching names override built-ins, custom names are appended, and disabled built-ins are removed. An empty or all-disabled `[providers]` table falls back to the ten defaults above.

Each provider row accepts five fields:

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `command` | list of strings | — | The language-server argv, e.g. `["pyright-langserver", "--stdio"]`. |
| `extensions` | list of strings | `[]` | File extensions (without dot) this server handles. |
| `initialization_options` | object | `{}` | Forwarded as the LSP `initializationOptions` on handshake. |
| `root_dir` | string | `"."` | Provider root, resolved relative to the workspace root. |
| `enabled` | boolean | `true` | Set `false` to disable without removing the row. |

```toml
schema_version = "soleaux.config/v1"

[[workspaces]]
id = "application"
root = "."

[providers.python]
command = ["pyright-langserver", "--stdio"]
extensions = ["py", "pyi"]
root_dir = "."
enabled = true

[providers.css-language-server]
command = ["vscode-css-languageserver", "--stdio"]
extensions = ["css"]
```

Provider declarations are validated by the typed schema (Pydantic, `extra="forbid"`). Unknown keys fail clearly at load time.

## Choose a semantic mode

- `best_available` (default): default for exploration; return partial evidence when a provider is unavailable
- `syntax_only`: skip all Language Server Protocol work
- `semantic_required`: fail when semantic coverage is incomplete

## Configure the catalog lifecycle

The service publishes one bounded base SQLite generation during lifespan startup, then atomically replaces it after service-owned background enrichment. The default is in-memory and writes neither the repository nor the platform user cache:

```toml
[catalog]
mode = "memory"
retained_generations = 2
max_disk_size_mb = 512
```

`disk` is an explicit content-addressed cache outside the checkout. Its workspace/source fingerprint is part of the filename, captured content hashes are revalidated before reuse, retained generations and size are bounded, and invalid state fails closed. `off` disables the catalog. `auto` is retained only for an explicit compatibility configuration that may fall back from disk to memory; it is not the zero-config default.

`context`, `search`, `query`, and `owners` read the published generation. `context` never captures files, parses source, or constructs a replacement analysis frame on its request path.

## Configure PostgreSQL provenance lanes

Soleaux ships no repository path conventions. A workspace may classify PostgreSQL facts by declaring nonoverlapping repository-relative roots:

```toml
[postgresql.lane_roots]
desired_state = ["database/schema"]
migration_history = ["database/migrations"]
test = ["database/tests"]
generated = ["generated/database"]
fixture = ["fixtures/database"]
```

Unmatched files remain `unclassified`. Provenance affects repository replay and coverage but is not part of object identity.

## Configure health thresholds

The `[health]` section declares retention thresholds surfaced through the `soleaux://health/v1` resource and the `soleaux check health` CLI command.

```toml
[health]
logs_retention_days = 7
temp_retention_hours = 24
archived_sessions_retention_days = 14
max_logs_db_size_mb = 500
```

| Field | Default | Range | Purpose |
| --- | --- | --- | --- |
| `logs_retention_days` | 7 | 1–90 | Days to retain Codex log database entries. |
| `temp_retention_hours` | 24 | 1–168 | Hours to retain workspace `.tmp/` entries. |
| `archived_sessions_retention_days` | 14 | 1–90 | Days to retain Codex archived session files. |
| `max_logs_db_size_mb` | 500 | 100–10000 | Size threshold for log database health alerts. |

## Add MCP server backends

The workflow for adding an MCP server is: discover, add, verify, confirm.

### 1. Discover

Scan the workspace for recommended MCP servers based on detected config files and dependencies:

```sh
soleaux --root /path/to/repository suggest --json
```

Suggestions are read-only — they do not modify any files.

### 2. Add

Append the suggested `[mcp.<name>]` block to `soleaux.toml`. An MCP backend must set exactly one of `command` or `url`:

```toml
[mcp.local]
command = ["backend"]
lifecycle = "on_demand"

[mcp.remote]
url = "https://mcp.example.com/mcp"
auth = "bearer_env"
auth_token_env = "MCP_TOKEN"
```

Alternatively, scaffold a starter config from existing host configs plus detected suggestions:

```sh
soleaux --root /path/to/repository generate soleaux-toml --output soleaux.toml
```

Three lifecycle modes are available:

- `on_demand` (default) creates a fresh backend client per operation. Best for rarely used backends.
- `session` retains one backend client per connected downstream session and closes it with that session. Requires a command backend; best for interactive stdio backends used throughout a session.
- `shared` retains one backend client across all sessions for the server lifetime. Requires a URL backend declared with `stateless = true`; best for stateless HTTP backends where connection reuse eliminates per-call initialization overhead.

Command backends may set literal `env` values and a workspace-contained relative `cwd`. URL backends require HTTPS except for loopback HTTP; bearer tokens, custom headers, and custom CA paths are read only from named environment variables.

URL backends declare one auth mode through `auth`: `none` (default), `bearer_env` (requires `auth_token_env`), or `oauth`. OAuth backends may set `oauth_scopes`, `oauth_client_name`, `oauth_client_metadata_url`, `client_id_env` with optional `client_secret_env`, and `token_store` (`disk` default, `keyring` opt-in); tokens persist in a user-private per-backend store, and login is CLI-mediated through `soleaux mcp login <name>`. See the [MCP gateway](/guides/mcp-gateway) guide for the full auth model, registration priority, and troubleshooting.

MCP backend component listings and calls can start a configured backend. Soleaux does not forward incoming headers, roots, sampling, elicitation, logs, or progress to it. A provider failure warns and omits only that provider's components so the fixed local catalog and other available providers remain usable.

Gateway tool capabilities and annotations are upstream-owned. MCP host approval is configured independently by the client; Soleaux never writes approval-mode keys.

Per-backend tool approval effects live in the `[policy]` section: a `default` effect of `allow`, `ask`, or `deny` per declared `[mcp]` backend plus per-tool overrides keyed by unprefixed backend tool names. `soleaux.toml` owns these effects; host approval surfaces are rendered output. See the [MCP gateway](/guides/mcp-gateway) guide for the policy model.

## Generate a starter config

The `soleaux generate soleaux-toml` command scans existing `.mcp.json` and `.codex/config.toml` in the workspace and emits a starter `soleaux.toml` with `[mcp]` entries derived from enabled MCP servers and a default `[health]` section:

```sh
soleaux --root /path/to/repository generate soleaux-toml --output soleaux.toml
```

## Validate MCP config consistency

The `soleaux check mcp` command cross-validates `.mcp.json` and `.codex/config.toml`, reporting servers present in one file but not the other, and disabled servers that should be removed:

```sh
soleaux --root /path/to/repository check mcp --json
```
