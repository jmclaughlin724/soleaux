# Integration Hosts and SDKs

## Source and Version Contract

Use this reference for OpenAPI conversion, ChatGPT, Claude Code, Claude Desktop, the OpenAI Responses API, the `fastmcp install` targets, and portable MCP JSON launch configuration.

This operational inventory was audited against every section and option in these live FastMCP guides:

- [OpenAPI](https://gofastmcp.com/integrations/openapi)
- [ChatGPT](https://gofastmcp.com/integrations/chatgpt)
- [Claude Code](https://gofastmcp.com/integrations/claude-code)
- [Claude Desktop](https://gofastmcp.com/integrations/claude-desktop)
- [OpenAI API](https://gofastmcp.com/integrations/openai)
- [MCP JSON configuration](https://gofastmcp.com/integrations/mcp-json-configuration)

Load [Version and source routing](version-and-source-routing.md) first. Prove the installed FastMCP version and extras, inspect the installed signature or CLI help, use the matching release as the exact FastMCP authority, and use the target host or SDK's current official documentation for its UI, entitlement, request, and response contract.

The audited surfaces are the pinned release's `FastMCP.from_openapi`, `OpenAPIProvider`, `RouteMap`, `create_proxy`, and the `fastmcp install` group. Recheck them when the owning project pins FastMCP differently, and confirm the owning manifest actually declares any SDK and extras before using them.

**The HTTP client is `httpx2`, not `httpx`.** `import httpx` raises `ModuleNotFoundError` in this environment. Every upstream OpenAPI example that writes `import httpx` must be translated. The installed `from_openapi` and `OpenAPIProvider` both type their `client` parameter as `httpx2.AsyncClient | None`.

Host UI labels, plan availability, and third-party SDK APIs change independently of FastMCP. The audited ChatGPT page uses a Connectors-oriented UI, while current OpenAI documentation may use Plugins or Apps language. The Claude Desktop page dates its remote-plan statement to June 2025. Preserve the capability guidance below, but refresh host-owned click paths and entitlements before presenting or automating them.

## Choose the Owning Integration

| Goal | Owner and transport | Preferred route |
| --- | --- | --- |
| Expose an existing HTTP API | OpenAPI remains authoritative; FastMCP calls it through `httpx2.AsyncClient` | `FastMCP.from_openapi(...)`, then allow-list and curate |
| Use tools in ChatGPT | ChatGPT owns enrollment; FastMCP owns the remote HTTP server | Streamable HTTP at the deployed MCP URL; add `search` and `fetch` for Deep Research |
| Use a local server in Claude Code | Claude Code owns registration; FastMCP creates a STDIO launcher | `fastmcp install claude-code` or `claude mcp add` |
| Use a local server in Claude Desktop | Claude Desktop owns `mcpServers`; FastMCP creates a STDIO launcher | `fastmcp install claude-desktop` or manual JSON |
| Use a local server in Cursor, Gemini CLI, or Goose | The host owns registration | The matching `fastmcp install` target |
| Bridge a host to remote HTTP | FastMCP Client/proxy owns the bridge | Native remote support when available; otherwise local STDIO `create_proxy(...)` |
| Let an OpenAI model call remote tools | Responses API owns orchestration; FastMCP owns the remote MCP endpoint | MCP tool entry with a current compatible model, scoped tools, auth, and approvals |
| Emit a launcher command only | Caller owns placement | `fastmcp install stdio` |
| Share a local launcher with an unsupported client | Client owns its config; FastMCP emits a portable entry | `fastmcp install mcp-json`, then merge under the client's `mcpServers` key |

Keep authentication, application authorization, component visibility, and model approval as separate controls. A host connection never authorizes a domain action.

## OpenAPI

OpenAPI conversion uses `OpenAPIProvider`. It is a useful bootstrap and prototype path, but a curated MCP surface usually gives a model fewer ambiguous tools and better task performance than mirroring a large REST API.

### Create and Own the Server

Create an async HTTP client for the upstream API, load a trusted OpenAPI document, and pass both to `FastMCP.from_openapi`:

```python
import httpx2
from fastmcp import FastMCP

api_client = httpx2.AsyncClient(
    base_url="https://api.example.com",
    headers={"Authorization": "Bearer ..."},
    timeout=30.0,
)

mcp = FastMCP.from_openapi(
    openapi_spec=openapi_spec,
    client=api_client,
    name="My API Server",
)
```

Put upstream API credentials on the `httpx2.AsyncClient`; this authenticates HTTP API calls. It does not authenticate MCP clients or authorize application operations. Own and close the async client through the server lifespan.

The installed signature is:

```text
FastMCP.from_openapi(
    openapi_spec: dict[str, Any],
    client: httpx2.AsyncClient | None = None,
    name: str = "OpenAPI Server",
    route_maps: list[RouteMap] | None = None,
    route_map_fn: OpenAPIRouteMapFn | None = None,
    mcp_component_fn: OpenAPIComponentFn | None = None,
    mcp_names: dict[str, str] | None = None,
    tags: set[str] | None = None,
    validate_output: bool = True,
    **settings,
) -> Self
```

The provider is importable directly for composition:

```python
from fastmcp.server.providers.openapi import (
    MCPType,
    OpenAPIProvider,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    RouteMap,
)
```

`OpenAPIProvider(...)` takes the same arguments minus `name`, with everything after `client` keyword-only. See [Providers and transforms](providers-and-transforms.md) for provider lifecycle.

The `**settings` forwarding does not make every historical server setting valid. `FastMCP(timeout=30)` raises `TypeError: FastMCP() got unexpected keyword argument(s): 'timeout'`. Put the upstream timeout on `httpx2.AsyncClient` and use component-specific timeouts only through APIs that explicitly expose them. Decide output validation and server settings explicitly instead of relying on a floating guide.

### Route Mapping

Every route becomes a tool by default for broad client compatibility. FastMCP checks ordered `RouteMap` rules, and the first rule whose criteria all match assigns the component type.

`RouteMap` is keyword-only. `mcp_type` is the one required field:

| `RouteMap` field | Default | Behavior |
| --- | --- | --- |
| `methods` | `'*'` | Match methods such as `['GET', 'POST']`; `'*'` matches all methods |
| `pattern` | `'.*'` | Match the route path with a regular expression |
| `tags` | empty set | Require every listed OpenAPI tag; an empty set disables tag filtering |
| `mcp_type` | **required** | Produce `TOOL`, `RESOURCE`, `RESOURCE_TEMPLATE`, or `EXCLUDE` |
| `mcp_tags` | empty set | Add FastMCP component tags to every matched route |

Custom rules run before the default catch-all tool rule. To restore pre-2.8 behavior, map parameterized GET paths to `RESOURCE_TEMPLATE` and remaining GET paths to `RESOURCE`; non-GET routes then fall through to tools.

Prefer explicit allow rules followed by `RouteMap(mcp_type=MCPType.EXCLUDE)` for production exposure. That catch-all prevents the default tool mapping from seeing unmatched routes. Specific exclusions can instead target administrative paths or OpenAPI tags such as `internal`.

Use `route_map_fn(route, assigned_type)` only when method, path, and tags are insufficient. It runs after route-map assignment for every route, including excluded routes. Return a new `MCPType` to override the assignment or `None` to preserve it. Review overrides carefully because the function can re-enable an excluded route.

### Names, Tags, and Component Customization

- Default names use `operationId` only up to the first `__`, then are slugified, limited to 56 characters, and made unique with numeric suffixes.
- `mcp_names` maps exact OpenAPI `operationId` strings to preferred names. Overrides are still slugified and truncated; unlisted operations use the default rule.
- `RouteMap.mcp_tags` adds tags for matching routes, while the top-level `tags` argument adds tags to every generated component.
- Original OpenAPI tags remain available to clients at `meta.fastmcp.tags`; do not confuse them with FastMCP component tags used for server-side filtering.
- `mcp_component_fn(route, component)` mutates each generated `OpenAPITool`, `OpenAPIResource`, or `OpenAPIResourceTemplate` in place. Its return value is ignored. Use it for model-facing descriptions, annotations, and tags OpenAPI cannot infer safely.

Review names, descriptions, schemas, tags, annotations, metadata, and exposed identities through FastMCP Client after every mapping change. Snapshot the intended catalog rather than an entire unstable OpenAPI document.

### Request Parameter Semantics

- Query parameters with `None` are omitted. An explicitly supplied empty string is sent as `name=`; omit the argument rather than passing `""` when the upstream must not receive it.
- Required path parameters are validated after `None` values are removed, and a missing value raises an explicit error.
- Query arrays follow OpenAPI `explode`: repeated keys when true, comma-separated values when false.
- Path arrays use comma-separated OpenAPI simple-style serialization.
- Header parameters are stringified and included in the upstream request.

Preserve request bodies, parameter locations, upstream security, timeouts, errors, and output-validation behavior. Exclude administrative and internal operations by default, and add focused drift tests when the authoritative specification changes.

## ChatGPT

The FastMCP ChatGPT guide covers remote HTTP servers in Chat and Deep Research modes. Run FastMCP with HTTP transport and deploy it at a stable HTTPS URL. A local tunnel such as ngrok is only for controlled development; never expose an unauthenticated sensitive server. Use the default `/mcp` path only when deployment has not changed it.

The page notes that the OpenAI MCP examples it links were written for FastMCP v2. Treat them as host-contract examples, then translate FastMCP imports and runtime options through the installed release.

### Chat Mode

At the audited page revision, Chat mode requires Developer Mode and the page lists Pro, Team, Enterprise, and Edu access. Treat the entitlement and UI path as time-sensitive host facts. The page's workflow is:

1. Enable Developer Mode in ChatGPT settings.
2. Create a connector with a name, the complete MCP server URL, provider-trust acknowledgement, and authentication when required.
3. Start a new chat, enable Developer Mode, and explicitly add the connector to that conversation.
4. Invoke a tool naturally and confirm the correct server and tool were selected.

The page shows `Settings -> Connectors`, `Advanced`, and `+ -> More -> Developer Mode`; current OpenAI documentation may instead use Security, Plugins, or Apps language. Follow [OpenAI's current connection guide](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt) for the actual UI and authentication flow.

Developer Mode removes the Chat-mode requirement to expose `search` and `fetch`. Without it, the page says a server lacking both tools is rejected. A connector must be enabled separately in each new chat and then remains active for that conversation.

For genuinely read-only tools, set the read-only annotation so ChatGPT can avoid unnecessary confirmations. The installed field is **snake_case**:

```python
from mcp_types import ToolAnnotations

ToolAnnotations(read_only_hint=True)
```

`ToolAnnotations` fields are `title`, `read_only_hint`, `destructive_hint`, `idempotent_hint`, and `open_world_hint`. The legacy `readOnlyHint` spelling still constructs through the compatibility alias, but write the snake_case name. Never mark a write, external side effect, or data-disclosing call as read-only merely to suppress a prompt. Enforce server-side authorization regardless of host confirmation.

### Deep Research

Deep Research uses only `search` and `fetch` from the selected server. The FastMCP page's minimal pattern is:

- `search(query: str)` returns an object containing string record IDs, such as `{'ids': [...]}`;
- `fetch(id: str)` returns the complete record, including stable ID, title, content, and useful metadata.

Reconcile those examples with [OpenAI's current Deep Research contract](https://developers.openai.com/api/docs/guides/deep-research) before shipping because result schemas and citation requirements are OpenAI-owned. Keep IDs stable, return the source URL or citation fields required by the current contract, bound record sizes, and authorize both operations.

To use the source, add the server to ChatGPT, choose Deep Research in a new chat, select the server as a source, and ask a research question. Verify that ChatGPT searches, fetches, and cites the intended records without gaining access to unrelated tools.

## The `fastmcp install` Group

`fastmcp install` is a **group with seven targets**, not a single command:

| Target | Effect |
| --- | --- |
| `claude-code` | Register through the Claude Code CLI |
| `claude-desktop` | Write the Claude Desktop `mcpServers` config |
| `cursor` | Open a `cursor://` install deeplink, or write workspace config with `--workspace` |
| `gemini-cli` | Register with the Gemini CLI |
| `goose` | Open a `goose://extension?...` install deeplink |
| `mcp-json` | Print the server object as JSON for manual placement |
| `stdio` | Print the launcher command string |

All seven take a required `SERVER-SPEC` and `--name` / `-n`. The shared resolver accepts both `file.py` (optionally `file.py:object`) and a `.json` project config, even though several help strings mention only the Python form — `fastmcp install mcp-json fastmcp.json` is verified to work and derives the server name from the config. The remaining flags differ per target:

| Flag | `claude-code` | `claude-desktop` | `cursor` | `gemini-cli` | `goose` | `mcp-json` | `stdio` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `--with` | yes | yes | yes | yes | yes | yes | yes |
| `--with-editable` | yes | yes | yes | yes | — | yes | yes |
| `--with-requirements` | yes | yes | yes | yes | — | yes | yes |
| `--python` | yes | yes | yes | yes | yes | yes | yes |
| `--project` | yes | yes | yes | yes | — | yes | yes |
| `--env` / `--env-file` | yes | yes | yes | yes | yes | yes | — |
| `--copy` | — | — | — | — | — | yes | yes |
| target-specific | — | `--config-path` | `--workspace` | — | — | — | — |

**There is no `--server-name` flag.** Every target uses `--name` / `-n`. A live page still showing `--server-name` is stale; installed CLI help outranks a floating guide.

`--copy` uses `pyperclip`, which is a bundled dependency of the installed release rather than an optional extra.

Cursor and Goose install through OS deeplinks by default rather than editing files. Cursor builds `cursor://anysphere.cursor-deeplink/mcp/install?name=<name>&config=<base64>`; Goose builds `goose://extension?...`. Pass `--workspace <dir>` to Cursor to write `.cursor/` inside a workspace instead. A deeplink hands the install to a running desktop application — confirm the correct application handled it rather than assuming success.

Never place secrets in checked-in `fastmcp.json`, command history, or client JSON; inject them through a trusted runtime mechanism when the host supports one.

## Claude Code

The Claude Code guide focuses on local STDIO servers. Configure remote HTTP or SSE servers through Claude Code's native MCP management rather than wrapping them merely to use the installer.

`fastmcp install claude-code` registers through `claude mcp add`, manages the `uv` launcher, and accepts `file.py:object`. Without an object suffix, FastMCP searches for `mcp`, `server`, or `app`.

```bash
fastmcp install claude-code server.py:mcp
```

Confirm the installed Claude CLI first. The live guide says the FastMCP integration searches its historical default at `~/.claude/local/claude`; use installed FastMCP and Claude Code help instead of assuming that path is still current.

For manual control, put the complete launcher after `--`:

```bash
claude mcp add dice-roller -- uv run --with fastmcp fastmcp run /absolute/path/server.py:mcp
claude mcp add weather -e API_KEY=... --scope user -- uv run --with fastmcp fastmcp run /absolute/path/server.py:mcp
```

Claude Code supports local, user, and project scope. Choose the narrowest scope matching the intended consumers. Put `uv run --python ...` and `--project ...` before dependency flags and the launched `fastmcp run` command.

Claude Code stores servers in `~/.claude.json`, under a global `mcpServers` key and per-directory `projects[<path>].mcpServers` keys. `fastmcp discover` reads both — see [CLI, testing, and migrations](cli-testing-and-migrations.md#inspecting-a-server-you-did-not-write).

After registration, test tools with a natural-language request. Resources use the guide's `@server:protocol://resource/path` form, and prompts use `/mcp__servername__promptname`. Verify these conventions against the installed Claude Code version.

## Claude Desktop

The Claude Desktop guide focuses on local STDIO. Its audited revision describes native remote-server support as beta for Pro, Max, Team, and Enterprise users as of June 2025; verify current availability instead of reusing that historical entitlement claim.

```bash
fastmcp install claude-desktop server.py:mcp
```

After installation, fully restart Claude Desktop and confirm the hammer/tool indicator appears.

Claude Desktop starts local servers in an isolated environment. Do not assume it inherits the interactive shell's variables, applications, or PATH. Pass required variables explicitly, keep `uv` visible system-wide, and on macOS prefer a globally visible installation such as Homebrew when the app cannot see a user-local binary.

The installed release resolves the config **directory** per platform and then appends `claude_desktop_config.json`:

| Platform | Config directory |
| --- | --- |
| macOS | `~/Library/Application Support/Claude` |
| Windows | `%USERPROFILE%\AppData\Roaming\Claude` |
| Linux | `$XDG_CONFIG_HOME/Claude`, defaulting to `~/.config/Claude` |
| anything else | Unsupported; resolution returns nothing and the install fails |

So the macOS file is `~/Library/Application Support/Claude/claude_desktop_config.json`. **Linux is supported** — this is not a macOS-and-Windows-only installer. There is **no** `~/.claude/claude_desktop_config.json`; that path belongs to Claude Code's unrelated `~/.claude.json`.

Two failure modes are worth distinguishing before debugging an install:

- **The directory must already exist.** Resolution returns nothing rather than creating it, and the installer prints "Claude Desktop config directory not found" followed by guidance to ensure Claude Desktop "has been run at least once to initialize its config". The real condition is therefore _never launched_, not _not installed_ — an installed-but-never-opened Claude Desktop fails here.
- **`--config-path` short-circuits platform detection entirely.** If the supplied directory does not exist, it reports "The specified config path does not exist" and stops; the generic not-found guidance above is suppressed because an explicit path was given. Use it for non-standard installations, and read the specific message to tell the two cases apart.

For manual configuration, edit the client-owned JSON and restart the app:

```json
{
  "mcpServers": {
    "dice-roller": {
      "command": "uv",
      "args": [
        "run",
        "--python",
        "3.11",
        "--project",
        "/absolute/path/project",
        "--with",
        "fastmcp",
        "fastmcp",
        "run",
        "/absolute/path/server.py:mcp"
      ],
      "env": { "API_KEY": "..." }
    }
  }
}
```

Argument order matters: Python and project selection, dependency options, then the launched command. Use absolute paths because the desktop app's working directory is not the project directory.

### Remote Servers and Proxies

Use native remote support when the user's client and plan provide it. Otherwise create a local STDIO proxy to the remote HTTP/SSE endpoint.

`create_proxy` is a **module-level function exported from `fastmcp.server`**. It is not a `FastMCP` method — `FastMCP.as_proxy` and `FastMCP.create_proxy` do not exist — and there is no `fastmcp.server.proxy` module to import from.

```python
from fastmcp.server import create_proxy

proxy = create_proxy("https://example.com/mcp", name="Remote Server Proxy")

if __name__ == "__main__":
    proxy.run()
```

The installed signature is:

```text
create_proxy(
    target: Client | ClientTransport | FastMCP | SDKServer | AnyUrl | Path | MCPConfig | dict | str,
    *,
    mode: str | None = None,
    **settings,
) -> FastMCPProxy
```

`mode` is the protocol-era knob; see [Protocol eras and sessions](protocol-eras-and-sessions.md) before pinning it. Everything else passes through as server settings.

For bearer authentication, construct a FastMCP `Client` with the installed auth helper and pass that client to `create_proxy`. Keep the token outside source, validate TLS and remote identity, and test transport loss. Confirm the auth helper's import and signature in the installed release; see [Auth, security, and deployment](auth-security-and-deployment.md).

## OpenAI Responses API

The FastMCP OpenAI guide documents remote MCP tools through the Responses API. It distinguishes Responses from the older Completions and Assistants APIs and notes that this adapter imports MCP tools, not resources or prompts. Other OpenAI surfaces evolve independently, so use [current official MCP and connector guidance](https://developers.openai.com/api/docs/guides/tools-connectors-mcp) for supported models and fields.

### Server and Request

Run a Streamable HTTP server, deploy it at an OpenAI-reachable HTTPS URL, and use the exact MCP path. The FastMCP page assumes a public deployment; current official OpenAI guidance also documents Secure MCP Tunnel for supported private or on-premises servers. Install the OpenAI Python SDK separately from FastMCP and provide `OPENAI_API_KEY` through the environment.

The FastMCP page's core MCP tool fields are:

| Field | Purpose |
| --- | --- |
| `type: 'mcp'` | Select the built-in MCP integration |
| `server_label` | Stable label used in list, call, and approval output items |
| `server_url` | Public Streamable HTTP or HTTP/SSE endpoint, including its deployed path |
| `require_approval` | The page uses `'never'`; current OpenAI docs also define approval flows and safer defaults |
| `headers` | Send remote-server headers such as bearer authentication when the current schema permits it |

Current official guidance also documents `server_description`, `allowed_tools`, `mcp_list_tools`, `mcp_call`, and `mcp_approval_request` / `mcp_approval_response`. Limit `allowed_tools`, keep the list-tools item in conversation state when appropriate to avoid repeated discovery, inspect call errors, and implement approvals rather than defaulting every server to `never`. Use a current compatible model from official docs instead of preserving the guide's illustrative `gpt-4.1` string.

### Authentication

The FastMCP page's authenticated OpenAI example demonstrates development JWTs with `RSAKeyPair.generate()`, a token whose audience matches the server, and `JWTVerifier(public_key=..., audience=...)`. `RSAKeyPair` and printing a token to the console are development and testing techniques only. Production should use the owning identity provider, rotation, expiry, issuer/audience checks, least-privilege scopes, and [Auth, security, and deployment](auth-security-and-deployment.md).

An unauthenticated request to a protected server surfaces as an OpenAI external-connector error; the page's example is API status 424 wrapping the remote server's HTTP 401. The page authenticates by sending `Authorization: Bearer <token>` through MCP tool headers. Current OpenAI APIs distinguish remote-server headers from the `authorization` field used by OAuth and connector flows; never send competing mechanisms without checking the current request schema.

Do not expose an unauthenticated production server through a development tunnel. Trust and review every remote MCP server, keep schemas free of secrets, filter imported tools, preserve approvals for sensitive data or side effects, and defend against prompt injection and cross-tool exfiltration.

## MCP JSON Configuration

The MCP JSON guide describes an emergent ecosystem format, not a protocol-level guarantee that every client interprets identically. Prefer a first-class installer when one exists; use MCP JSON for unsupported clients, CI/CD generation, team sharing, custom tooling, or deliberate manual setup.

### Standard Shape

Client files commonly wrap named launchers under `mcpServers`:

```json
{
  "mcpServers": {
    "server-name": {
      "command": "executable",
      "args": ["arg1", "arg2"],
      "env": { "VAR": "string-value" }
    }
  }
}
```

- `command` is required and must be an absolute executable path or discoverable on the client's PATH.
- `args` is optional and order-sensitive.
- `env` is optional and all values must be strings.

`fastmcp install mcp-json` prints only the named server object, **not** the outer `mcpServers` wrapper. Verified output:

```json
{
  "probe": {
    "command": "uv",
    "args": [
      "run",
      "--with",
      "fastmcp",
      "fastmcp",
      "run",
      "/absolute/path/server.py:mcp"
    ],
    "env": { "API_KEY": "x" }
  }
}
```

Merge that object into the client-owned wrapper. Generated entries use `uv run`, include FastMCP plus declared dependencies, and normalize file paths to absolute paths.

`fastmcp install stdio` emits the same launcher as a single shell-quoted command string rather than JSON:

```text
uv run --with fastmcp fastmcp run /absolute/path/server.py:mcp
```

### Generator Options

```bash
fastmcp install mcp-json server.py:mcp \
  --name "Production API" \
  --python 3.11 \
  --project /absolute/path/project \
  --with requests \
  --with-editable /absolute/path/local-package \
  --with-requirements /absolute/path/requirements.txt \
  --env API_BASE_URL=https://api.example.com \
  --env TIMEOUT=30
```

These flags demonstrate the inventory; combine dependency sources only when the owning environment requires them.

Prefer a checked-in, schema-pinned `fastmcp.json` for reproducible source and environment declarations:

```json
{
  "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
  "source": { "path": "server.py", "entrypoint": "mcp" },
  "environment": {
    "dependencies": ["requests"]
  }
}
```

Confirm every declarative field against the installed schema instead of assuming all examples combine in every release. See [CLI, testing, and migrations](cli-testing-and-migrations.md#fastmcpjson-ownership) for the full field inventory. Keep secrets out of the file.

### Pipelines, uv Projects, and Published Packages

- Redirect stdout to a file for pipelines, or parse the emitted JSON with `jq`. Treat stdout as secret-bearing if `--env` was used, and do not log or echo it in CI.
- For a uv-managed project, pass `--project .` or declare `environment.project` in `fastmcp.json`. Add packages beyond `pyproject.toml` through dependencies or `--with`.
- The generator emits `uv run` for local development.
- For a published package, manually use `uvx package-command`. If distribution and command names differ, use `uvx --from distribution command`. `uvx` also accepts `--python` and `--with` before the package command.

### Client Placement and Requirements

- Claude Desktop: merge into the platform config directory's `claude_desktop_config.json` (macOS `~/Library/Application Support/Claude/`); prefer the first-class installer for routine setup.
- Cursor: the first-class installer uses a deeplink; for manual placement merge into `~/.cursor/mcp.json`, or a workspace `.cursor/mcp.json`.
- Gemini CLI: `~/.gemini/settings.json`, or a project-level `.gemini/settings.json`.
- Goose: `config.yaml` under the Goose config directory, using its `extensions` schema rather than `mcpServers`.
- VS Code: merge into workspace `.vscode/mcp.json` while honoring VS Code's current schema.
- Custom applications: parse the standard shape only after defining supported transports, environment handling, working directory, and secret policy.

The generated `command` is `uv`; the wider emergent format permits other commands such as `python` or `uvx`. `uv` must be installed on the host and visible to the client. Prefer the current official uv installation instructions for the target platform.

## Integration Verification

1. Record Python, FastMCP, selected extras, lockfile, installed module path, and host/SDK version.
2. Confirm every import, signature, command, and flag in installed source or `--help`.
3. Inspect the exposed catalog through FastMCP Client, or `fastmcp list` / `fastmcp inspect`; verify names, schemas, annotations, tags, metadata, and exclusions.
4. Exercise the real transport and host or SDK, not only the underlying Python function.
5. Test missing capability, invalid input, auth rejection, application-authorization rejection, approval denial, callback failure, cancellation, timeout, and transport loss as relevant.
6. Test secret isolation and ensure generated configuration does not commit credentials.
7. Close clients, subprocesses, async HTTP clients, and server lifespan exactly once.

For OpenAPI, snapshot the intended catalog and representative upstream requests. For ChatGPT and OpenAI, verify tool filtering, approval behavior, and current search/fetch or request schemas. For Claude clients, restart or reload and exercise tools, resources, and prompts. For deeplink installers, confirm the receiving application actually registered the server. For MCP JSON, parse the final client file and launch the emitted command from the client's actual environment.

## Source Coverage Checklist

| Source | Topics represented above |
| --- | --- |
| OpenAPI | creation, `httpx2` client, upstream auth/timeout, ordered route maps, legacy semantic maps, exclusions and allow-listing, advanced mapper, names, all tag sources, client metadata, in-place customization, query/path/array/header handling |
| ChatGPT | remote deployment, Chat enrollment and per-chat enabling, Developer Mode/search-fetch distinction, read-only annotations, Deep Research tool shapes and usage |
| Claude Code | local STDIO focus, installer/object selection, dependency/project/Python/env options, manual registration/scopes, `~/.claude.json` layout, tools/resources/prompts usage |
| Claude Desktop | local/remote modes, installer/restart, per-platform config directory, dependency/project/Python/env options, isolated environment/uv visibility, argument order, proxy/bearer auth |
| `fastmcp install` | all seven targets, shared and per-target flags, `--name` vs stale `--server-name`, deeplink versus file-writing behavior, clipboard output |
| OpenAI API | Responses tool scope, deployment/SDK setup, MCP fields, development JWT verifier, client bearer header, authentication failure behavior |
| MCP JSON | emergent standard/fields, use cases, output wrapper, generator options, examples/pipelines, uv projects, `uvx`, client placement, format/runtime requirements |

Use [llms.txt](https://gofastmcp.com/llms.txt) to detect renamed or newly added integration pages, and refresh this checklist when any listed page changes materially.
