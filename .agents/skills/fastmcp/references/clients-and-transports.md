# Clients and Transports

## Purpose and Authority

Use this reference for FastMCP client construction, packaging, protocol-era selection, transports, operations, callbacks, response caching, roots, notifications, bearer authentication, and `fastmcp-remote`.

The live client guides linked in [Source Coverage](#source-coverage) provide current design guidance. Installed source and signatures remain authoritative for exact behavior; recheck both when the owning project moves its pin. For the baseline, see [Version and source routing](version-and-source-routing.md).

This is a programmatic, deterministic MCP client. It makes explicit operations and is a foundation for tests, applications, or agent hosts; it is not an autonomous agent by itself.

### What Changed From v3

A v3 client will not port unchanged. The behavioral deltas, all verified against installed source:

| Area | v3 | v4 |
| --- | --- | --- |
| Protocol models | `mcp.types` | `mcp_types` — `import mcp.types` raises `ModuleNotFoundError` |
| HTTP stack | `httpx` | `httpx2` — `import httpx` raises `ModuleNotFoundError` |
| Wire fields | camelCase | snake_case, with a warn-once read shim |
| Connection | `initialize` handshake always | Era negotiation; `mode="auto"` reaches a sessionless era with **no** `InitializeResult` |
| `Client.__init__` | 16 params | 22 params; six new, `sse_read_timeout` gone |
| `StreamableHttpTransport` | accepted `sse_read_timeout` | **removed** — raises `TypeError` |
| Background tasks | Client task API + task handles | Removed from the client entirely; see [Background tasks](tasks.md) |
| Response caching | none | Opt-in client cache (SEP-2549) |

The "introduced in" version landmarks in the live guides describe the pre-v4 line. They remain useful for reading old code and are useless for deciding what v4 offers — several landmark features, the client task API among them, were removed outright. Check the installed signature, not the landmark.

## Choose the Package

`fastmcp` is a **meta-package**. It contains no Python modules; it exists to pull `fastmcp-slim[client,server]` at a matching pin. All code — client and server alike — ships in `fastmcp-slim`.

That reframes the install choice. There is no separate "client-only distribution" with a different codebase; there is one wheel whose **extras** decide which optional dependencies come along:

```bash
pip install fastmcp                 # meta-package -> fastmcp-slim[client,server]
pip install "fastmcp-slim[client]"  # same code, client dependencies only
```

Use the narrower `fastmcp-slim[client]` when a library or application only connects to remote or subprocess MCP servers. It is a dependency-footprint decision, not a feature-parity one at the source level. The import namespace is `fastmcp` either way:

```python
from fastmcp import Client

client = Client("https://example.com/mcp")
```

A client-only dependency set supports HTTP, SSE, stdio, and a single-server MCP configuration. It does not support an in-memory `FastMCP` object or a multi-server configuration, because those paths construct server-side FastMCP objects and therefore need the server extra's dependencies.

Install provider sampling support only when the client owns that model route:

```bash
pip install "fastmcp-slim[client,openai]"
pip install "fastmcp-slim[client,anthropic]"
```

Declare the narrowest package and extras in the owner manifest and lockfile. Do not rely on a globally installed full distribution to hide an incomplete dependency declaration. Because `fastmcp` and `fastmcp-slim` are pinned to the same exact version by construction, they must be bumped together.

## Construct a Client

`Client` accepts a target and infers a transport, or accepts an explicit transport when configuration matters:

| Target | Inferred behavior | Use |
| --- | --- | --- |
| `FastMCP` instance | In-memory transport | Deterministic tests |
| HTTP(S) URL | Streamable HTTP by default | Remote service |
| Python or Node script path | stdio subprocess | Trusted local executable |
| `MCPConfig` or `{"mcpServers": ...}` | Direct single-server or composite transport | Existing MCP configuration |
| Explicit `ClientTransport` | Uses that transport unchanged | Auth, headers, TLS, environment, custom factory, or lifecycle control |

The constructor takes twenty-two parameters:

| Option | Use |
| --- | --- |
| `transport` | Target or explicit transport; required. |
| `name` | Stable client name for logs and diagnostics; generated when omitted. |
| `roots` | Static roots or a roots callback advertised to the server. |
| `sampling_handler` | Handles server-initiated model sampling. |
| `sampling_capabilities` | Narrows advertised sampling features, including tool support. |
| `elicitation_handler` | Handles structured or URL-mode user input requests. |
| `log_handler` | Receives MCP logging messages. |
| `message_handler` | Receives all server messages; use dedicated callbacks for interactions. |
| `progress_handler` | Default foreground progress callback. |
| `timeout` | Session read timeout as seconds, numeric duration, or `timedelta`. |
| `auto_initialize` | Negotiates on context entry; defaults to `True`. See the caveat below. |
| `init_timeout` | Separate initialization timeout; `0` disables it and `None` uses FastMCP settings. |
| `client_info` | MCP implementation metadata sent during initialization. |
| `auth` | `httpx2.Auth`, `"oauth"`, or a raw bearer token string. |
| `verify` | HTTP TLS verification: `bool`, CA-bundle path, or `ssl.SSLContext`. |
| `mode` | Protocol-era negotiation. **New.** |
| `prior_discover` | Adopt a previously obtained `DiscoverResult`. **New.** |
| `input_required_max_rounds` | Cap on `InputRequiredResult` retry rounds. **New.** |
| `cache` | Client-side response caching. **New.** |
| `extensions` | Opt-in client extensions. **New.** |
| `result_claims` | Additional result claims keyed by extension identifier. **New.** |

Note the `auth` type: it is `httpx2.Auth`, not `httpx.Auth`. Any custom auth object must subclass the `httpx2` class.

### The Six New Parameters

**`mode: ConnectMode = "auto"`** — `ConnectMode` is `Literal["legacy", "auto"] | str`. `"auto"` probes `server/discover` and negotiates the modern era, denylist-falling-back to the initialize handshake for any server that is not positive evidence of a modern peer; it is therefore safe against a mixed fleet. `"legacy"` forces the handshake, byte-identical to pre-v4 behavior. A modern version string such as `"2026-07-28"` adopts that version directly with no probe.

Verified against an in-memory FastMCP server: `mode="auto"` reports `protocol_version == "2026-07-28"`, `mode="legacy"` reports `"2025-11-25"`, and `mode="2026-07-28"` pins the modern era directly.

**`prior_discover: DiscoverResult | None = None`** — a previously obtained `DiscoverResult` to adopt when `mode` is a version pin, reused instead of synthesizing a minimal one. **Ignored otherwise**, including under the default `"auto"`. Passing it without a version pin does nothing.

**`input_required_max_rounds: int = 10`** — cap on `InputRequiredResult` (SEP-2322) retry rounds for `call_tool`, `get_prompt`, and `read_resource` before the driver gives up. Only reachable on 2026-era servers that emit `InputRequiredResult` (the type lives in `mcp_types`). A guard tool that keeps requesting input will consume rounds until this cap; size it against the deepest legitimate interaction the application expects, not against the worst case a server could invent.

**`cache: CacheConfig | bool | None = None`** — opt-in client response cache. See [Cache Responses](#cache-responses).

**`extensions: Sequence[ClientExtension] | None = None`** — opt-in client extensions (SEP-2133), instances of `mcp.client.extension.ClientExtension`. Each contributes its capability advertisement, its result claims, and its notification bindings, all threaded into the underlying session. User-supplied notification bindings **compose with** FastMCP's internal bindings rather than replacing them. A claimed `call_tool` result is resolved transparently through the owning extension's resolver. For an advertise-only entry use `mcp.client.advertise(identifier, settings=None)`.

**`result_claims: Mapping[str, Sequence[ResultClaim]] | None = None`** — additional `ResultClaim`s (SEP-2133) keyed by the identifier of an extension already advertised through `extensions`, merged with that extension's own claims. Rarely needed directly; prefer declaring claims on the extension itself. Claimed shapes are modern-only and inert on a legacy connection.

## Connect and Read Server Metadata

All normal operations require an active async context. Context entry connects and negotiates; exit releases the client connection. A stdio transport with `keep_alive=True` may intentionally retain its subprocess for later contexts, but the client session still has context scope.

Under the default `mode="auto"` against a FastMCP server there is **no initialize handshake and no `InitializeResult`**. Read metadata off the client:

```python
from fastmcp import Client

async with Client("https://example.com/mcp", timeout=30) as client:
    print(client.protocol_version)      # e.g. "2026-07-28"
    print(client.server_info.name)      # snake_case
    print(client.server_capabilities)
    print(client.instructions)
    await client.list_tools()           # liveness check — see below, not ping()
```

### The `initialize()` Trap

Three idioms that were correct in v3 are broken under the v4 default. All were verified against an in-memory FastMCP server in both modes.

**`client.initialize()` raises.** Under a negotiated modern era it fails rather than returning:

```
RuntimeError: The client negotiated a modern protocol era (server/discover), which has
no InitializeResult. Inspect client.protocol_version, client.server_info,
client.server_capabilities, and client.instructions for the metadata available in this
mode, or construct the client with mode='legacy'.
```

**`client.initialize_result` is `None`.** Any code shaped like `info = client.initialize_result; assert info is not None` fails on the assertion, and `info.serverInfo` compounds the problem with a deprecated camelCase read.

**`client.ping()` raises `MCPError: Method not found`.** This one is easy to miss because it is not an era-awareness error — it reads like a broken server.

It is not a missing handler. `ping` **is** registered — `"ping" in server._mcp_server._request_handlers` is `True`. The method was removed from the `2026-07-28` protocol itself. `mcp_types.methods.CLIENT_REQUESTS` is keyed by `(method, protocol_version)`, and the pair simply does not exist:

```python
from mcp_types.methods import CLIENT_REQUESTS
("ping", "2025-11-25") in CLIENT_REQUESTS   # True
("ping", "2026-07-28") in CLIENT_REQUESTS   # False
```

`client.set_logging_level()` fails the same way and for the same reason. The full era delta, and the complete list of what a modern client may call, is owned by [Protocol eras and sessions](protocol-eras-and-sessions.md).

Because the cause is a protocol method table rather than server wiring, this is **transport-independent by construction** — the table is consulted regardless of transport. The table below was observed in-memory, but the behavior does not depend on that.

| Operation | `mode="auto"` | `mode="legacy"` |
| --- | --- | --- |
| `ping()` | `MCPError: Method not found` | OK |
| `set_logging_level()` | `MCPError: Method not found` | OK |
| `list_tools()`, `call_tool()`, `read_resource()`, `get_prompt()` | OK | OK |

Use `await client.list_tools()` as the liveness check instead. It exercises connect, negotiation, and a real round-trip, which is what a smoke test actually wants. Reserve `ping()` for a connection explicitly pinned to `mode="legacy"`.

The rows above were observed over the in-memory transport, but the outcome follows from the era's method table rather than from transport wiring, so it holds for HTTP and SSE as well. The cheap check is the membership test — `("ping", client.protocol_version) in CLIENT_REQUESTS` — which needs no live connection at all.

### `auto_initialize=False` Is a Second Dead End

`auto_initialize=False` is not a workaround for the era change. Under the default `mode="auto"` it is a way to get stuck, because both exits are closed: nothing negotiates on its own, and the documented escape hatch raises.

**There is no lazy negotiation.** `auto_initialize=False` defers negotiation — `protocol_version` reads `None` immediately after connect — and it stays deferred. Ordinary operations then fail:

```python
# Broken under mode="auto"
async with Client(target, auto_initialize=False) as client:
    await client.call_tool("add", {"a": 1, "b": 2})
    # MCPError: Invalid request parameters
```

`Client.initialize`'s own docstring is explicit that manual initialization is required, not optional: _"Manual calls to this method are only needed when auto-initialization is disabled."_ But on a modern connection that manual call raises. The full matrix, executed against an in-memory FastMCP server:

| `mode` | Manual `initialize()`? | Result |
| --- | --- | --- |
| `"auto"` | no | `MCPError: Invalid request parameters` |
| `"auto"` | yes | `RuntimeError` — modern era has no `InitializeResult` |
| `"legacy"` | no | `MCPError: Invalid request parameters` |
| `"legacy"` | yes | **OK** — `protocol_version == "2025-11-25"` |

Only the last row works. `auto_initialize=False` is usable exclusively with `mode="legacy"` **and** an explicit `initialize()`.

One sharp edge is worth knowing, because it will otherwise make a debugging session lie to you. `initialize()` negotiates _before_ it checks for an `InitializeResult`, so the call that raises `RuntimeError` has already completed negotiation as a side effect. `protocol_version` flips from `None` to `"2026-07-28"` across the failed call, and subsequent operations succeed:

```python
async with Client(target, auto_initialize=False) as client:
    assert client.protocol_version is None
    try:
        await client.initialize()
    except RuntimeError:
        pass                                  # negotiation already happened
    assert client.protocol_version == "2026-07-28"
    await client.call_tool("add", {"a": 1, "b": 2})   # now works
```

Do not write that. It is shown only so a swallowed-exception idiom in existing code is recognizable for what it is, and so a probe that calls `initialize()` before testing anything else is not mistaken for evidence of lazy negotiation. If a client needs the modern era, let `auto_initialize` default to `True`.

### When You Actually Need the Handshake

`mode="legacy"` is the deliberate exit from every trap above, and the only one. Reach for it when an application genuinely needs the handshake result object — a compatibility shim, a conformance test, an audit record of the negotiated `InitializeResult` — and say so at the call site, because the choice pins a protocol era:

```python
async with Client(target, mode="legacy") as client:
    result = await client.initialize()
    print(result.protocol_version, result.server_info.name)
```

`initialize()` remains idempotent and returns the cached result after the first successful handshake. Inspect negotiated capabilities before relying on sampling, elicitation, roots, list-change notifications, or other optional MCP behavior. Use `client.new()` when concurrent work needs an independent session with the same configuration.

The full era model — what changes on the server side, how sessions and state behave, and which `Context` methods are era-conditional — is in [Protocol eras and sessions](protocol-eras-and-sessions.md). This page covers only what a client author must decide.

## Rename Wire Fields

MCP SDK v2 renamed protocol fields from camelCase to snake_case. `mcp_camelcase_compat` defaults to `True`, which installs warn-once `@property` shims routing a camelCase read to its snake_case attribute, so stale code still runs while emitting one `FastMCPDeprecationWarning` per `(class, name)`.

Client-visible renames:

| Model | camelCase | snake_case |
| --- | --- | --- |
| `CallToolResult` | `isError` | `is_error` |
| `CallToolResult` | `structuredContent` | `structured_content` |
| `InitializeResult` | `serverInfo` | `server_info` |
| `InitializeResult` | `protocolVersion` | `protocol_version` |
| `ListToolsResult` | `nextCursor` | `next_cursor` |
| `ListResourcesResult` | `nextCursor` | `next_cursor` |
| `ListPromptsResult` | `nextCursor` | `next_cursor` |
| `ListResourceTemplatesResult` | `nextCursor`, `resourceTemplates` | `next_cursor`, `resource_templates` |
| `Tool` | `inputSchema`, `outputSchema` | `input_schema`, `output_schema` |
| `ToolAnnotations` | `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` | `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint` |
| `Resource`, `ResourceTemplate`, `TextResourceContents`, `BlobResourceContents`, `ImageContent`, `AudioContent` | `mimeType` | `mime_type` |
| `ResourceTemplate` | `uriTemplate` | `uri_template` |
| `Completion` | `hasMore` | `has_more` |
| `CreateMessageRequestParams` | `systemPrompt`, `maxTokens`, `stopSequences`, `modelPreferences`, `toolChoice` | `system_prompt`, `max_tokens`, `stop_sequences`, `model_preferences`, `tool_choice` |
| `ElicitRequestFormParams` | `requestedSchema` | `requested_schema` |

Three caveats, each verified:

- **The shim is read-only and incomplete.** `CreateMessageRequestParams.includeContext` is **not** in the alias table, so `params.includeContext` raises `AttributeError` even with compat enabled while its five siblings merely warn. Read `params.include_context`. Do not assume "compat is on" means every stale name is covered.
- **The shim requires importing `fastmcp`.** It is installed as a side effect of `fastmcp/_compat.py`. A test helper that imports only `mcp_types` gets no shim and fails hard on camelCase reads.
- **Warn-once hides the rest.** The second read through the same shim is silent, so warnings alone cannot prove a migration is complete. Set `fastmcp.settings.mcp_camelcase_compat = False` (or export `FASTMCP_MCP_CAMELCASE_COMPAT=false`) — the shim re-reads the setting on every access, so camelCase reads immediately raise `AttributeError` instead. Use that as the migration gate.

Construction is unaffected: pydantic validation aliases still accept `Tool(name="x", inputSchema={...})` without warning. Only attribute _reads_ go through the shim.

FastMCP's own high-level `CallToolResult` was always snake_case and is unchanged: `content`, `structured_content`, `meta`, `data`, `is_error`.

## Select and Configure a Transport

| Need                                          | Preferred transport         |
| --------------------------------------------- | --------------------------- |
| Fast, deterministic protocol test             | In-memory FastMCP transport |
| Trusted local command                         | stdio                       |
| Production network service                    | Streamable HTTP over TLS    |
| Legacy endpoint that only supports SSE        | SSE compatibility transport |
| Existing one- or multi-server config          | MCP config transport        |
| stdio-only host connecting to remote HTTP/SSE | `fastmcp-remote`            |

Prefer an explicit transport whenever environment, working directory, process reuse, stderr routing, headers, auth, TLS, or HTTP-client behavior matters. The installed transports are `StdioTransport`, `PythonStdioTransport`, `NodeStdioTransport`, `NpxStdioTransport`, `UvStdioTransport`, `UvxStdioTransport`, `FastMCPStdioTransport`, `StreamableHttpTransport`, `SSETransport`, `FastMCPTransport`, and `MCPConfigTransport`.

### stdio

`StdioTransport` owns the subprocess lifecycle:

```python
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

transport = StdioTransport(
    command="python",
    args=["server.py", "--verbose"],
    env={"SERVICE_MODE": "test"},
    cwd="/absolute/path/to/server",
    keep_alive=False,
    log_file=Path("/absolute/path/to/server.stderr.log"),
)

async with Client(transport) as client:
    await client.list_tools()
```

`command` and `args` are separate; never interpolate untrusted data through a shell. `env` is the complete explicit server environment expected by the MCP SDK path, so selectively forward owned variables or load an owner-approved `.env` mapping. Do not forward the entire host environment by default. `cwd` controls relative server paths. `log_file` redirects subprocess stderr to a `Path` or text stream; protocol messages must remain isolated on stdout.

`keep_alive` is typed `bool | None = None` and resolves to `True` when omitted. It reuses the same subprocess across client contexts and therefore may retain process and server state. Set it to `False` when each connection needs isolation. Explicitly close long-lived transports at application shutdown.

The live client overview shows `Client("server.py", env={...})`, but the `Client` constructor has no `env` parameter. Use `StdioTransport` for environment configuration. File-path inference is appropriate only when defaults are sufficient.

### Streamable HTTP

Use Streamable HTTP for new remote deployments:

```python
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

transport = StreamableHttpTransport(
    url="https://api.example.com/mcp",
    headers={"X-Client-Name": "reporting-service"},
    auth="token-without-bearer-prefix",
    verify="/etc/ssl/certs/internal-ca.pem",
)

async with Client(transport, timeout=60) as client:
    await client.list_tools()
```

The full parameter list is `url`, `headers`, `auth`, `httpx_client_factory`, `verify` — five, with no timeout among them. Pass the complete MCP endpoint, including `/mcp` or any required trailing slash; FastMCP preserves the supplied path exactly. Configure:

- `headers` for bounded non-secret routing metadata or custom auth schemes;
- `auth` as an `httpx2.Auth`, `"oauth"`, or raw bearer token string;
- `verify` as `None`/`True`, a CA-bundle path, an `SSLContext`, or `False` only for a controlled development exception;
- `httpx_client_factory` when the owner must control proxy, timeouts, connection limits, certificates, or other HTTP behavior.

If both `httpx_client_factory` and `verify` are supplied, the custom factory owns verification and a warning is emitted. The callable must accept `headers`, `auth`, `follow_redirects`, and, for forward compatibility, arbitrary supported keyword arguments.

**`sse_read_timeout` is removed from `StreamableHttpTransport`, not deprecated.** It raises:

```
TypeError: StreamableHttpTransport.__init__() got an unexpected keyword argument 'sse_read_timeout'
```

Two replacements, depending on what the timeout was doing:

```python
# Session read timeout — the usual intent
async with Client(transport, timeout=300) as client: ...

# Full control over the HTTP stack
transport = StreamableHttpTransport(url=..., httpx_client_factory=owned_factory)
```

A migration that only silences a deprecation warning is not enough here; the keyword must be deleted from the call site.

Require HTTPS outside trusted local development. Keep certificate verification enabled, bound redirects and response sizes in the owned HTTP client, and test the real network or ASGI path for production-sensitive behavior.

### SSE

`SSETransport` has the same URL, header, auth, custom-factory, and verification controls, and it **does** still accept `sse_read_timeout` — it is the one transport where the keyword survives, because a long-lived SSE read is exactly what it bounds. Use it only when a legacy server or infrastructure requirement does not support Streamable HTTP:

```python
from fastmcp import Client
from fastmcp.client.transports import SSETransport

transport = SSETransport(
    url="https://legacy.example.com/sse",
    auth="token-without-bearer-prefix",
    sse_read_timeout=300,
)

async with Client(transport) as client:
    await client.list_tools()
```

### In-Memory

An in-memory client connects directly to a `FastMCP` instance in one Python process. It exercises MCP serialization, capability negotiation, and dispatch without sockets or subprocesses:

```python
from fastmcp import Client, FastMCP

server = FastMCP("test-server")

@server.tool
def add(left: int, right: int) -> int:
    return left + right

async with Client(server) as client:
    result = await client.call_tool("add", {"left": 2, "right": 3})
    assert result.data == 5
```

This path shares memory and environment with the test process. It does not prove subprocess environment isolation, stdout discipline, TLS, proxy, origin, reconnect, or deployed session behavior. It needs the server dependency set.

Note that an in-memory client also negotiates the modern era under the default `mode="auto"`, so it is a faithful place to catch the `initialize()` trap before deployment — and equally a place where a legacy-only assumption will pass tests it should fail. Pin `mode` explicitly when the test is about era behavior.

### MCP Configuration and Multiple Servers

FastMCP accepts the common `mcpServers` configuration shape even though MCP does not standardize one configuration-file format:

```python
config = {
    "mcpServers": {
        "weather": {"url": "https://weather.example.com/mcp"},
        "assistant": {
            "command": "python",
            "args": ["assistant.py"],
            "env": {"LOG_LEVEL": "INFO"},
        },
    }
}
```

A single server delegates directly to its transport and works with the client dependency set. Multiple servers need the server dependency set and create a composite FastMCP server. By default:

- tools use `{server_name}_{tool_name}`;
- resources insert the server name into the URI, such as `weather://weather/icons/sunny`;
- prompts must be addressed by the names returned from `list_prompts()`; default namespacing commonly yields names such as `weather_prompt` and `assistant_prompt`.

Do not construct names from memory. Inspect the post-composition catalog, because namespaces, transforms, duplicate handling, and server failures affect what is actually exposed.

Configuration ownership differs by entry type. A `command` entry launches a process, so its `env` mapping becomes that subprocess environment. A remote `url` entry configures the client transport (URL, HTTP/SSE selection, headers and auth, timeouts, metadata, tag filters, and tool transforms). It does not inject environment variables into an already-running remote process; configure that process through its launcher or deployment environment. An `env` key on a remote URL entry is ignored when building the transport.

Per-server `tools` configuration can rename a tool, replace its description, add tags, set argument defaults, and hide arguments. Apply `include_tags` after transformations and `exclude_tags` for unwanted tags. Treat hidden or defaulted arguments as client presentation, not authorization; the upstream server still owns validation and permission checks.

## Discover Components and Pagination

High-level list methods automatically follow cursors and return all discovered items:

```python
tools = await client.list_tools()
resources = await client.list_resources()
templates = await client.list_resource_templates()
prompts = await client.list_prompts()
```

Each accepts `max_pages=250` by default, stops on a duplicate cursor, and raises if the maximum is exhausted. Use the corresponding raw methods for incremental pagination:

```python
page = await client.list_tools_mcp(cursor=None)
next_cursor = page.next_cursor          # snake_case
```

The raw methods also take `cache_mode: CacheMode = "use"`; see [Cache Responses](#cache-responses). Equivalent raw list methods exist for resources, resource templates, and prompts. Inspect annotations, schemas, capabilities, and versions before invoking components. Do not treat discovery visibility as authorization.

## Call Tools

`call_tool()` executes a named server tool with a dictionary of arguments:

```python
from fastmcp.exceptions import ToolError

try:
    result = await client.call_tool(
        "calculate_total",
        {"items": [10, 20]},
        version="2.0",
        timeout=15,
        progress_handler=on_progress,
        meta={"trace_id": "trace-123"},
    )
except ToolError as exc:
    handle_tool_failure(str(exc))
```

The options are:

| Option | Behavior |
| --- | --- |
| `name` | Advertised tool name; multi-server names may be prefixed. |
| `arguments` | Tool input mapping; `None` becomes `{}`. |
| `version` | Requests a specific FastMCP component version; omitted selects the highest available. |
| `timeout` | Per-call read timeout. |
| `progress_handler` | Per-call override of the client progress handler. |
| `raise_on_error` | Defaults to `True`; raises `ToolError` for a tool-level failure. |
| `meta` | Ancillary request metadata for tracing, preferences, or client context. |

There is no `task`, `task_id`, or `ttl`. The v3 client task keywords were removed along with the rest of the client task API.

`meta` is not a tool argument, authenticated identity, authorization claim, or secret channel. Treat it as untrusted ancillary input at the server.

The high-level result is `fastmcp.client.client.CallToolResult`, a dataclass:

| Property | Meaning |
| --- | --- |
| `.data` | Hydrated Python output validated from the advertised output schema. |
| `.content` | Standard MCP text, image, audio, embedded-resource, or other content blocks. |
| `.structured_content` | Raw structured JSON from the MCP result. |
| `.meta` | Result metadata. |
| `.is_error` | Tool-level failure flag. |

FastMCP unwraps primitive server results from the framework's `{"result": value}` envelope, so a tool returning `5` yields `.data == 5` alongside `.structured_content == {"result": 5}`. `.data` can be `None` when there is no output schema or hydration fails; then inspect `.structured_content` and typed content blocks instead of assuming text. Never select only `content[0]` unless the tool contract guarantees one text block.

Set `raise_on_error=False` only when the caller deliberately branches on `.is_error`. For raw MCP behavior use `call_tool_mcp()`, which returns `mcp_types.CallToolResult`, does no FastMCP hydration, and does not raise for a tool-level error. Read its fields as `is_error` and `structured_content`.

## Read Resources

Use `read_resource(uri)` for both static resources and resolved resource-template URIs:

```python
contents = await client.read_resource("weather://london/current", version="1.0")

for item in contents:
    if hasattr(item, "text"):
        consume_text(item.text, item.mime_type)
    else:
        consume_base64(item.blob, item.mime_type)
```

The result is a list of `TextResourceContents` or `BlobResourceContents`. `.blob` is typed `str` and carries base64, not decoded bytes. Decode it with `base64.b64decode()` before writing a binary file, validate the MIME type and size, and use an owner-approved destination. A direct `file.write(item.blob)` is not valid for this type.

`read_resource()` accepts `version` and `meta` only — no task keywords. An omitted version selects the highest available FastMCP version. Use `read_resource_mcp()` for the full `mcp_types.ReadResourceResult` and raw metadata; it also accepts `cache_mode`.

For multi-server clients, use the URI returned by `list_resources()` or instantiate a template URI from the advertised template. Do not synthesize path prefixes without checking the composed catalog. Treat resource content as untrusted input, bound binary and text sizes, and authorize local persistence separately.

## Get Prompts

Use `get_prompt()` to render a server-defined message template:

```python
result = await client.get_prompt(
    "summarize",
    {"document": {"title": "Quarterly report", "pages": 12}},
    version="2.0",
)

for message in result.messages:
    consume_prompt_message(message.role, message.content)
```

FastMCP leaves string arguments unchanged and serializes non-string values to JSON with `pydantic_core.to_json()`. A FastMCP server can deserialize those strings into its declared types. Do not assume a non-FastMCP server applies the same hydration; the MCP wire contract carries prompt arguments as strings.

`GetPromptResult` can contain multiple user or assistant messages and different MCP content block types. Preserve roles, order, and content types. Treat server prompt text as data for the owning application, not as automatically trusted system instructions.

`get_prompt()` accepts `version` and `meta` only. Use `get_prompt_mcp()` for explicit raw protocol access. In a multi-server client, call the exact prompt name returned by `list_prompts()`.

## Cache Responses

Client-side response caching (SEP-2549) is opt-in and off by default. `None` and `False` disable it; `True` enables a per-client in-memory store honoring the server's `ttlMs` and `cacheScope` hints; a `CacheConfig` customizes it.

**Honoring is modern-only.** A cache is inert on a legacy connection, so `mode="legacy"` and `cache=True` together do nothing.

The types are split across two packages. `CacheMode`, `CacheConfig`, `CacheEntry`, `CacheKey`, `ResponseCacheStore`, `InMemoryResponseCacheStore`, and `MAX_TTL_MS` live in `mcp.client.caching`. FastMCP contributes `KeyValueResponseCacheStore` and `DEFAULT_CACHE_COLLECTION` in `fastmcp.client.caching`.

```python
from fastmcp import Client

async with Client(target, cache=True) as client:
    await client.list_tools()
```

`CacheMode = Literal["use", "refresh", "bypass"]` is the per-call knob on the raw methods: `"use"` serves and stores, `"refresh"` stores without serving, `"bypass"` skips the cache entirely. It defaults to `"use"`.

`CacheConfig` fields:

| Field | Meaning |
| --- | --- |
| `store` | Backing store; `None` means a per-client `InMemoryResponseCacheStore`. |
| `partition` | Authorization-context identifier isolating `"private"` entries within a shared store. |
| `target_id` | Server-identity override for custom transports and proxies. |
| `default_ttl_ms` | TTL for results carrying no `ttlMs` hint; the default `0` leaves them uncached. |
| `clock` | Wall-clock source returning epoch seconds; injectable for expiry tests. |
| `share_public` | Serve server-marked `"public"` entries across every partition. |

Three constraints are enforced in `__post_init__` and raise `ValueError`: a custom `store` requires a non-empty `partition`, `target_id` must be non-empty when provided, and `default_ttl_ms` must be non-negative. Verified:

```python
CacheConfig(store=KeyValueResponseCacheStore())
# ValueError: a custom store requires an explicit partition
```

FastMCP additionally requires `target_id` on a custom store, because FastMCP transports expose no server URL to derive a shared-store identity from.

For a shared backend across client replicas — a proxy fleet, for instance — use the key-value adapter:

```python
from fastmcp import Client
from fastmcp.client.caching import KeyValueResponseCacheStore
from mcp.client.caching import CacheConfig
from key_value.aio.stores.redis import RedisStore

store = KeyValueResponseCacheStore(storage=RedisStore(url="redis://localhost"))
config = CacheConfig(store=store, partition="tenant-a", target_id="weather-api")
client = Client("https://example.com/mcp", cache=config)
```

Security properties worth stating explicitly:

- **Derive `partition` from a verified credential**, never from request-supplied data or the server URL. It is folded into every stored key and is what prevents cross-principal leakage. It is fixed for the client's lifetime: construct a new `Client` when the principal changes.
- **`share_public=True` trusts the server's classification.** A mislabeled `"public"` response leaks across every tenant sharing the store. It is constructor-level only; a per-call `cache_mode` can never widen sharing.
- Each adapter instance owns one collection (`DEFAULT_CACHE_COLLECTION` is `"fastmcp_response_cache"`), so `clear()` never reaches beyond its own namespace. Against a backend supporting neither collection destruction nor key enumeration, `clear()` is a no-op and entries age out by TTL, with a warning logged once.
- Entries are round-tripped through a type-tagged envelope validated against an allowlist of cacheable result models — `DiscoverResult`, `ListToolsResult`, `ListResourcesResult`, `ListResourceTemplatesResult`, `ListPromptsResult`, `ReadResourceResult`. A stored value naming an unknown type is treated as a miss, never imported by name. **`tools/call` results are not cacheable.**
- `MAX_TTL_MS` caps any entry at 24 hours; larger `ttlMs` values are clamped down.
- Store operations may raise; the SDK degrades to a miss rather than failing the call.

## Use Background Tasks

The client background-task API was removed in v4. Task execution now lives behind a separately installed extension. See [Background tasks](tasks.md).

## Handle Sampling Requests

Sampling lets a server ask the connected client to perform an LLM call. The client controls the provider, credentials, model policy, cost, and data boundary.

```python
from fastmcp import Client
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams

async def sampling_handler(
    messages: list[SamplingMessage],
    params: SamplingParams,
    context: RequestContext,
) -> str:
    return await owned_model_gateway.generate(
        messages=messages,
        system_prompt=params.system_prompt,
        max_tokens=params.max_tokens,
    )

client = Client(target, sampling_handler=sampling_handler)
```

`SamplingParams` is `mcp_types.CreateMessageRequestParams` and all of its fields are snake_case. A handler may be synchronous or async and may return a string, `CreateMessageResult`, or `CreateMessageResultWithTools`. A string becomes an assistant text response. Preserve supported text, image, audio, tool-use, and tool-result blocks instead of flattening them accidentally.

Inspect every requested parameter:

| Field | Guidance |
| --- | --- |
| `messages` | Ordered user/assistant MCP messages. Bound size and remove data outside the approved model boundary. |
| `system_prompt` | Server-requested instructions; apply owner policy and do not let it override client security controls. |
| `model_preferences` | Hints for name, cost, speed, and intelligence; the client retains model choice. |
| `include_context` | Requested context scope (`none`, `thisServer`, or `allServers`); include only policy-approved data. **No camelCase shim exists for this field.** |
| `temperature` | Provider sampling hint. Validate against the chosen provider. |
| `max_tokens` | Required output cap; also enforce client-side budget and timeout. |
| `stop_sequences` | Optional provider stop sequences. |
| `metadata` | Untrusted sampling metadata. |
| `tools` | Tools the model may request. Validate schemas and allowlist the surface. |
| `tool_choice` | `auto`, `required`, or `none` behavior when tools are present. |

Built-in provider handlers require their extras, which **this repository does not declare** — treat their behavior as upstream-documented:

| Handler | Extra | Constructor controls |
| --- | --- | --- |
| `OpenAISamplingHandler` | `openai` | `default_model`, optional `AsyncOpenAI` client, including an owned compatible `base_url` |
| `AnthropicSamplingHandler` | `anthropic` | `default_model`, optional `AsyncAnthropic` client |

Use an owner-configured model ID rather than copying a documentation example. Verify the owning project actually declares the provider extra and SDK before importing a handler; credentials, model selection, privacy boundary, timeout, retries, and billing owner still require explicit application configuration.

Providing a handler advertises sampling. An omitted `sampling_capabilities` becomes an empty `SamplingCapability`, which advertises no optional context or tool sub-capability. Explicitly advertise only what the handler implements; `SamplingCapability()` disables sampling-tool support even when the handler can generate text.

When the server supplies sampling tools, the client handler passes them to its model and returns requested tool-use blocks; the server executes the actual sampling tools and may issue follow-up sampling requests with results. The client must not execute arbitrary tool calls merely because the sampled model requested them.

## Handle Elicitation Requests

Elicitation lets a server request structured user input while an operation is active:

```python
from fastmcp.client.elicitation import ElicitRequestParams, ElicitResult, RequestContext

async def elicitation_handler(
    message: str,
    response_type: type | None,
    params: ElicitRequestParams,
    context: RequestContext,
) -> ElicitResult | object:
    decision = await user_interface.ask(message, params)
    if decision.cancelled:
        return ElicitResult(action="cancel")
    if decision.declined:
        return ElicitResult(action="decline")
    return response_type(**decision.values) if response_type else {}
```

`ElicitRequestParams` is the union `ElicitRequestURLParams | ElicitRequestFormParams`. FastMCP converts form-mode `requested_schema` into a Python dataclass type. `response_type` is `None` for URL-mode elicitation and for an empty object schema. Inspect `params` to distinguish modes. Do not call `input()` inside production async code; bridge to the host's owned UI or non-interactive policy.

Returning data directly implicitly accepts. Return `ElicitResult` for explicit control:

- `accept` includes validated `content`;
- `decline` means the user chose not to provide the data;
- `cancel` aborts the containing operation.

The accepted wire value must serialize to a JSON object. FastMCP wraps a scalar only for a generated one-field `value` schema. Use the provided dataclass or validate against `params.requested_schema`; never echo arbitrary user data without validation. Treat decline and cancel as different outcomes, revalidate authorization and stale mutable state after an interactive pause, and never use elicitation to collect credentials that belong in authentication or configuration.

Elicitation is unavailable inside a background task; see [Background tasks](tasks.md).

## Monitor Foreground Progress

Configure a client-level callback or override it for one tool call:

```python
async def progress_handler(
    progress: float,
    total: float | None,
    message: str | None,
) -> None:
    if total not in (None, 0):
        render_percent(progress / total * 100, message)
    else:
        render_indeterminate(progress, message)

client = Client(target, progress_handler=progress_handler)
```

The handler receives the current value, optional total, and optional message. Avoid division by zero, do not assume a total exists, throttle expensive UI work, and preserve cancellation. A per-call `call_tool(..., progress_handler=...)` overrides the client handler for that operation.

FastMCP installs a default progress handler that logs at debug level. Foreground progress notifications require a progress token on the request.

## Receive Server Logs

Provide an async `log_handler` when the application needs to route MCP logs:

```python
import logging

from fastmcp.client.logging import LogMessage

logger = logging.getLogger("mcp.server")

async def log_handler(message: LogMessage) -> None:
    logger.info(
        "MCP log",
        extra={
            "mcp_level": message.level,
            "mcp_logger": message.logger,
            "mcp_data": redact(message.data),
        },
    )
```

`LogMessage` is `mcp_types.LoggingMessageNotificationParams` with fields `data`, `level`, `logger`, and `meta`. MCP levels are `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, and `emergency`. The default FastMCP handler maps `notice` to Python `INFO` and `alert`/`emergency` to `CRITICAL`.

The live logging page presents `message.data` as a dictionary with `msg` and `extra`, and FastMCP server helpers normally emit that shape, but the installed type permits any JSON-serializable data. Branch by type before accessing keys. Keep logs payload-free by default; redact arguments, resource data, elicitation responses, sampled conversations, tokens, cookies, and sensitive metadata.

## Advertise Roots

Roots tell a server which client-controlled filesystem locations are relevant. They are capability hints, not a sandbox or an authorization grant.

Static roots accept strings and/or `mcp_types.Root` values. A string must already be a valid `file://` URL; plain filesystem strings such as `/workspace/project` fail `pydantic.FileUrl` validation with a `ValidationError` at connect time. Convert owned paths with `Path.resolve().as_uri()` or construct an explicit `Root`:

```python
from pathlib import Path

from fastmcp import Client

client = Client(
    target,
    roots=[
        Path("/workspace/project").resolve().as_uri(),
        Path("/workspace/shared").resolve().as_uri(),
    ],
)
```

A dynamic callback may be synchronous or async and receives a client `RequestContext`:

```python
from fastmcp.client.roots import RequestContext

async def roots(context: RequestContext) -> list[str]:
    return await policy.allowed_roots_for(context.request_id)

client = Client(target, roots=roots)
```

The live guide describes strings as filesystem paths, but the installed code validates them directly as file URLs instead of converting plain paths. Expose the narrowest canonical directory set, exclude secrets and unrelated home directories, and make the server authorize and contain every actual read. `client.set_roots(...)` replaces the callback but does not automatically send `roots/list_changed`; call `await client.send_roots_list_changed()` when a connected server must refresh.

## Handle Notifications

Use a message function for simple filtering or subclass `MessageHandler` for typed hooks. Import notification types from `mcp_types`:

```python
import mcp_types
from fastmcp.client.messages import MessageHandler

class CatalogHandler(MessageHandler):
    async def on_tool_list_changed(
        self,
        message: mcp_types.ToolListChangedNotification,
    ) -> None:
        tool_cache.clear()

    async def on_resource_updated(
        self,
        message: mcp_types.ResourceUpdatedNotification,
    ) -> None:
        resource_cache.invalidate(str(message.params.uri))

client = Client(target, message_handler=CatalogHandler())
```

Beyond tool, resource, and prompt list changes plus progress and logging, hooks exist for all messages, server requests, ping, roots requests, sampling requests, all notifications, exceptions, resource updates, and cancellation. Keep handlers quick, idempotent, and failure-contained. Invalidate or re-list a catalog after a list-change event; do not mutate a shared cache halfway through readers.

Use `sampling_handler`, `elicitation_handler`, `progress_handler`, and `log_handler` for those interactions. A generic `message_handler` is primarily for observation and notification handling; it must not replace the dedicated response path.

Notification bindings supplied through `extensions` compose with FastMCP's internal bindings rather than replacing them. That composition guarantee applies to the `extensions` parameter, not to a `message_handler` you pass directly — verify that a custom `MessageHandler` still routes what the application depends on.

## Authenticate with a Bearer Token

Bearer authentication applies only to HTTP-based transports. A bearer token may be a JWT or an opaque token; FastMCP treats it as a secret string and does not validate its format.

Pass a raw token string without the `Bearer` prefix. FastMCP adds the scheme:

```python
from fastmcp import Client

async with Client(
    "https://api.example.com/mcp",
    auth=load_secret("MCP_ACCESS_TOKEN"),
) as client:
    await client.list_tools()
```

For explicit auth use `BearerAuth`:

```python
from fastmcp.client.auth import BearerAuth

client = Client(
    "https://api.example.com/mcp",
    auth=BearerAuth(token=load_secret("MCP_ACCESS_TOKEN")),
)
```

`fastmcp.client.auth` also exposes `OAuth`, `ClientCredentialsOAuthProvider`, `PrivateKeyJWTOAuthProvider`, and `SignedJWTParameters`. The same `auth` values work on `StreamableHttpTransport` and `SSETransport`. Use explicit `headers` instead when the server expects a custom scheme such as `X-API-Key`.

Any custom auth class must subclass `httpx2.Auth`. An `httpx.Auth` subclass from a v3-era codebase will not import, because `httpx` is not installed.

Load tokens from an owned secret provider or environment boundary, require TLS, scope and rotate credentials, validate issuer, audience, expiry, and scopes at the server when applicable, and never put tokens in source, logs, test snapshots, command arguments, or committed MCP configuration. Authentication does not replace application authorization.

## Bridge a Remote Server with `fastmcp-remote`

Use `fastmcp-remote` when an MCP host can launch only a local stdio command but the real server is remote HTTP/SSE. It is a **standalone distribution not installed here**; the flags below are upstream-documented rather than locally verified.

```json
{
  "mcpServers": {
    "remote-api": {
      "command": "uvx",
      "args": ["fastmcp-remote", "https://example.com/mcp"]
    }
  }
}
```

`uvx` can run the standalone package without a persistent install. Use `uv tool install fastmcp-remote` only when the host requires an already-installed command. Pin the package through the owner's execution policy when reproducibility matters.

Pass the complete MCP endpoint. The bridge starts locally and connects upstream during MCP initialization; an unreachable endpoint, wrong path, or failed auth should fail initialization. After success, tools, resources, prompts, and ping proxy through the same remote configuration.

HTTPS endpoints use browser OAuth automatically unless an `Authorization` header is supplied. OAuth tokens default to `~/.fastmcp/remote`; change the directory with `FASTMCP_REMOTE_CONFIG_DIR`, and use `--resource <name>` to isolate token storage for a remote identity. When the authorization server requires it, pass the callback port as the second positional argument and set `--host` (default `localhost`).

Use repeated `--header "Name: Value"` options for explicit headers. The first colon ends the name, so values may contain colons. Quote values with spaces. On hosts that lose spaces, put the value in an environment variable and use `Authorization:${AUTH_HEADER}`. An Authorization header disables OAuth by default. Do not commit the resulting token-bearing environment or configuration.

For an unauthenticated local HTTP server, use `--auth none`. For an internal CA use `--verify /path/to/ca.pem` or `SSL_CERT_FILE`; `--verify false` disables certificate verification and is only for a time-bounded trusted-development exception.

Documented options:

| Option | Use |
| --- | --- |
| `--transport http \| sse` | Upstream transport; defaults to HTTP. |
| `--header "Name: Value"` | Repeatable upstream header; supports `${VAR}` expansion in values. |
| `--auth oauth \| none` | OAuth policy; default is OAuth unless Authorization is explicit. |
| `--verify <CA path> \| false` | TLS verification control. |
| `--resource <name>` | Separates OAuth token storage by remote identity. |
| `--host <hostname>` | OAuth callback hostname; defaults to `localhost`. |
| `--auth-timeout <seconds>` | OAuth callback wait; defaults to 300 seconds. |
| `--ignore-tool <glob>` | Repeatable tool-name filter. |
| `--debug` | Enables debug logs. |
| `--silent` | Suppresses non-critical logs. |

Review local browser callback exposure, token storage permissions, TLS, log output, host environment expansion, and hidden-tool policy. Use `fastmcp run`, not `fastmcp-remote`, for local Python servers, project environments, FastMCP config files, or development reload loops.

## Verification

Use the owning interpreter and installed release. Confirm the shape of the API before exercising it:

```bash
python -c "import inspect; from fastmcp import Client; print(inspect.signature(Client.__init__))"
python -c "import httpx"                                  # expect ModuleNotFoundError
python -c "from mcp.types import Tool as A; from mcp_types import Tool as B; assert A is B"
# mcp.types is a permanent alias of mcp_types on the stable SDK; prefer mcp_types in library code
python -c "from fastmcp.client.transports import StreamableHttpTransport as T; T(url='https://x/mcp', sse_read_timeout=1)"
# expect TypeError: unexpected keyword argument 'sse_read_timeout'
```

Then exercise the actual supported path:

1. Connect under the mode the deployment will use, and assert `protocol_version`, `server_info`, and negotiated capabilities. Assert that `initialize()` raises under `mode="auto"` if any code path still calls it.
2. Pin `mode="legacy"` in one test and assert the handshake result object is present, so an era regression is visible.
3. Grep for `ping()` in health checks, readiness probes, and connection smoke tests, and replace it with `list_tools()` on any connection not pinned to `mode="legacy"`. A probe that only asserts "no exception" will not catch this; assert on the result.
4. List tools, resources, templates, and prompts; cover pagination when used.
5. Execute one tool, resource, and prompt per used result type, including a pinned version when version selection matters.
6. Test tool error raising and `raise_on_error=False`; validate structured, unstructured, binary, and raw protocol results.
7. Test sampling and elicitation success, invalid response, handler exception, decline/cancel, unsupported capability, and cancellation.
8. Test foreground progress and callback failure.
9. Assert no camelCase reads remain by running the suite with `mcp_camelcase_compat=False`; warnings alone will not prove it.
10. Test roots against out-of-root and symlink access at the server authorization boundary.
11. Test list-change and resource-update notifications, cache refresh, and duplicate delivery.
12. If caching is enabled: test hit, miss, `"refresh"`, `"bypass"`, TTL expiry, partition isolation across principals, and store failure degrading to a miss.
13. For stdio, test environment isolation, stderr routing, subprocess exit, `keep_alive` reuse, and shutdown.
14. For HTTP/SSE, test missing, expired, and wrong-audience credentials, custom headers, TLS failure, timeouts, disconnect, reconnect, redirects, and the exact endpoint path.
15. For `fastmcp-remote`, test first OAuth, token reuse and cleanup, explicit headers, custom CA, callback timeout, hidden tools, and host startup failure.

Do not stop at an in-memory test when the production consumer uses stdio, HTTP, SSE, MCP config composition, or `fastmcp-remote`.

## Source Coverage

| Source | Covered behavior |
| --- | --- |
| `fastmcp/client/client.py` | Constructor, era negotiation, `initialize()` behavior, operations, result types |
| `fastmcp/client/transports/` | stdio, Streamable HTTP, SSE, in-memory, MCP config composition |
| `fastmcp/client/caching.py`, `mcp/client/caching.py` | Response cache config, modes, stores, security properties |
| `fastmcp/_compat.py` | camelCase read shim, alias table, runtime toggle |
| `fastmcp/client/auth/` | Bearer and OAuth providers |
| [Client](https://gofastmcp.com/clients/client), [Transports](https://gofastmcp.com/clients/transports) | Targets, construction, lifecycle, transport selection |
| [Tools](https://gofastmcp.com/clients/tools), [Resources](https://gofastmcp.com/clients/resources), [Prompts](https://gofastmcp.com/clients/prompts) | Execution options, hydration, content, versions, raw MCP access |
| [Sampling](https://gofastmcp.com/clients/sampling), [Elicitation](https://gofastmcp.com/clients/elicitation) | Handler contracts, capabilities, accept/decline/cancel |
| [Progress](https://gofastmcp.com/clients/progress), [Logging](https://gofastmcp.com/clients/logging), [Roots](https://gofastmcp.com/clients/roots), [Notifications](https://gofastmcp.com/clients/notifications) | Callbacks, levels, root conversion, notification hooks |
| [Bearer auth](https://gofastmcp.com/clients/auth/bearer) | String token, `BearerAuth`, transport auth, secret handling |
| [fastmcp-remote](https://gofastmcp.com/clients/fastmcp-remote) | Host config, endpoints, OAuth, headers, TLS, flags — upstream-documented only |

Use the [Python SDK reference](https://gofastmcp.com/python-sdk) for generated signatures and [llms.txt](https://gofastmcp.com/llms.txt) to find moved pages. Resolve any discrepancy against installed source and record it in the implementation handoff.
