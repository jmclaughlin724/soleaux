# Middleware

## Source and Version Contract

Use this reference for the complete workflow represented by the live [FastMCP middleware guide](https://gofastmcp.com/servers/middleware), verified 2026-07-14. Middleware is FastMCP-specific rather than part of the MCP protocol. Confirm imports, signatures, defaults, and result models against installed source before implementing. See [Version and source routing](version-and-source-routing.md) for the pinned baseline.

The live guide differs from the pinned release on a few defaults and contains synchronous-looking state examples even though `Context.get_state` and `set_state` are async in installed source. Use the installed behavior recorded below.

**Import paths moved.** `fastmcp.server.middleware.__all__` exports only `Middleware`, `MiddlewareContext`, `CallNext`, `AuthMiddleware`, and `PingMiddleware`. Every other built-in must be imported from its own submodule; `from fastmcp.server.middleware import LoggingMiddleware` and its siblings raise `ImportError`. The table under [Built-In Middleware](#built-in-middleware) gives each owning module.

## Pipeline and Ordering

Middleware wraps MCP operations bidirectionally. Requests enter in registration order, reach the handler, then unwind in reverse order. `call_next(context)` is the boundary: call it exactly once to continue, or deliberately omit it by raising the correct protocol error to terminate processing.

```python
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

class TraceMiddleware(Middleware):
    async def on_request(self, context: MiddlewareContext, call_next):
        record_start(context.method)
        try:
            return await call_next(context)
        finally:
            record_finish(context.method)

mcp = FastMCP("service", middleware=[TraceMiddleware()])
```

Order middleware by the behavior required on both sides of the handler:

1. Put error sanitization early so it can catch failures from every inner layer.
2. Establish safe correlation metadata before middleware that consumes it.
3. Authenticate and authorize before protected or expensive work.
4. Rate-limit before expensive work.
5. Place timing and logging where their intended measurement scope is explicit.
6. Apply response limits after the operation has produced its result.

The first added middleware is first on ingress and last on egress. Test the actual sequence; comments are not proof.

### Mounted Servers

Parent middleware runs for every request routed through the parent, including mounted-server operations. A child server's middleware runs only for that child's operations, after parent middleware on ingress and before parent middleware on egress.

Each `FastMCP` instance owns its session state store. Middleware state does not cross a mount merely because middleware execution does. To share serializable session state, give parent and child the same `session_state_store`. To share one request-local non-serializable value across the routed request, use `await ctx.set_state(..., serializable=False)` and confirm the mounted path preserves it.

## Hook Selection

Override only the narrowest hook that owns the behavior. Unoverridden hooks pass through.

| Hook | Scope | Typical use | Result |
| --- | --- | --- | --- |
| `on_message` | Every request and notification | Whole-channel metrics or logging | Downstream result |
| `on_request` | Requests expecting a response | Request validation or broad policy | Downstream result |
| `on_notification` | Fire-and-forget notifications | Bounded event observation | No response value |
| `on_call_tool` | Tool execution | Tool policy, audit, result inspection | `ToolResult`; may raise `ToolError` |
| `on_read_resource` | Resource or template read | URI policy and content controls | Resource contents |
| `on_get_prompt` | Prompt rendering | Prompt policy and result controls | Prompt result |
| `on_list_tools` | Tool catalog | Filter or annotate `Tool` objects | `list[Tool]` |
| `on_list_resources` | Resource catalog | Filter or annotate resources | `list[Resource]` |
| `on_list_resource_templates` | Template catalog | Filter or annotate templates | `list[ResourceTemplate]` |
| `on_list_prompts` | Prompt catalog | Filter or annotate prompts | `list[Prompt]` |
| `on_initialize` | Session initialization | Client compatibility checks and setup | `None`; initialization response is internal |
| `__call__` | Raw all-message path | Uniform handling that intentionally bypasses hook dispatch | Downstream result |

For one operation, hooks nest from general to specific. A tool call passes through `on_message`, `on_request`, then `on_call_tool`.

Reject an initialization before `await call_next(context)`. An exception after initialization has already continued is only logged because the response has already been sent.

## MiddlewareContext

`MiddlewareContext` provides:

| Attribute         | Meaning                                          |
| ----------------- | ------------------------------------------------ |
| `method`          | MCP method name such as `tools/call`             |
| `source`          | `"client"` or `"server"`                         |
| `type`            | `"request"` or `"notification"`                  |
| `message`         | Typed MCP message payload                        |
| `timestamp`       | Receive time                                     |
| `fastmcp_context` | Request-scoped FastMCP `Context`, when available |

The MCP session may not exist during initialization or other pre-handshake phases. Test `context.fastmcp_context` and then `ctx.request_context` before reading `request_id` or `session_id`. For HTTP headers before an MCP session exists, use `get_http_headers()` from the dependency API. Treat client IDs, request IDs, headers, and metadata as untrusted correlation data, never authenticated identity.

## Built-In Middleware

Prefer these implementations over custom protocol plumbing. Exact installed defaults below take precedence over floating examples.

Each built-in is owned by one submodule. Only the last two are re-exported from `fastmcp.server.middleware` itself:

| Class | Owning module |
| --- | --- |
| `LoggingMiddleware`, `StructuredLoggingMiddleware` | `fastmcp.server.middleware.logging` |
| `TimingMiddleware`, `DetailedTimingMiddleware` | `fastmcp.server.middleware.timing` |
| `ResponseCachingMiddleware` and its settings types | `fastmcp.server.middleware.caching` |
| `RateLimitingMiddleware`, `SlidingWindowRateLimitingMiddleware` | `fastmcp.server.middleware.rate_limiting` |
| `ErrorHandlingMiddleware`, `RetryMiddleware` | `fastmcp.server.middleware.error_handling` |
| `ResponseLimitingMiddleware` | `fastmcp.server.middleware.response_limiting` |
| `DereferenceRefsMiddleware` | `fastmcp.server.middleware.dereference` |
| `ToolInjectionMiddleware` | `fastmcp.server.middleware.tool_injection` |
| `AuthMiddleware`, `PingMiddleware` | `fastmcp.server.middleware` (and their own submodules) |

### Logging

- `LoggingMiddleware`: human-readable records.
- `StructuredLoggingMiddleware`: structured JSON-style records suitable for aggregation.
- Common options: `logger`, `log_level=20`, `include_payloads=False`, `include_payload_length=False`, `estimate_payload_tokens=False`, `methods=None`, and `payload_serializer=None`.
- `LoggingMiddleware` additionally has `max_payload_length=1000`; `StructuredLoggingMiddleware` has no such parameter. The live guide currently shows a different default.

Keep payloads disabled by default. If an owner explicitly permits payload logging, redact before serialization and cap size. Never log tokens, cookies, authorization headers, secrets, elicitation answers, sampled conversations, or protected component contents.

### Timing

- `TimingMiddleware(logger=None, log_level=20)` records overall request duration.
- `DetailedTimingMiddleware(logger=None, log_level=20)` separates timing for tools, resources, and prompts.

Include success, failure, and cancellation. Export spans with the installed OpenTelemetry path when traces, rather than one middleware record, are the goal.

### Response Caching

`ResponseCachingMiddleware` can cache list operations, tool calls, resource reads, and prompt reads.

```python
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    ListToolsSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)

cache = ResponseCachingMiddleware(
    list_tools_settings=ListToolsSettings(ttl=30),
    call_tool_settings=CallToolSettings(
        ttl=60,
        included_tools=["catalog_lookup"],
    ),
    read_resource_settings=ReadResourceSettings(enabled=False),
    max_item_size=1_048_576,
)
```

Constructor options are `cache_storage`, one settings object for each supported operation, and `max_item_size=1_048_576` bytes. Settings types are:

| Settings | Hook | Installed fields |
| --- | --- | --- |
| `ListToolsSettings` | `on_list_tools` | `enabled`, `ttl` |
| `CallToolSettings` | `on_call_tool` | `enabled`, `ttl`, `included_tools`, `excluded_tools` |
| `ListResourcesSettings` | `on_list_resources` | `enabled`, `ttl` |
| `ReadResourceSettings` | `on_read_resource` | `enabled`, `ttl` |
| `ListPromptsSettings` | `on_list_prompts` | `enabled`, `ttl` |
| `GetPromptSettings` | `on_get_prompt` | `enabled`, `ttl` |

Do not assume every settings type accepts inclusion/exclusion lists; installed source limits those fields to `CallToolSettings`.

The live guide currently says cache keys omit user/session identity, but installed source contradicts that statement: it partitions every cache key by a hash of the raw access token, then adds operation identity and arguments where applicable. It does not add MCP session identity, and every unauthenticated request shares the same anonymous partition. Token partitioning prevents one bearer token from reusing another token's cached result, but it does not make session-state-dependent results safe to cache or separate requests that deliberately share a token. Disable caching for session-specific or mutable actor-specific results unless the missing identity is an explicit, validated part of the cache contract. Invalidate or version caches when schemas, authorization, provider catalogs, token entitlements, or backing data change. Select a storage backend through [Storage backends](storage-backends.md).

### Rate Limiting

- `RateLimitingMiddleware(max_requests_per_second=10.0, burst_capacity=None, get_client_id=None, global_limit=False)` uses a token bucket and permits configured bursts.
- `SlidingWindowRateLimitingMiddleware(max_requests, window_minutes=1, get_client_id=None)` enforces a precise rolling window without token-bucket bursts.

The live example shows a burst capacity of 20 but installed source defaults it to `None`. Supply a stable authenticated or transport-derived client key where per-client limits are required; do not trust a spoofable header. Use `global_limit=True` only when one shared process-wide bucket is the intended contract. A local in-memory limiter does not coordinate replicas.

### Error Handling and Retry

- `ErrorHandlingMiddleware(logger=None, include_traceback=False, error_callback=None, transform_errors=True)` centralizes logging and client-safe error transformation. The installed `transform_errors` default is `True`, despite a different live-guide table.
- `RetryMiddleware(max_retries=3, base_delay=1.0, max_delay=60.0, backoff_multiplier=2.0, retry_exceptions=(ConnectionError, TimeoutError), logger=None)` shares the `fastmcp.server.middleware.error_handling` module. Subclass it when retries must be restricted to explicitly allowlisted tool names.

Never expose tracebacks in production responses. Retry only idempotent operations or operations protected by an idempotency key; cancellation and authorization failures are not transient.

### Ping

`PingMiddleware(interval_ms=30000)` starts its ping loop on the first message and stops with the connection. It is useful for stateful long-lived HTTP connections and has no effect on stateless connections. Confirm the client and intermediary behavior before depending on it.

The teardown race that older releases surfaced to callers is now handled inside the middleware: `_ping_loop` catches `anyio.ClosedResourceError` and `anyio.BrokenResourceError` and returns, and the loop is cancelled through the connection's exit stack (including the case where the connection is built and torn down inside one request on the modern per-request path). Do not add a release-pinned adapter to suppress a terminal ping error; if one appears, it is a real failure worth reporting rather than swallowing.

### Response Limiting

`ResponseLimitingMiddleware(*, max_size=1_000_000, truncation_suffix="\n\n[Response truncated due to size limit]", tools=None)` constrains all tools or the named subset. Every parameter is keyword-only, and the suffix default is that explicit sentence rather than an ellipsis. When a result exceeds the byte limit, FastMCP collapses text content and truncates it; non-text results are serialized to text first.

Truncation replaces a structured result with plain `TextContent`, so the result no longer conforms to a declared `output_schema`. For structured tools, design pagination, bounded queries, or durable result resources instead of relying on truncation.

### Authorization

`AuthMiddleware(auth: AuthCheck | list[AuthCheck])` provides server-wide callable authorization. Keep it distinct from transport authentication and read [Authorization](authorization.md) before configuring it.

### Schema Dereferencing and Tool Injection

Two further built-ins have no live-guide coverage; read their source before use.

- `DereferenceRefsMiddleware` (module `fastmcp.server.middleware.dereference`) inlines `$ref` pointers in advertised tool and resource-template schemas for clients that do not resolve them. **It is already active by default**: `FastMCP(dereference_schemas=...)` defaults to `True` and installs it. Add it by hand only when you have set that flag to `False` and want dereferencing on one narrower path; otherwise you will run it twice.
- `ToolInjectionMiddleware(tools: Sequence[Tool])` (module `fastmcp.server.middleware.tool_injection`) adds server-owned tools to the advertised catalog. Injected tools bypass the local provider's registration path, so confirm authorization, versioning, and transform interaction explicitly rather than assuming parity with declared tools.

## Custom Middleware Patterns

### Deny Correctly

Raise the **typed exception owned by the operation** and let FastMCP translate it:

- tool call: `ToolError`;
- resource read: `ResourceError`;
- prompt retrieval: `PromptError`;
- component absent or disabled: `NotFoundError` / `DisabledError`;
- invalid arguments: `ValidationError`.

Translation to a wire code is centralized in `fastmcp.exceptions.to_mcp_error(exc, *, default_code=INTERNAL_ERROR)`, which request-handler adapters call so codes stay spec-correct and consistent across resources, prompts, and tools. **Middleware should almost never construct `McpError` itself.** Raising the typed exception gets the right code for free and keeps one denial reporting one code everywhere; hand-building the protocol error in a middleware is how a middleware-layer denial starts reporting a different code than the identical denial raised inside a component. See [Authorization](authorization.md) for the full mapping table and why not-found maps to invalid-params.

Because `to_mcp_error` returns an existing `MCPError` **unchanged**, the way to pin a specific code is to raise an exception already carrying it — not to re-map at the call site.

When you genuinely do need `McpError` (a general MCP request with a code no typed exception expresses), note the v4 constructor change. `McpError` is an alias of `MCPError` and its signature is `(code: int, message: str, data: Any = None)`. Both call forms work:

```python
from fastmcp.exceptions import McpError

raise McpError(-32602, "unsupported argument")
raise McpError(code=-32602, message="unsupported argument")
```

What v3 allowed and v4 does not is the **single-argument `ErrorData` wrapper** — `McpError(ErrorData(code=..., message=...))` now raises `TypeError: MCPError.__init__() missing 1 required positional argument: 'message'`. Pass `code` and `message` directly instead. `ErrorData` itself still exists in `fastmcp.exceptions`; it is simply no longer how you construct the exception.

Import from `fastmcp.exceptions`, not `mcp`: `from mcp import McpError` raises `ImportError` because the SDK spells it `MCPError`.

Do not return an error-shaped success value. Preserve typed downstream errors and cancellation unless the owner explicitly maps them to a safer client contract.

### Modify Requests and Responses

Normalize or validate a request before `call_next`, and transform a response after it. Mutate typed message/result objects only through fields supported by the installed release. For substantial tool-schema, argument, name, or result changes, prefer a FastMCP transform because transforms own catalog and execution consistency.

### Filter Catalogs Consistently

If `on_list_tools` hides a tool, `on_call_tool` must deny its direct invocation. Apply the same rule to resources and prompts. Prefer component authorization when the rule represents access control; hand-written visibility alone is not authorization.

Execution hooks do not directly carry all component metadata. Resolve the component through `context.fastmcp_context.fastmcp.get_tool`, `get_resource`, or `get_prompt`, handle a missing result explicitly, and avoid broad exception swallowing.

### Store Request or Session State

State APIs are async in installed source:

```python
ctx = context.fastmcp_context
if ctx is not None:
    await ctx.set_state("actor_id", actor_id, serializable=False)
```

Use `serializable=False` for request-only objects. Use serializable state for cross-request session data only with an explicit store, retention, namespace, and confidentiality policy. Do not keep per-request identity or counters in middleware instance attributes unless the implementation is concurrency-safe, bounded, and deliberately process-local.

### Constructor Configuration and Error Handling

Accept stable owner-provided configuration in `__init__`, such as a policy client, bounded rate, or redacted sink. Do not embed secrets in source, exception strings, or object representations. Treat the middleware instance as shared across concurrent requests: immutable configuration is simplest; mutable maps need bounds, synchronization, expiry, and an explicit process-local contract.

Wrap `call_next` only when the middleware owns the failure policy. Log or classify the error, then re-raise unless the contract deliberately transforms it to a client-safe protocol error. Swallowing a downstream exception turns a failed operation into an undefined success path.

### Audit and Event Records

`on_call_tool` can produce one redacted record containing tool identity, request correlation ID when available, an argument-shape hash, outcome (`completed`, `empty`, `error`, `failed`, or `denied`), error class, and duration. Keep raw inputs and outputs out of the default path. Emit denial before `call_next`; emit success/failure after the result or exception is known. Make the sink failure-tolerant and do not let audit delivery silently change the operation result unless the owning compliance contract requires fail-closed behavior.

## Verification

Exercise middleware through `fastmcp.Client`, not direct hook calls. Cover:

- every built-in imports from its owning submodule, not the package root;
- exact ingress/egress order and one `call_next` per continuing hook;
- deliberate short-circuiting with the correct protocol error, including `McpError(code, message)` construction;
- initialization rejection before continuation and session-unavailable paths;
- parent and child middleware order plus state isolation/sharing;
- every configured built-in default and non-default option;
- payload redaction, retry eligibility, cancellation, limiter identity, and replica behavior;
- cache hit, expiry, invalidation, actor isolation, and backend failure;
- list filtering plus blocked direct access;
- response-limit behavior for text, non-text, and structured results;
- custom request/response mutation and audit records without sensitive data.

Inspect the installed catalog and client-visible errors after the full middleware stack is assembled.
