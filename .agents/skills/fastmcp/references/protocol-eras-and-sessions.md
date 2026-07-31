# Protocol Eras and Sessions

## Purpose

FastMCP v4 speaks two structurally different MCP protocols. The **handshake era** initializes with `initialize`, keeps a live session, and lets the server push requests back to the client. The **modern era** (`2026-07-28`) has none of that: no handshake, no persistent session, no server-to-client back-channel.

`Client(mode=...)` defaults to `"auto"`, and against a FastMCP server `"auto"` negotiates the modern era. **A v3 codebase therefore lands on the sessionless protocol without changing a line**, and the features that depended on a back-channel — `initialize()`, `Middleware.on_initialize`, cross-request `ctx.set_state`, `ctx.elicit`, `ctx.sample` — change, stop working, or are removed entirely.

This reference owns era semantics, the replacements v4 provides (guard tools, explicit sessions, extensions), and how to tell which era you are on. For the release baseline and the wider v3 → v4 delta, see [Version and source routing](version-and-source-routing.md).

## Era Constants

Defined in `mcp_types.version`, re-exported through `fastmcp.client.client`.

| Constant | Value |
| --- | --- |
| `HANDSHAKE_PROTOCOL_VERSIONS` | `('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')` |
| `MODERN_PROTOCOL_VERSIONS` | `('2026-07-28',)` |
| `LATEST_HANDSHAKE_VERSION` | `'2025-11-25'` |
| `LATEST_MODERN_VERSION` | `'2026-07-28'` |
| `LATEST_PROTOCOL_VERSION` | `'2026-07-28'` |
| `OLDEST_SUPPORTED_VERSION` | `'2024-11-05'` |
| `KNOWN_PROTOCOL_VERSIONS` / `SUPPORTED_PROTOCOL_VERSIONS` | all five, oldest first |

`is_version_at_least(version: str, minimum: str) -> bool` compares two version strings.

Era membership is a **tuple lookup, not a comparison**. Server and client code both branch on `protocol_version in MODERN_PROTOCOL_VERSIONS`. Write the same test; do not compare date strings.

## Selecting an Era

```python
ConnectMode = Literal["legacy", "auto"] | str
Client(transport, mode: ConnectMode = "auto")
```

| `mode` | Negotiation |
| --- | --- |
| `"auto"` (default) | Probe `server/discover` at the newest modern version; adopt it, or fall back to the `initialize` handshake for handshake-era servers |
| `"legacy"` | Force the `initialize` handshake |
| a modern version string | Adopt that version directly, from `prior_discover` if supplied, else a synthesized minimal `DiscoverResult` |

The constructor validates `mode` eagerly. Anything outside `"legacy"`, `"auto"`, and `MODERN_PROTOCOL_VERSIONS` raises `ValueError`; passing a **handshake** version gets an added hint to use `mode="legacy"`.

`"auto"` silently degrades to `"legacy"` when the transport cannot carry the modern era — `ClientTransport.legacy_only`. `SSETransport` sets it `True`. `MCPConfigTransport` sets it `True` for a multi-server config (each backend is mounted behind a handshake-era `ProxyClient`) and delegates to the underlying transport for a single-server config.

## What the Modern Era Changes

Observed by connecting an in-memory `Client` to a `FastMCP` server at both eras.

| Behavior | `mode="legacy"` → `2025-11-25` | `mode="auto"` → `2026-07-28` |
| --- | --- | --- |
| `client.initialize_result` | `InitializeResult` | `None` |
| `await client.initialize()` | returns the result | **raises `RuntimeError`** |
| `await client.ping()` | returns | **raises `MCPError: Method not found`** |
| `await client.set_logging_level(...)` | returns | **raises `MCPError: Method not found`** |
| `list_tools` / `call_tool` / `read_resource` / `get_prompt` | work | work |
| `Middleware.on_initialize` | fires | **never fires** |
| `ctx.session_id` across requests on one connection | stable | **a new UUID every request** |
| `ctx.set_state` / `get_state` within one request | works | works |
| `ctx.set_state` visible to the next request | yes | **no — reads `None`** |
| `ctx.enable_components` / `disable_components` | applies | **silently does nothing** |
| `await ctx.elicit(...)` | returns an elicitation result | **raises `ToolError`** |
| `ctx.sample` / `ctx.sample_step` | **removed — `AttributeError`** | **removed — `AttributeError`** |
| `await ctx.log(...)` | works, deprecated | works, deprecated |
| Returning `InputRequiredResult` | `MCPError(INVALID_PARAMS)` | supported |
| Advertised `*.list_changed` capability | `True` | `False` |

Rows in that table divide into three kinds, and the third is the one that costs debugging time:

- **Loud** — `initialize()`, `ping()`, and `elicit()` raise immediately, and `ctx.sample` / `ctx.sample_step` fail at attribute access.
- **Absent** — `on_initialize` simply never runs.
- **Silent** — cross-request state reads `None` and visibility rules do nothing, with **no exception and no warning**. Audit for these; nothing surfaces them at runtime.

The `initialize()` failure names its own remedy:

> The client negotiated a modern protocol era (server/discover), which has no InitializeResult. Inspect `client.protocol_version`, `client.server_info`, `client.server_capabilities`, and `client.instructions` for the metadata available in this mode, or construct the client with `mode='legacy'`.

Those four properties are the **era-neutral accessors**. They are populated from whichever negotiation result the era produced, so read them instead of `initialize_result` in code that must work on both. A directly pinned modern version synthesizes a server identity with an empty name.

`mode="legacy"` is the escape hatch, not the destination. Reach for it when a dependency genuinely needs the back-channel and cannot be restructured; otherwise adopt the modern replacements below.

### Methods the modern era removed

`await client.ping()` raises `MCPError: Method not found` on the modern era. So does `await client.set_logging_level(...)`. Neither is a FastMCP gap — **the methods do not exist in the `2026-07-28` protocol.**

`mcp_types.methods` keys its registries by `(method, protocol_version)`. Comparing the newest handshake version against the modern one:

| Method                  | `2025-11-25` | `2026-07-28` |
| ----------------------- | ------------ | ------------ |
| `initialize`            | defined      | **removed**  |
| `ping`                  | defined      | **removed**  |
| `logging/setLevel`      | defined      | **removed**  |
| `resources/subscribe`   | defined      | **removed**  |
| `resources/unsubscribe` | defined      | **removed**  |
| `server/discover`       | —            | **added**    |
| `subscriptions/listen`  | —            | **added**    |

The complete client-request set at `2026-07-28` is `completion/complete`, `prompts/get`, `prompts/list`, `resources/list`, `resources/read`, `resources/templates/list`, `server/discover`, `subscriptions/listen`, `tools/call`, `tools/list`. Everything else is `Method not found` by definition of the era.

This is worth stating precisely, because two plausible-sounding explanations are wrong:

- **Not** a missing handler. FastMCP's low-level server registers `ping` — the SDK's `Server.__init__` defaults `on_ping=_ping_handler` and installs it. A live `FastMCP` instance reports `'ping' in server._mcp_server._request_handlers` as `True`.
- **Not** `_INIT_EXEMPT`. `mcp/server/runner.py` does carry `_INIT_EXEMPT = frozenset({"ping"})`, but it exempts `ping` from the _initialization gate_ — it lets a client ping before the handshake completes. The handler lookup runs **first** and raises `METHOD_NOT_FOUND` on its own, which is why the source comment there notes the init gate "only ever applies to methods the server actually serves."

Note the consequence for `logging/setLevel`: FastMCP registers a handler for it, and on the modern era that handler is unreachable. Client-driven log-level control is gone, which pairs with the logging deprecation below.

Every other core operation is unaffected — `list_tools`, `call_tool`, `read_resource`, and `get_prompt` work on both eras.

Do not use `client.ping()` as a health check on the default client mode. Use a cheap real operation such as `list_tools()`, or pin `mode="legacy"` for that probe. Client-side liveness guidance belongs to [Clients and transports](clients-and-transports.md).

Because the cause is the protocol method registry rather than transport plumbing, this is transport-independent by construction. It was still observed only in-memory here; socket binding is unavailable in this sandbox.

### A fresh session per request

`ctx.session_id` does **not** raise on the modern era — it returns a **new UUID on every request**. Three tool calls over one connection produce three distinct ids under `mode="auto"` and one shared id under `mode="legacy"`.

This is the root cause of two other rows, not a separate fact. Anything keyed by session identity silently loses continuity, because each request is a new session by construction:

- `ctx.set_state` prefixes keys with `f"{session_id}:{key}"`, so the next request looks under a different prefix and reads `None`.
- `ctx.enable_components` / `disable_components` persist rules to session state under `_visibility_rules` (`fastmcp/server/transforms/visibility.py`), so the rule is written and then never found.

Treat "is this keyed by `session_id`?" as the test for whether a feature survives the modern era.

### Session-scoped visibility fails silently

This is the most dangerous row in the table, because nothing announces it.

Observed with two tools and a rule disabling one of them:

|                                        | `mode="legacy"` | `mode="auto"` |
| -------------------------------------- | --------------- | ------------- |
| Rule written to state                  | yes             | yes           |
| Tool hidden from the next `list_tools` | **yes**         | **no**        |
| Exception raised                       | none            | none          |
| Warning emitted                        | none            | none          |

The call returns normally, the rule really is stored, change notifications still fire, and the component stays visible. Code that hides a tool for authorization, tenancy, or workflow-gating reasons therefore **keeps exposing it** with no runtime signal at all.

If component visibility is a security boundary, do not implement it with session-scoped rules. Enforce it where the era cannot erase it — an authorization check on the component, or a server-side transform. See [Authorization](authorization.md) and [Providers and transforms](providers-and-transforms.md).

### SEP-2577 has three casualties, not one

SEP-2577 removed the server-initiated back-channel. Three surfaces depend on it, and each degrades differently:

| Surface     | Modern-era status                                            |
| ----------- | ------------------------------------------------------------ |
| Sampling    | Client push removed; a **server-side handler still answers** |
| Elicitation | Server push removed; replaced by the guard-tool channel      |
| Logging     | Capability **deprecated**, still functional on both eras     |

Logging is the one usually missed. `mcp/server/session.py` decorates `send_log_message` with `@deprecated("The logging capability is deprecated as of 2026-07-28 (SEP-2577).", category=MCPDeprecationWarning)`, so **every** `ctx.log()` call emits `MCPDeprecationWarning` — on both eras, verified. The call still delivers; only its future is in doubt.

Note the warning class. `MCPDeprecationWarning` subclasses `UserWarning`, while FastMCP's own `FastMCPDeprecationWarning` subclasses `DeprecationWarning`, so a test filter catching one misses the other. The logging severity model and client-side log handling belong to [Interactivity and observability](interactivity-and-observability.md).

## Which Era Am I On

**Client side** — `client.protocol_version` is set by connect-time negotiation regardless of era, and is `None` while disconnected.

```python
async with Client(server) as client:
    modern = client.protocol_version in MODERN_PROTOCOL_VERSIONS
```

**Server side** — read it off the request context. There is **no `ctx.protocol_version`**; the attribute does not exist and `getattr` on it silently returns your default.

```python
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

@mcp.tool
async def probe(ctx: Context) -> bool:
    rc = ctx.request_context
    return rc is not None and rc.protocol_version in MODERN_PROTOCOL_VERSIONS
```

`Context._is_modern_protocol()` is the internal form of exactly this test. It returns `False` when no request context exists — a background task or pre-session call — which lets the underlying wire path raise its own error rather than mislabeling the era. Reproduce that fallback in your own checks; do not treat "no context" as "modern".

Diagnostically, `client.server_capabilities` is a fast era tell: handshake-era connections advertise `list_changed: True` on tools, resources, and prompts, and modern connections advertise `False` because there is no channel to deliver the notification.

A second tell, useful from inside a tool with no client access: call `ctx.session_id` twice across two requests. Differing values mean the modern era.

When triaging an unexplained behavior change, work down this list — it is ordered by how loudly each failure announces itself:

| Symptom | Likely cause |
| --- | --- |
| `MCPError: Method not found` | the method was removed from the modern protocol — check `mcp_types.methods.CLIENT_REQUESTS` |
| `RuntimeError` about no `InitializeResult` | `initialize()` on the modern era |
| `ToolError` naming `2026-07-28` | `ctx.elicit` |
| `AttributeError` for `sample` / `sample_step` / `list_roots` | server-side push APIs were removed from `Context`; call an LLM from server-owned code |
| Middleware setup never runs | `on_initialize` does not fire |
| State reads `None` that was just written | fresh `session_id` per request |
| A disabled component is still listed | session-scoped visibility rule never applied |
| `MCPDeprecationWarning` in test output | `ctx.log()` — SEP-2577 deprecation, both eras |

## Era-Conditional Context

### `ctx.elicit`

Raises `ToolError` on the modern era, before touching the wire, so the failure names the era instead of surfacing an opaque "Method not found":

> elicitation via server-initiated requests is unavailable on 2026-07-28 connections.

It raises a **different** `ToolError` inside a background task on any era, because a worker must never block on a client round-trip:

> Imperative `ctx.elicit()` is not supported inside a background task. Gather input with the guard pattern instead: return an `InputRequiredResult` from the tool (with `input_requests`), and read `ctx.input_responses` / `ctx.request_state` when the task re-runs after the client answers.

The background-task check runs **first**, so a background task on any era gets that message rather than the era message.

### `ctx.sample`, `ctx.sample_step`, and `ctx.list_roots` — removed

Server-initiated push APIs are **removed from the server API** on the pinned release: `Context` has no `sample`, `sample_step`, or `list_roots` attribute (access raises `AttributeError`), and `FastMCP(sampling_handler=..., sampling_handler_behavior=...)` no longer exists. SEP-2577 removed server-initiated requests from the modern protocol, and rather than leave methods that only work against old clients, the removal is total across eras.

The migration is architectural, not a rename: call the model provider from server-owned code and inject its client through [Lifespan](lifespan.md) or [Dependency injection](dependency-injection.md); take roots as tool arguments or through the guard pattern below. The **client**-side handlers (`Client(sampling_handler=..., roots=...)`) are retained — a client still has to answer a legacy server's pushed requests.

## Guard Tools: the Input-Required Channel

SEP-2322 replaces server-push elicitation with a **client-driven** round trip. The tool returns a request instead of blocking on one; the client fulfills it and calls the tool again. `InputRequiredResult` lives in `mcp_types`.

```python
class InputRequiredResult(BaseModel):
    meta: dict[str, Any] | None          # wire: _meta
    result_type: Literal["input_required"]  # wire: resultType
    input_requests: dict[str, CreateMessageRequest | ListRootsRequest | ElicitRequest] | None  # wire: inputRequests
    request_state: str | None            # wire: requestState
```

Annotate the tool's return as a union with the guard arm:

```python
import mcp_types
from fastmcp import Context

@mcp.tool
async def book(ctx: Context) -> str | mcp_types.InputRequiredResult:
    if ctx.input_responses is None:
        return mcp_types.InputRequiredResult(
            inputRequests={
                "seat": mcp_types.ElicitRequest(
                    params=mcp_types.ElicitRequestFormParams(
                        message="Window or aisle?",
                        requestedSchema={
                            "type": "object",
                            "properties": {"seat": {"type": "string"}},
                            "required": ["seat"],
                        },
                    )
                )
            },
            requestState="round-1",
        )
    return f"booked {ctx.input_responses['seat'].content['seat']}"
```

Note `ElicitRequestFormParams`. `mcp_types.ElicitRequestParams` is a **union alias** (`ElicitRequestURLParams | ElicitRequestFormParams`) and is not callable; instantiating it raises `TypeError: 'typing.Union' object is not callable`.

### How the union is parsed

`fastmcp/tools/function_parsing.py` strips every `InputRequiredResult` arm before deriving the output schema — the arm is a suspend signal, not output data. Stripping resolves PEP 695 `TypeAliasType` and peels `Annotated`, recursively, so all of these are recognized:

```python
str | InputRequiredResult
Annotated[InputRequiredResult, Field(...)] | str
type Value = int | InputRequiredResult;  str | Value
```

The tool above publishes `{"properties": {"result": {"type": "string"}}, ..., "x-fastmcp-wrap-result": True}` — the guard arm is absent. A **bare** `InputRequiredResult` annotation with no other arm is left intact and suppressed downstream like any other non-serializable return type.

`fastmcp/tools/tool_transform.py` wraps a returned `InputRequiredResult` in `InputRequiredToolResult` so it reaches the wire handler intact; passing it through `output_schema` would rebuild it as an empty `ToolResult` and drop the payload. A custom transform function may return the raw model and is wrapped the same way.

### Era enforcement

`fastmcp/server/mixins/mcp_operations.py` refuses to serialize the result on a handshake-era connection, raising `MCPError(INVALID_PARAMS)`:

> Tool `<name>` returned an `InputRequiredResult` to request client input, but the multi-round-trip result type (SEP-2322) only exists at MCP 2026-07-28; this connection negotiated `<version>`. Use `ctx.elicit()` for server-initiated input on handshake-era connections.

A tool that must serve both eras has to branch on the era itself.

### Reading the responses

`ctx.input_responses` is `mcp_types.InputResponses | None`. It is `None` on the initial round — nothing has been asked, or the client retried without responses — and present on a later round, keyed to match the `input_requests` map the tool minted. Each value is the client's result for that request: an `ElicitResult`, `CreateMessageResult`, or `ListRootsResult`. Inside a background task there is no wire request, so it falls back to whatever the in-task guard loop delivered.

`request_state` round-trips through `ctx.request_state`, which is how a stateless tool remembers what it asked.

`Client(input_required_max_rounds=10)` caps the retry rounds. It is a client-side loop guard: a tool that always returns `InputRequiredResult` terminates instead of spinning. Lower it for untrusted servers.

The client needs a handler for whatever the tool requests — `elicitation_handler`, `sampling_handler`, or `roots` — exactly as on the handshake era. The difference is direction: the client answers a returned request rather than a pushed one.

## Explicit Session State

`ctx.set_state` no longer survives a request on the modern era, so v4 provides explicit, principal-scoped storage in `fastmcp/server/sessions.py`. Both mechanisms are backed by the server's state store and **isolated by the authenticated principal, never by a client-supplied identifier**.

### `ctx.set_state` has two storage modes

```python
async def set_state(self, key: str, value: Any, *, serializable: bool = True) -> None
async def get_state(self, key: str) -> Any
async def delete_state(self, key: str) -> None
```

Both accessors are **coroutines** in v4. Calling them without `await` produces a `RuntimeWarning: coroutine 'Context.set_state' was never awaited` and a coroutine object — the write silently never happens. This fails quietly; it does not raise.

`serializable` selects the backing store:

| `serializable` | Stored in | Lifetime | Value constraint |
| --- | --- | --- | --- |
| `True` (default) | Session-scoped state store | Across requests **in the same MCP session** | Must be JSON-serializable |
| `False` | Request-scoped dict | The current request only | Anything |

Keys are automatically prefixed with the session identifier (`f"{session_id}:{key}"`), and session-scoped entries are written with a TTL of `Context._STATE_TTL_SECONDS` — `86400`, twenty-four hours.

`get_state` checks the request-scoped dict **first**, then falls back to the session store, and returns `None` when neither has the key. `delete_state` removes from both. Setting a key with `serializable=True` clears any request-scoped shadow so the session value becomes visible again.

Storing a non-JSON value under the default raises `TypeError` naming the remedy:

> Value for state key `'obj'` is not serializable. Use `set_state('obj', value, serializable=False)` to store non-serializable values. Note: non-serializable state is request-scoped and will not persist across requests.

That caveat is the whole trap. `serializable=False` is how connections, clients, and other live handles get carried **within** one request — it is not a way to opt out of the serialization requirement for durable state. Verified on a handshake connection: a `serializable=False` value reads back within the same request and reads `None` on the next one, exactly like modern-era session state.

This is why the modern era forces the migration. Once state must survive a request and cross a `session_state_store`, it must be serializable and explicitly scoped — which is precisely what `UserSession` and `SessionId` provide below.

| Symbol | Role |
| --- | --- |
| `Session` | Async accessors over one `(principal, session_id)` bucket: `get`, `set`, `delete`, `clear`, `end`, `id` |
| `UserSession` | Annotation marker for the injected per-user session; the injected value is a `Session` |
| `SessionId` | `Annotated[str, ...]` tool argument; resolve with `await get_session(session_id)` |
| `CurrentSession()` | Inject the per-user `Session` for the current principal |
| `OptionalCurrentSession()` | Same, or `None` when unauthenticated |
| `SessionProvider` | `Provider` contributing the `create_session` / `end_session` tools |
| `create_session() -> str` | Mint a session id |
| `end_session(session_id: SessionId) -> str` | End a session and delete its state |
| `get_session(session_id: str) -> Session` | Resolve a minted id under the current principal |
| `SessionAuthError` | Injected `UserSession` with no authenticated principal |
| `InvalidSession` | Id did not resolve to a session created under the current principal |

Also exported: `SESSION_ID_DESCRIPTION` (the argument description agents read), `current_principal`, `get_access_token`, `get_server`, `session_storage_key`, `principal_components`, `session_id_parameter_names`.

### Per-user injection

```python
from fastmcp.server.sessions import UserSession

@mcp.tool
async def remember(value: str, session: UserSession) -> str:
    await session.set("last", value)
    return "ok"
```

Keyed by the request's authenticated principal. Always available under auth — no id, no provider, no validation. The parameter is **excluded from the published input schema**; the tool above advertises only `value`.

Without auth it raises `SessionAuthError`: _"Injected `session: UserSession` requires an authenticated principal, but this request is unauthenticated."_

### Per-session by id

```python
from fastmcp.server.sessions import SessionId, SessionProvider, get_session

mcp.add_provider(SessionProvider())

@mcp.tool
async def recall(session_id: SessionId) -> str:
    session = await get_session(session_id)
    return str(await session.get("last"))
```

The agent supplies the id. Ids that were never minted — or were minted under a different principal — raise `InvalidSession` (_"Invalid or unknown session."_). **That validation is the whole guarantee**; nothing enforces provider registration, so without `SessionProvider` no id can be created and these tools simply never resolve.

### Isolation boundary

State is keyed by `(principal, session_id)`. A request under principal B can never address principal A's keys regardless of the id it passes; the id only organizes sessions _within_ a principal.

**Without auth there is no principal wall.** A session id is then a bearer capability and sessions are not a boundary between clients. Do not use them to separate untrusted callers on an unauthenticated server. See [Authorization](authorization.md).

`FastMCP(session_state_store=...)` takes an `AsyncKeyValue` and defaults to `None`. Configure a shared store for multi-process deployments; see [Storage backends](storage-backends.md).

## Extensions

An MCP extension (SEP-2133) is an opt-in, capability-negotiated bundle of protocol behavior identified by a reverse-DNS string such as `io.modelcontextprotocol/tasks`. A FastMCP `ServerExtension` is bound to its `FastMCP` instance at registration, so its handlers reach the component registry, `Context`, and auth scope that the SDK's `mcp.server.extension.Extension` withholds.

```python
FastMCP.add_extension(extension: ServerExtension) -> None
```

`fastmcp.server.extensions.__all__` is `['MethodBinding', 'ServerExtension', 'read_client_extension_settings']`.

A subclass overrides only what it contributes; every hook has a default.

| Hook | Contribution |
| --- | --- |
| `settings()` | Spliced into `ServerCapabilities.extensions[identifier]` |
| `methods()` | `MethodBinding`s wired onto the low-level server at registration |
| `intercept_tool_call()` | Last gate before a tool body runs — after middleware, before component execution; may observe, short-circuit, or pass through |
| `lifespan()` | Entered with the server's lifespan and exited on shutdown; the hook the SDK's `Extension` lacks, needed to start backends and workers |
| `client_settings` | Client-advertised settings for this extension |

```python
MethodBinding(
    method: str,
    params_type: type[BaseModel],
    handler: ExtensionRequestHandler,
    protocol_versions: frozenset[str] | None = None,
)
```

`protocol_versions` scopes a method to specific eras. `read_client_extension_settings(ctx, identifier) -> dict[str, Any] | None` reads what the client advertised.

An interceptor may return an extension-defined wire result model instead of a `ToolResult` — the tasks extension's `CreateTaskResult`, for instance. Core does not interpret extension result shapes; it hands them to the runner for serialization.

### Detecting client support

```python
ctx.client_supports_extension(extension_id: str) -> bool
```

Reads the `extensions` field on the client's advertised `ClientCapabilities`. Available in request mode and in background-task mode, where the snapshot session preserves the client's initialize params. Returns `False` when no session is available — a distributed worker with no live session, or outside any context — so a `False` means "not advertised **or** not observable". Do not read it as a positive assertion of absence.

Background tasks are the extension this repository most often asks about; it is not installed here. See [Background tasks](tasks.md).

### Experimental capabilities

```python
FastMCP(experimental_capabilities: dict[str, dict[str, Any]] | None = None)
```

Surfaces verbatim under `server_capabilities.experimental`, on both eras. It is a capability announcement only — FastMCP attaches no behavior. Use `add_extension` when behavior is needed.

## Implementation Rules

- Do not call `client.initialize()` in code that may negotiate the modern era. Read `protocol_version`, `server_info`, `server_capabilities`, and `instructions`.
- Do not implement `Middleware.on_initialize` as the only place a cross-cutting concern is installed; it never fires on the modern era. Use lifespan or per-request hooks.
- Treat `ctx.set_state` as request-scoped. Use `UserSession` or `SessionId` for anything that must outlive a request.
- `await` every `ctx.set_state` / `ctx.get_state` / `ctx.delete_state` call. A missing `await` warns and silently does nothing.
- Do not reach for `serializable=False` to store a value that must outlive the request; it makes the state request-scoped, not durable.
- Replace `ctx.elicit` with a guard tool for new work; keep it only on a pinned `mode="legacy"` path.
- Replace `ctx.sample` / `ctx.sample_step` with a server-side model client.
- Branch on `protocol_version in MODERN_PROTOCOL_VERSIONS`, never on string comparison, and treat a missing request context as unknown rather than modern.
- Do not rely on session isolation for security on an unauthenticated server.
- Set `input_required_max_rounds` deliberately when calling an untrusted server.
- Pin `mode` explicitly in tests that assert era-specific behavior; the default can change what you are testing.
- Do not use `client.ping()` as a liveness probe on the default client mode; use a real operation such as `list_tools()`.
- Do not key anything durable on `ctx.session_id`. It is a per-request value on the modern era.
- Do not enforce a security boundary with `enable_components` / `disable_components`; the rule can vanish without raising.
- Server-side push APIs are gone, not gated: `ctx.sample` / `ctx.sample_step` / `ctx.list_roots` raise `AttributeError` on **both** eras; never write them.
- Filter both `FastMCPDeprecationWarning` (a `DeprecationWarning`) and `MCPDeprecationWarning` (a `UserWarning`) in tests; catching one misses the other.

## Verification

Exercise both eras. Cover:

- `mode="auto"` and `mode="legacy"` against the same server, asserting the negotiated `protocol_version`;
- `initialize()` succeeding on legacy and raising on modern;
- `ping()` and `set_logging_level()` raising `MCPError: Method not found` on modern while `list_tools` / `call_tool` / `read_resource` / `get_prompt` all succeed;
- the era's method registry directly — `("ping", "2026-07-28") not in mcp_types.methods.CLIENT_REQUESTS` — which is cheaper and more durable than probing a live connection;
- `ctx.session_id` differing across three requests on modern and matching on legacy;
- middleware `on_initialize` firing only on legacy;
- state written in one request being absent in the next on modern and present on legacy;
- a disabled component still appearing in `list_tools` on modern and disappearing on legacy, asserting **no** exception and **no** warning in both cases;
- a non-JSON value raising `TypeError` under `serializable=True`, and a `serializable=False` value being absent in the next request on **both** eras;
- `ctx.elicit` raising `ToolError` on modern, and the distinct background-task elicit error;
- `ctx.sample` / `ctx.sample_step` / `ctx.list_roots` raising `AttributeError` on both eras;
- `ctx.log()` emitting `MCPDeprecationWarning` on both eras while still delivering the message;
- a guard tool's published output schema omitting the `InputRequiredResult` arm, including aliased and `Annotated` arms;
- a full guard round trip: `input_responses is None` then populated, `request_state` round-tripping;
- a guard tool on a handshake connection raising `MCPError(INVALID_PARAMS)`;
- `input_required_max_rounds` terminating a tool that never stops asking;
- `UserSession` absent from the input schema, raising `SessionAuthError` unauthenticated, and isolating two principals;
- an unminted and a cross-principal `SessionId` raising `InvalidSession`;
- `SessionProvider` registering `create_session` and `end_session`;
- `session_state_store` persisting across processes when configured;
- `client_supports_extension` returning `False` both when unadvertised and when no session exists;
- `experimental_capabilities` appearing under `server_capabilities.experimental` on both eras;
- an SSE or multi-server config transport downgrading `"auto"` to the handshake era.
