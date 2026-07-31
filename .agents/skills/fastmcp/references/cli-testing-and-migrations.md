# CLI, Testing, and Migrations

## Contents

- [CLI and Configuration](#cli-and-configuration)
- [Running Servers](#running-servers)
- [`fastmcp.json` Ownership](#fastmcpjson-ownership)
- [Config Execution and Overrides](#config-execution-and-overrides)
- [Inspecting a Server You Did Not Write](#inspecting-a-server-you-did-not-write)
- [Programmatic Inspect API](#programmatic-inspect-api)
- [Testing Strategy](#testing-strategy)
- [Inspect and Diagnose](#inspect-and-diagnose)
- [v2 to v3 Migration](#v2-to-v3-migration)
- [v3 to v4 Migration](#v3-to-v4-migration)

## CLI and Configuration

Run the installed CLI through the owner's environment:

```bash
fastmcp --help
fastmcp run --help
fastmcp call --help
```

The CLI is built on **Cyclopts**, not Typer or Click. Help output renders parameters in a single `Parameters` panel, subcommand groups list under `Commands`, and choice-constrained flags print an inline `[choices: ...]` list. Read the panel rather than assuming Click conventions such as `--flag/--no-flag` pairs on every boolean.

The pinned release exposes eleven top-level commands:

| Command | Purpose |
| --- | --- |
| `auth` | Group. Authentication utilities; the only subcommand is `cimd` (Client ID Metadata Document utilities for OAuth) |
| `call` | Call a tool, read a resource, or get a prompt on a server |
| `dev` | Group. Development tooling; subcommands `apps` and `inspector` |
| `discover` | Enumerate servers configured in editor and project configs |
| `generate-cli` | Generate a standalone CLI script, and a `SKILL.md`, from a live server |
| `inspect` | Report the assembled catalog as text or JSON |
| `install` | Group. Install server configuration into clients and formats |
| `list` | List tools, and optionally resources and prompts, on a server |
| `project` | Group. The only subcommand is `prepare` |
| `run` | Run a server, or proxy a remote one |
| `version` | Print FastMCP, MCP, Python, platform, and installed root path |

`dev` and `install` are **groups**, not single commands; `fastmcp dev server.py` is not a valid invocation. Use `fastmcp dev inspector server.py` or `fastmcp dev apps server.py`. See [Integration hosts and SDKs](integration-hosts-and-sdks.md) for the seven `install` targets.

Only use subcommands and flags shown by the pinned release. Confirm the baseline through [Version and source routing](version-and-source-routing.md).

## Running Servers

`fastmcp run server.py` imports the module, finds an instance named `mcp`, `server`, or `app`, and applies CLI transport settings without editing source. Prefer an explicit `server.py:object` when discovery would be ambiguous.

`run` resolves seven spec forms:

| Form | Behavior |
| --- | --- |
| `server.py` | Import the module and auto-detect `mcp`, `server`, or `app` |
| `server.py:app` | Import and run the named object |
| `http://server-url` | Connect to a remote server and serve a proxy |
| `mcp.json` | Proxy every server in the MCPConfig |
| `fastmcp.json` | Run through FastMCP project configuration |
| _(no argument)_ | Look for `fastmcp.json` in the current directory |
| `-m my_module` | Run via `python -m my_module` |

A `.json` spec is recognized by suffix, not by filename, so `prod.json` is accepted. The runner then discriminates by content: a top-level `mcpServers` key selects MCPConfig proxying, and anything else loads as a FastMCP project config. Auto-detection with no argument matches only the exact name `fastmcp.json` in the current directory.

Runtime options in the pinned release:

- `--transport` / `-t` (`stdio`, `http`, `sse`, `streamable-http`), `--host`, `--port` / `-p`, `--path`, `--log-level` / `-l`, and `--no-banner`;
- `--python`, repeatable `--with`, `--with-requirements`, and `--project`; any of these cause execution through a `uv run` subprocess rather than the current environment;
- server-owned arguments after `--`, such as `fastmcp run server.py -- --config config.json --debug`;
- `--reload` / `--no-reload` and repeatable `--reload-dir`;
- `--skip-env` and `--skip-source` for an already prepared environment or source;
- `--module` / `-m` for `python -m <module>` execution;
- `--stateless`.

`--stateless` is exposed on `run` but is documented in help as "used internally for reload". It sets `stateless=True` on the underlying `run_async` call. Reload sets it for you; do not reach for it as a general production switch without confirming the server actually tolerates sessionless operation.

Auto-reload restarts a subprocess on file changes and watches a broad extension set, not only Python — the watched suffixes include `.py`, `.ts`, `.tsx`, `.vue`, `.svelte`, `.css`, `.html`, `.md`, `.json`, `.toml`, `.yaml`, and common image, font, and media types. Reload always runs stateless:

- stdio logs `Reload mode enabled (using stateless sessions)`;
- HTTP logs the same plus an explicit warning that features requiring bidirectional communication, such as elicitation, are unavailable;
- `--reload` with `--transport sse` is **not** an error. It logs a warning that sessions are lost on restart, then falls through and runs **without** reload.

Do not enable reload in production, and do not add a second launcher when the repository already owns one.

## `fastmcp.json` Ownership

The live [Project Configuration](https://gofastmcp.com/deployment/server-configuration) guide defines `fastmcp.json` as the portable, declarative source of truth for three questions:

| Section | Question | Installed owner |
| --- | --- | --- |
| `source` | Where is the server code? | Required `FileSystemSource` |
| `environment` | What build/runtime Python environment is needed? | Optional `UVEnvironment` |
| `deployment` | How should it run? | Optional transport/runtime settings |

Add `"$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json"` for a versioned schema, IDE completion, and validation. That exact URL is the installed `FASTMCP_JSON_SCHEMA` constant. The `.../latest.json` variant follows the newest schema and is less reproducible; pin `v1.json` for checked-in projects.

The only installed source type is `filesystem` (the default):

- `path` is required and resolved relative to the configuration file;
- `entrypoint` may name a `FastMCP` instance or a no-argument factory returning one;
- without `entrypoint`, discovery checks `mcp`, `server`, and `app`;
- a `path` may use the CLI-compatible `file.py:object` form, but an explicit field is clearer in shared configuration.

Do not use the live guide's future `git` or `cloud` source types until the installed schema and source union implement them.

The only installed environment type is `uv` (the default). Its optional fields are:

- `python`: exact version, lower bound, or range;
- `dependencies`: list of PEP 508 requirements;
- `requirements`: requirements-file path;
- `project`: directory containing `pyproject.toml`;
- `editable`: **list** of package paths to install editable, including monorepo siblings.

When any environment field is set, FastMCP builds and uses `uv run`; otherwise it can use the current environment. `editable` is `list[Path]`, so a live development example showing a scalar `"."` is not valid installed-source guidance. Keep Python and FastMCP pinned through the owning project and install only declared extras.

The optional `deployment` fields are:

| Field | Values and use |
| --- | --- |
| `transport` | `stdio`, `http`/`streamable-http`, or legacy `sse` |
| `host` | HTTP bind host; use loopback locally and explicit `0.0.0.0` only behind an owned exposure boundary |
| `port` | HTTP port |
| `path` | Exact external MCP endpoint path before outer mounts and proxies |
| `log_level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `env` | Runtime string environment mapping with `${VAR_NAME}` interpolation |
| `cwd` | Trusted process working directory, relative to the config file when not absolute |
| `args` | Server-owned command arguments passed after `--` |

Omitted values fall back to runtime settings: `stdio`, loopback host `127.0.0.1`, port `8000`, Streamable HTTP path `/mcp`, and `INFO` server logging. The live deployment table now documents the same `8000` and `/mcp`. Still set externally significant production values explicitly rather than inheriting a default.

Interpolation replaces each existing `${VAR_NAME}` from the launcher's environment and leaves an unknown placeholder unchanged. Use it to inject externally managed values or construct environment-specific URLs; never commit actual secrets in `env` examples or production files.

## Config Execution and Overrides

- `fastmcp run` auto-detects only an exact `fastmcp.json` in the current directory.
- `fastmcp run prod.fastmcp.json` runs an explicitly named configuration; any `.json` path is accepted and classified by content.
- CLI values override configuration values, so `--port`, `--transport`, `--log-level`, or additional `--with` packages are useful for a bounded local experiment. Production automation should keep effective configuration auditable.
- `--skip-env` is for an activated or prebuilt virtual environment, Docker image, CI environment, system environment, or a nested uv execution where rebuilding would recurse.
- `--skip-source` skips preparation for source types that need fetching, such as a future remote source; it has no effect for a local filesystem source.
- `fastmcp project prepare fastmcp.json --output-dir ./env` creates a persistent uv project with a generated `pyproject.toml` and installed `.venv` for build/run separation; later run it with `--project ./env`. It takes `--skip-source` and nothing else.
- The config can be passed to `run`, `dev inspector`, `inspect`, and `install` workflows. Verify every subcommand in the selected release.

Use separate explicitly named configuration files only when environments genuinely differ. A minimal config needs source only; development may add loopback HTTP/debug and editable packages; production adds pinned project/requirements, explicit HTTP host/port/path, non-debug logging, runtime secret references, and a trusted working directory. Migrate shell launchers by mapping `uv --with` inputs into `environment.dependencies` and FastMCP transport flags into `deployment`, then keep only necessary deployment-time CLI overrides.

Validate the file with the installed schema and CLI, inspect the effective catalog under the same configuration, and exercise every configured consumer from its own launch command.

## Inspecting a Server You Did Not Write

`discover`, `list`, and `call` form a client-side workflow for interrogating a server you do not own: find what is already configured, read its catalog, then invoke one component. Nothing here requires importing the server's code or editing its configuration.

Upstream ships an agent-facing skill for this workflow at [`skills/fastmcp-client-cli/SKILL.md`](https://github.com/PrefectHQ/fastmcp/blob/main/skills/fastmcp-client-cli/SKILL.md). It is a useful orientation but is narrower than the installed CLI on five points — resources and prompts, `.js` targets, bearer tokens, `--timeout`, and transport scope. Where it disagrees with installed help, installed help wins.

### Step 1 — `discover`

`fastmcp discover` scans **Claude Desktop, Claude Code, Cursor, Gemini CLI, Goose, and project-level `mcp.json`** and reports every server it can read from disk. It takes exactly two flags: repeatable `--source` and `--json`.

The `--source` values are the six scanner labels, which are **not** the same set as the seven [`fastmcp install`](integration-hosts-and-sdks.md) targets:

| `--source` value | Scanned location |
| --- | --- |
| `claude-desktop` | Platform Claude Desktop directory, `claude_desktop_config.json` |
| `claude-code` | `~/.claude.json`, both global and `projects[<cwd>]` scopes |
| `cursor` | Nearest `.cursor/mcp.json`, walking up from the working directory to `$HOME` |
| `gemini` | `~/.gemini/settings.json` and `<cwd>/.gemini/settings.json` |
| `goose` | Goose `config.yaml`, converting `extensions` entries into server records |
| `project` | `./mcp.json` in the working directory |

Note `gemini`, not `gemini-cli`; and `project` and `mcp-json`/`stdio` do not correspond. Filtering is an exact membership test against these strings, so a wrong label silently returns nothing rather than erroring.

`--json` emits an array of objects with exactly `name`, `source`, `qualified_name`, `transport_summary`, and `config_path`:

```json
[
  {
    "name": "render",
    "source": "claude-code",
    "qualified_name": "claude-code:render",
    "transport_summary": "http: https://mcp.render.com/mcp",
    "config_path": "~/.claude.json"
  }
]
```

Duplicate names across sources are preserved deliberately. Address a server by bare `name` when it is unique, or by `source:name` (`claude-code:render`) when it is not; an ambiguous bare name raises an explicit "found in multiple sources" error.

### Step 2 and 3 — Shared Server-Target Grammar

`list` and `call` accept the **same** `SERVER-SPEC` grammar, resolved in this order. `generate-cli` accepts it too.

| Target form | Resolution |
| --- | --- |
| `http://…` / `https://…` | Passed through to transport inference. With `--transport sse`, the URL is rewritten to end in `/sse` before inference |
| `server.py` | Spawned as a subprocess: `fastmcp run <absolute path> --no-banner` over stdio. The `fastmcp` executable must be resolvable on `PATH` |
| `server.js` | Passed through to transport inference unchanged |
| `mcp.json` | Loaded as an MCPConfig and resolved to its configured server |
| `--command "npx -y @scope/server"` | Shell-split into a stdio command and arguments |
| `weather` or `cursor:weather` | Resolved against `discover` results |

A path is treated as a file when it exists on disk, or when it ends in `.py`, `.js`, or `.json` and is not a directory. `--command` is mutually exclusive with a positional spec; supplying both exits with an error.

Because a `.py` target shells out to `fastmcp run`, it inherits that command's object-discovery rules (`mcp`, `server`, `app`) and needs no `if __name__ == "__main__"` block in the target file.

`--transport` accepts exactly `http` or `sse` and applies to **URL targets only**. It has no effect on file, command, or discovered-name targets.

### Step 2 — `list`

```bash
fastmcp list server.py --resources --prompts --json
```

| Flag              | Effect                                    |
| ----------------- | ----------------------------------------- |
| `--resources`     | Also list resources                       |
| `--prompts`       | Also list prompts                         |
| `--input-schema`  | Print full input schemas                  |
| `--output-schema` | Print full output schemas                 |
| `--json`          | Emit JSON instead of a table              |
| `--timeout`       | Connection timeout in seconds             |
| `--auth`          | `oauth`, a bearer token string, or `none` |

Without `--resources` or `--prompts`, only tools are listed.

### Step 3 — `call`

```bash
fastmcp call server.py search query=hello limit=5 verbose=true --json
```

`call` dispatches on the **shape of the target**, which is the single largest gap in upstream's tool-only framing:

| Target                  | Dispatch                                    |
| ----------------------- | ------------------------------------------- |
| `search`                | Tool call (default)                         |
| `probe://status`        | Resource read — any target containing `://` |
| `greet` with `--prompt` | Prompt render                               |

All three return real payloads. A resource read emits a list of contents; a prompt render emits `description` and `messages`.

Arguments are `key=value` pairs coerced against the target tool's input schema before dispatch:

| Schema type | Coercion |
| --- | --- |
| `integer` | `int(raw)`; non-numeric raises `Expected integer, got …` |
| `number` | `float(raw)` |
| `boolean` | `true`/`1`/`yes` and `false`/`0`/`no`, case-insensitive |
| `array`, `object` | `json.loads(raw)` |
| anything else, or unknown key | Left as a string |

`--input-json` supplies a base argument dict and `key=value` pairs **merge over it**. A single positional argument beginning with `{` is itself treated as `--input-json`. Verified: `--input-json '{"query":"from-json","limit":1}' limit=9` calls with `limit` 9 and `query` `"from-json"`.

An unknown tool or prompt name produces a close-match suggestion and exit status 1:

```
Error: Tool serch not found. Did you mean: search?
```

### Auth Defaults

`--auth` on `list`, `call`, and `generate-cli` accepts three shapes: the string `oauth`, **a bearer token string**, or `none` to disable.

When `--auth` is omitted, the CLI **automatically attempts OAuth for HTTP targets**. This is a silent outbound behavior: pointing `fastmcp list` at an unfamiliar HTTPS URL can begin an OAuth flow. It no-ops against a server that does not require auth. Non-HTTP targets default to no auth. Pass `--auth none` to suppress the attempt explicitly.

Every client command also installs a terminal elicitation handler, so a server may prompt interactively during a call.

### JSON Output Shape

`--json` output is a **hand-written projection per handler**, not a model dump, so key casing is not uniform across commands. Verified against the same server:

- `list --json` emits `inputSchema`, `outputSchema`, and `mimeType`;
- `call --json` on a tool emits `content`, `is_error`, and `structured_content`.

Parse the shape the command you ran actually produced. Do not assume it matches the wire format, the programmatic models below, or the other command.

### `generate-cli`

`fastmcp generate-cli <SERVER-SPEC>` connects, reads the catalog, and writes a standalone Python script (default `cli.py`) plus a `SKILL.md` agent skill. It takes `OUTPUT`, `--force`/`-f`, `--timeout`, `--auth`, and `--no-skill`. Review generated output before committing it; it is a snapshot of a live catalog, not a maintained contract.

## Programmatic Inspect API

`fastmcp/utilities/inspect.py` backs `fastmcp inspect` and is usable directly when a test or tool needs the catalog as data.

```python
async def inspect_fastmcp(mcp: FastMCP[Any] | SDKServer) -> FastMCPInfo
async def inspect_fastmcp_v2(mcp: FastMCP[Any]) -> FastMCPInfo
async def inspect_fastmcp_v1(mcp: SDKServer) -> FastMCPInfo

def format_fastmcp_info(info: FastMCPInfo) -> bytes
async def format_mcp_info(mcp: FastMCP[Any] | SDKServer) -> bytes
async def format_info(
    mcp: FastMCP[Any] | SDKServer,
    format: InspectFormat | Literal["fastmcp", "mcp"],
    info: FastMCPInfo | None = None,
) -> bytes
```

`inspect_fastmcp` dispatches to the `v1`/`v2` variants by server type. `InspectFormat` is a `str` enum with members `fastmcp` and `mcp`. Note `format_mcp_info` takes the **server**, not a `FastMCPInfo`, because the MCP projection is produced from a live connection.

The models are snake_case throughout:

| Model | Fields |
| --- | --- |
| `FastMCPInfo` | `name`, `instructions`, `version`, `website_url`, `icons`, `fastmcp_version`, `mcp_version`, `server_generation`, `tools`, `prompts`, `resources`, `templates`, `capabilities` |
| `ToolInfo` | `key`, `name`, `description`, `input_schema`, `output_schema`, `annotations`, `tags`, `title`, `icons`, `meta` |
| `PromptInfo` | `key`, `name`, `description`, `arguments`, `tags`, `title`, `icons`, `meta` |
| `ResourceInfo` | `key`, `uri`, `name`, `description`, `mime_type`, `annotations`, `tags`, `title`, `icons`, `meta` |
| `TemplateInfo` | `key`, `uri_template`, `name`, `description`, `mime_type`, `parameters`, `annotations`, `tags`, `title`, `icons`, `meta` |

The two CLI formats are genuinely different documents, not casing variants:

- `--format fastmcp` reports a FastMCP-shaped document with top-level `server`, `environment`, `tools`, and per-component `key` values such as `"tool:search@"`, using snake_case (`input_schema`, `mime_type`).
- `--format mcp` reports the MCP protocol projection with `serverInfo`, `capabilities`, and camelCase (`inputSchema`, `websiteUrl`).

`--format` / `-f` is **required whenever `-o`/`--output` is used**. Omitting it exits with `--format is required when using -o/--output`. Without either flag, `inspect` prints a human-readable text summary.

Prefer the programmatic API over parsing CLI stdout when the caller is Python. Use `fastmcp inspect` when the consumer is a shell pipeline or another language, and pin `--format` explicitly so the document shape is part of the recorded contract.

## Testing Strategy

The live [Testing your FastMCP Server](https://gofastmcp.com/servers/testing) guide uses `pytest`, `pytest-asyncio`, and an in-process `fastmcp.Client`. Add `pytest-asyncio` as a development dependency when the owner does not already provide equivalent async-test support, and prefer:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

This removes per-test `@pytest.mark.asyncio` decoration. Use an async fixture that enters `Client(mcp)` and yields it; `Client` automatically chooses `FastMCPTransport` for an in-process server.

```python
import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport

@pytest.fixture
async def mcp_client():
    async with Client(mcp) as client:
        yield client

async def test_tools(mcp_client: Client[FastMCPTransport]):
    tools = await mcp_client.list_tools()
    assert {tool.name for tool in tools} >= {"add"}
```

This proves component serialization, discovery, dispatch, callbacks, middleware, and lifecycle with low cost. Use `@pytest.mark.parametrize` for meaningful valid/invalid argument cases and invoke tools with `await client.call_tool(name, arguments)`. For typed results, assert `result.data` is present, has the expected type, and equals the expected value; inspect content and structured content separately when either is contract-relevant.

For large catalog or result structures, `inline-snapshot` can keep an intentional contract readable; populate deliberate snapshots with `pytest --inline-snapshot=fix,create`. Use `dirty-equals` only for well-understood dynamic fields. Prefer focused field assertions over snapshots when the full structure is not public contract, and review every snapshot update instead of accepting drift mechanically.

Add targeted transport tests where the risk lives:

- stdio for subprocess startup, environment, stdout isolation, timeout, and teardown;
- ASGI/HTTP for auth, paths, headers, streaming, limits, and lifespan;
- a real host for Apps, browser OAuth, or host-specific capabilities;
- worker and storage integration for durable tasks.

Test at least:

- component catalogs and identity;
- valid and invalid inputs;
- structured, binary, and error results used by the implementation;
- authorization and redaction;
- elicitation callback success, decline, cancel, and failure (plus client-side sampling handlers when the client answers a legacy server);
- progress, logs, notifications, and cancellation;
- lifespan cleanup and transport disconnect;
- provider refresh, duplicate handling, and transform output.

Prefer focused assertions over full catalog snapshots unless the catalog itself is the contract.

The upstream FastMCP repository contains broad client and server examples, but consult the tests from the matching release tag when copying patterns; `main` may exercise future APIs.

## Inspect and Diagnose

Use `fastmcp inspect` to enumerate the assembled server, and `fastmcp list` / `fastmcp call` to interrogate it as a client. Verify tools, prompts, resources, templates, auth, and transport settings under the same flags production or the owner command uses.

When behavior fails:

1. reproduce through Client;
2. separate framework behavior from application logic;
3. inspect the installed source and matching release tests by symbol;
4. reduce to a minimal server;
5. compare with the matching release tag, not `main`;
6. report whether the issue is configuration, usage, compatibility, or a likely upstream defect.

## v2 to v3 Migration

Treat migration as a contract change, not an import-only rewrite.

- Inventory server construction, decorators, Context usage, providers, proxy/composition, middleware, auth, client transports, CLI/config, and deployment.
- Pin the target release and extras.
- Follow the official [upgrading from FastMCP 2](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-2) guide for removed and renamed APIs.
- Replace legacy composition or proxy patterns with installed providers and transforms.
- Re-inspect component identity, schemas, and auth.
- Port tests to Client and run transport-sensitive coverage.
- Remove compatibility code only after all consumers use the new contract.

Do not simultaneously redesign application behavior unless the request includes it.

## v3 to v4 Migration

**v4 is the installed era in this repository.** It is not future evaluation work. Treat [Upgrading from FastMCP 3](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3) and [What's new](https://gofastmcp.com/getting-started/whats-new) as the migration authority, and the installed module as the tiebreaker whenever they disagree.

Because the unversioned `https://gofastmcp.com/python-sdk/*` pages carry no version marker, they cannot establish which era they describe. Confirm every API against installed source before adopting it.

Migration surfaces that carry real work:

| Surface | v3 shape | v4 shape |
| --- | --- | --- |
| Composition | `FastMCP.import_server`, `mount(prefix=…, as_proxy=…)` | `mount(server, namespace=…, tool_names=…)`; see [Providers and composition](providers-and-composition.md) |
| Proxying | `FastMCP.as_proxy` | Module-level `from fastmcp.server import create_proxy`, with a `mode` protocol-era kwarg |
| Tool decorator | `exclude_args`, `serializer`, `decorator_mode` | Removed; see [Server components](server-components.md) |
| Transforms | `add_tool_transformation`, `remove_tool` | `add_transform`, `add_extension` |
| HTTP client | `httpx` | `httpx2` |
| Errors | `McpError(ErrorData(...))` | `McpError(code, message, data=None)` |
| Wire fields | `inputSchema`, `mimeType`, `isError` | snake_case equivalents, with a warn-once compatibility shim |
| Tasks | Embedded Docket task support in `fastmcp.server.tasks` | Module removed; the engine moved out of core behind the `io.modelcontextprotocol/tasks` extension and the separate `fastmcp-tasks` distribution. See [Background tasks](tasks.md) |

Work the migration in this order:

1. Inventory private-module imports and deep submodule paths first — a pre-release pin can break them on any bump.
2. Prove each removal locally (`python -c "import X"`) rather than trusting a changelog line.
3. Replace composition and proxy call sites, which change shape rather than name.
4. Rename wire fields on sight. The compatibility shim keeps stale camelCase running while emitting `FastMCPDeprecationWarning`; treat it as a migration aid, not a contract.
5. Re-inspect the catalog and re-run transport-sensitive tests.

Do not update the lockfile without explicit authorization.

Use the [CLI documentation](https://gofastmcp.com/cli/overview), [testing](https://gofastmcp.com/servers/testing), the [release list](https://github.com/PrefectHQ/fastmcp/releases), and [llms.txt](https://gofastmcp.com/llms.txt). Confirm every command and migration API in the installed release, and route version questions through [Version and source routing](version-and-source-routing.md).
