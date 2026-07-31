# Soleaux

Soleaux is a local FastMCP server for repository intelligence. Its package entry point uses stdio by default and combines bounded structural analysis, optional language-server semantics, typed relation tables, framework route and handler discovery, source evidence, and hash-bound editor previews without writing an index or database into the target repository. The service lifespan publishes one SQLite catalog generation before serving reads; `context`, `search`, `query`, and `owners` are pure reads of that generation and never wait, capture, parse, build, enrich, or publish. The lifecycle-owned indexer alone performs background reconciliation, and full projection preparation—including one batched PostgreSQL rebind, resolve, and merge—runs off the MCP event loop.

Optional `[mcp.<name>]` entries expose namespaced MCP backends through the same server without changing the fixed local Soleaux catalog. The `[providers.<name>]` section overrides the built-in language-server defaults. The `[health]` section declares retention thresholds surfaced through the `soleaux://health/v1` resource.

The fixed local catalog is ten tools and seven resources, with zero prompts and zero resource templates. `describe` and `soleaux://about` derive that catalog from the registered FastMCP components in `soleaux.server`; configured gateway components are additive and namespaced.

Local MCP tool identities are bare actions. The client contributes the configured server namespace once, so a host can render the local `describe` tool as `soleaux.tools.describe` and flatten `navigate` to `soleaux_navigate`.

## Package defaults

The reusable package has zero MCP backend defaults: `ResolvedConfig.default().mcp` is empty, the wheel contains no `soleaux.toml`, and the Python distribution does not depend on Node packages. Backend commands are always an explicit workspace concern.

## OpenTelemetry

FastMCP emits native server spans such as `tools/call search`; Soleaux does not maintain a duplicate process-local invocation counter. Instrumentation is enabled by default, but the package installs only `opentelemetry-api`, so it exports nothing on its own.

The process that hosts Soleaux owns the SDK and exporter. Configure them before constructing the server:

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
```

Install the SDK and selected exporter in that host environment and configure its endpoint and credentials there. Keep exported attributes bounded and non-sensitive: counts, coverage status, and elapsed time are suitable; query text, source content, credentials, and absolute paths are not. Set `FASTMCP_ENABLE_TELEMETRY=false` only when the host must suppress FastMCP spans entirely.

## Publication status

Version `0.1.0` is technically installable and licensed under the MIT License. Publishing to PyPI remains blocked until the distribution name is rechecked and the user explicitly authorizes the external release.

## Requirements

- Python `>=3.14`
- `uv` for workspace development and artifact installation
- Optional local language servers for TypeScript, Python, and Go semantics
- Optional MCP backend commands or URLs configured through `soleaux.toml`
- Optional `adopt` extra (`pip install "soleaux[adopt]"`) for the `soleaux adopt` workflow that detects and consolidates competing language servers under soleaux

## Adopt existing language servers

After install, point soleaux at your repository to detect editor-launched language servers (Pylance, typescript-language-server, rust-analyzer, gopls, python-lsp-server, and others) and consolidate them under soleaux:

```sh
soleaux --root /path/to/repository adopt --dry-run
```

Every detection and planned write is shown before any file is touched. Drop `--dry-run` for an interactive confirmation prompt, or pass `--yes` for non-interactive runs. Modified files are backed up to `.soleaux-backups/`; restore with `soleaux adopt --revert`. See the [adopt guide](src/soleaux/resources/docs/adopt-guide.md) for the full workflow.

## Repository gateway profile

The repository-root `soleaux.toml` is the executable owner of configured gateway providers and lifecycles. Gateway provider failures are isolated from the local catalog and other available providers. Discovered tool catalogs are reused only for each backend's bounded `cache_ttl_seconds`, preventing MCP clients' protocol-mandated `tools/list` requests from restarting command-backed providers before every local call. Each MCP host owns approval policy for discovered Soleaux and gateway tools.

## Discover canonical repository records

Soleaux discovers ownership and governance records without imposing field names, roles, platforms, or path conventions. A Markdown table becomes eligible when the immediately preceding parsed HTML comment contains `{"soleaux":{"canonical_records":true}}`; add a `required_fields` string array inside the same object only when the consumer requires specific authored columns. A structured JSON, TOML, or YAML collection uses the equivalent parsed `canonical = true` flag and optional `required_fields` list. The marker activates the existing source by reference and never copies its records.

All discovery uses the owning document, configuration, command, or source parser. Filenames and prose phrases carry no governance meaning, and neutral traced evidence never acquires a consumer-authored policy role.

The source's heading, field labels, identities, values, and row attributes remain intact. Reference-bearing fields become consumer-authored declared relationships. Other scalar fields remain attributes. An optional `Required fields:` or `Required columns:` statement can make authored fields required; no roles are required by default.

Starting from declared repository references, Soleaux traces neutral evidence such as structured references, configuration closures, registrations, scripts, imports, tests, and consumers. Those inferred edges never acquire a consumer policy role. Conflicts and redundancies compare only declarations for the same record identity and authored field.

## Run Soleaux

The installed command starts stdio when no subcommand is present:

```sh
soleaux --root /path/to/repository
```

Use the CLI for product administration and delivery checks; interactive analysis belongs to the MCP tools:

```sh
soleaux --version
soleaux --root /path/to/repository doctor --json
soleaux --root /path/to/repository lint --path src
soleaux --root /path/to/repository check mcp --json
soleaux --root /path/to/repository check health --json
soleaux --root /path/to/repository generate soleaux-toml --output soleaux.toml
soleaux --root /path/to/repository install ast-grep-rust
```

`lint` exits `0` when clean, `1` when findings remain, and `2` on a request error, so CI can consume it directly.

Start repository research with one `context` call, state the task objective, and add repository-relative `paths` only when the task is scoped. `context` performs ranked SQLite full-text retrieval and relation expansion against the generation already published by the service lifespan; it does not capture files, parse source, or rebuild the catalog. The typed `soleaux.context/v1` packet contains bounded source excerpts, canonical owners, consumers, constraints, conflicts, validation routes, explicitly requested FastMCP resources, and honest coverage gaps. Its human-readable tool content is suitable for a host pre-prompt hook, while structured content preserves the packet for MCP clients. Host adapters preserve every required semantic section within their configured envelope; when those sections cannot fit, the adapter fails with explicit `host_context_limit` instead of silently slicing or dropping them. When coverage is complete, begin work without another discovery call; use `search`, `query`, `owners`, `navigate`, or `inspect` only for a named gap or an exact semantic question. Run `soleaux lint` on the CLI for the workspace's configured structural rules.

## Read the packaged guidance

- [Agent workflow](src/soleaux/resources/docs/agent-workflow.md)
- [Quickstart](src/soleaux/resources/docs/quickstart.md)
- [Tool catalog](src/soleaux/resources/docs/tool-catalog.md)
- [Provider and gateway configuration](src/soleaux/resources/docs/provider-configuration.md)
- [Evidence and coverage](src/soleaux/resources/docs/evidence-and-coverage.md)
- [Editor safety](src/soleaux/resources/docs/editor-safety.md)
- [Adopt existing language servers](src/soleaux/resources/docs/adopt-guide.md)
- [Troubleshooting](src/soleaux/resources/docs/troubleshooting.md)
- [Server instructions](src/soleaux/resources/docs/server-instructions.md)
- [Packaged skill](src/soleaux/resources/skills/soleaux/SKILL.md)

## Safety model

Normal analysis uses an in-memory SQLite catalog and leaves no `.soleaux` directory, index, database, or cache in the target repository or user cache. Explicit `disk` mode stores a content-fingerprint-keyed disposable projection outside the checkout, revalidates content identity before use, bounds retained generations and size, and fails closed when disk state cannot be trusted. Legacy explicit `auto` mode may fall back to memory; it is not the default. Within the fixed local catalog, `preview` never writes and `edit` is the only file-writing tool; editing requires explicit confirmation plus a live match for the preview ID, digest, process epoch, and every preimage hash. Gateway tools retain their upstream capabilities and annotations, so callers must also honor the host approval policy for each configured provider tool.
