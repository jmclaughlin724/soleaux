# Dependency Injection

## Source and Version Contract

Use this reference for the complete workflow represented by the live [FastMCP dependency injection guide](https://gofastmcp.com/servers/dependency-injection), verified 2026-07-14. FastMCP's dependency system is powered by the Docket ecosystem's `uncalled-for` library. Core `Depends()`, `Shared()`, request dependencies, and progress ship with the server install. The task-runtime accessors are **not in this module at all** — they moved to the separate `fastmcp-tasks` distribution and now fail at import rather than at resolution.

Confirm exact dependency return types and failure behavior against installed source. See [Version and source routing](version-and-source-routing.md) for the pinned baseline.

**This is the v4 replacement for `exclude_args`**, which is removed and absent from `FastMCP.tool` in the pinned release. Give the parameter a `Depends(factory)` default instead. Two differences matter when migrating: dependency injection carries **no requirement that the parameter have a default value** (`exclude_args` rejected parameters without one), and like `exclude_args` it operates on **top-level callable parameters only** — neither mechanism can hide a field nested inside a Pydantic request model. FastMCP excludes a resolved dependency parameter from the published MCP input schema automatically, so no separate hiding directive is needed.

`fastmcp.dependencies.__all__` carries exactly **11** names in the pinned release:

`Depends`, `Dependency`, `Shared`, `Progress`, `ProgressLike`, `TokenClaim`, `CurrentContext`, `CurrentFastMCP`, `CurrentHeaders`, `CurrentRequest`, `CurrentAccessToken`.

`CurrentDocket` and `CurrentWorker` are **no longer exported from this module**. Both now fail at import time, not at resolution time:

```
ImportError: 'CurrentDocket' moved to the fastmcp-tasks package. Install it with
`pip install 'fastmcp[tasks]'` and import from `fastmcp_tasks.dependencies`.
```

`CurrentWorker` raises the same shape. Because this is an import-time failure, a stale module cannot start — which is the safer behavior, but it also means the fix is an import-path change plus a manifest change, not a manifest change alone. See [Background tasks](tasks.md).

## Resolution Model

Declare a recognized dependency as a parameter annotation or default. FastMCP resolves it when a tool, resource, resource template, or prompt runs and excludes the dependency parameter from the MCP input schema.

```python
from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

mcp = FastMCP("service")

@mcp.tool
async def search(query: str, ctx: Context = CurrentContext()) -> str:
    await ctx.info("search started")
    return await run_search(query)
```

A `Context` annotation alone is retained for compatibility. Prefer an explicit default such as `CurrentContext()` in new code because it makes the runtime dependency visible. Dependency parameters remain invisible to clients in either form.

Dependencies are request-scoped and cached per request. Reusing the same dependency directly or through nested dependencies resolves it once and shares that value within the request. They are not a cross-request singleton mechanism.

## Built-In Dependencies

| Need | Parameter default | Helper access | Behavior |
| --- | --- | --- | --- |
| MCP request context | `CurrentContext()` | `get_context()` | Logging, progress, session state, resource/prompt access, elicitation; helper raises outside a server request |
| Server instance | `CurrentFastMCP()` | `get_server()` | Current `FastMCP`; use only for server-owned introspection/operations |
| Starlette HTTP request | `CurrentRequest()` | `get_http_request()` | HTTP transports only; raises `RuntimeError` without HTTP context |
| Safe HTTP headers | `CurrentHeaders()` | `get_http_headers()` | Returns a normalized dict and gracefully returns empty when HTTP is unavailable |
| Required access token | `CurrentAccessToken()` | — | Raises when no authenticated token exists |
| Optional access token | — | `get_access_token()` | Returns `AccessToken | None` |
| One token claim | `TokenClaim(name)` | — | Extracts the named claim; raises with available claims when missing |
| Request-scoped service | `Depends(factory)` | — | Resolved once per request and cached across the request's dependency graph |
| App-scoped service | `Shared(factory)` | — | Resolved once per `SharedContext` and reused for every later resolution; identity is the factory function |
| Task progress | `Progress()` | — | Task-enabled component; atomic total, increment, and message updates |

`Shared` and `Depends` accept the same factory forms (sync function, async function, sync generator, async generator) and differ only in lifetime: `Depends` is per request, `Shared` is per `SharedContext`. Context-manager factories are cleaned up when their owning scope exits. Do not use `Shared` for anything actor-, tenant-, or request-specific.

### MCP Context and Server

Use `CurrentContext()` when the component needs MCP-negotiated behavior. Use `get_context()` only in a deep server helper where threading the dependency explicitly would add disproportionate noise; ambient access hides coupling and raises outside a request.

Use `CurrentFastMCP()` or `get_server()` for server-level assembly or introspection. Application services should depend on their own narrow interfaces rather than the entire server object.

### HTTP Request and Headers

`CurrentRequest()` and `get_http_request()` expose a Starlette `Request` only for SSE or Streamable HTTP. Both raise outside HTTP, including STDIO.

For a background task originating from HTTP, FastMCP restores a minimal request backed by snapshotted originating headers. It does not preserve the live socket or full request body. Prefer `CurrentHeaders()` or `get_http_headers()` when the code must degrade safely across transports and task execution.

`get_http_headers(include_all=False, include=None)` excludes problematic hop/request framing headers such as `host` and `content-length` by default. Use `include_all=True` only when the owner has reviewed every consumer. Use `include={...}` to request a bounded allowlist. Header presence is not proof of identity.

### Access Tokens and Claims

`CurrentAccessToken()` is strict; `get_access_token()` is optional. The installed `AccessToken` includes the raw token, client ID, scopes, expiry timestamp, resource, subject, and claims. Do not return or log the raw token or full claims.

`TokenClaim("name")` is useful when exactly one validated claim is needed. Common identity-provider conventions include:

| Provider family | User ID | Email   | Display name |
| --------------- | ------- | ------- | ------------ |
| GitHub          | `sub`   | `email` | `name`       |
| Google          | `sub`   | `email` | `name`       |
| Auth0           | `sub`   | `email` | `name`       |

Treat this as a mapping aid, not a security guarantee. Verify the configured issuer's actual claim contract, then map the claim to an authoritative application actor. A client ID, subject, role, or tenant claim does not by itself authorize a resource operation. Read [Authorization](authorization.md).

### Task Dependencies

`Progress()` ships in this module and supports request progress as well as task progress. The task-runtime accessors do **not**: `CurrentDocket` and `CurrentWorker` left this module for the separate `fastmcp-tasks` distribution and now raise `ImportError` on access. See [Background tasks](tasks.md).

## Custom Dependencies with Depends

`Depends(factory)` accepts synchronous functions, async functions, and async context managers. Use it to inject configuration, repositories, API clients, actor mappings, or other request-scoped services.

```python
from contextlib import asynccontextmanager

from fastmcp.dependencies import Depends, TokenClaim

@asynccontextmanager
async def database_session():
    session = await database.open_session()
    try:
        yield session
    finally:
        await session.close()

async def load_actor(
    subject: str = TokenClaim("sub"),
    session=Depends(database_session),
):
    return await actor_repository.require_actor(session, subject)

@mcp.tool
async def list_orders(
    actor=Depends(load_actor),
    session=Depends(database_session),
) -> list[dict[str, object]]:
    return await order_repository.list_for_actor(session, actor.id)
```

Both `load_actor` and the tool receive the same `database_session` value during one request because dependency resolution is cached across the nested dependency graph.

### Resource Management

Use an async context manager dependency for request-level resources. Cleanup runs after component execution even when the operation raises. Put server-wide connection pools, model clients, or registries in [Lifespan](lifespan.md), then inject a narrow handle per request. Do not create a new shared pool in every dependency call.

Ensure cleanup is cancellation-safe, bounded, and idempotent. If dependency setup partially succeeds and then fails, close every acquired resource before propagating the error.

### Nested Dependencies

Dependencies may depend on other dependencies. FastMCP resolves the graph in dependency order and applies per-request caching to shared nodes. Keep the graph acyclic and shallow enough to understand. Avoid hidden network calls in widely shared dependencies; make expensive authorization or data loads explicit and test their failure behavior.

Custom dependency subclasses are a **local** capability and need no extra. `Dependency` is exported from this module as a subclassable base: implement `__aenter__` to produce the injected value and, when cleanup is needed, `__aexit__`, which runs in reverse resolution order. Setting `single = True` on the subclass (it defaults to `False`) rejects a signature declaring more than one instance of it. `Progress` is itself such a subclass — read it as the worked example.

Task-argument accessors are the exception: they require a task execution context this server never establishes, so they belong to the separate `fastmcp-tasks` distribution and are not importable here. Their exact surface is upstream-documented rather than verified locally, since the extra is not installed. See [Background tasks](tasks.md).

## Selection Rules

- Use a normal MCP input parameter for client-supplied validated data.
- Use `CurrentContext()` for MCP request capabilities.
- Use `CurrentRequest()` only for HTTP-specific behavior that cannot use bounded headers.
- Use `CurrentHeaders()` for transport-tolerant header access.
- Use `CurrentAccessToken()` when authentication is mandatory and `get_access_token()` when an explicitly anonymous path exists.
- Use `Depends()` for request-scoped application services and cleanup.
- Use `Shared()` for app-scoped services that are safe to reuse across every call.
- Use lifespan state for process/server-lifetime resources.
- Use session state for serializable cross-request MCP session data, not dependency caching.
- Do not import `CurrentDocket` or `CurrentWorker` from this module; they live in `fastmcp_tasks.dependencies` and require the `tasks` extra.

## Verification

Test dependency behavior through `fastmcp.Client` and every supported transport. Cover:

- dependency parameters are absent from the exposed MCP schema;
- annotation-based and explicit `CurrentContext()` injection where compatibility matters;
- helper failure outside a request;
- HTTP request failure under STDIO and safe empty-header fallback;
- excluded/default headers plus bounded `include` and reviewed `include_all` behavior;
- authenticated, unauthenticated, and missing-claim paths without token leakage;
- sync, async, context-manager, and nested dependency resolution;
- one resolution per request and fresh resolution across requests;
- `Shared()` resolving once per `SharedContext` and its cleanup on scope exit;
- cleanup on success, exception, cancellation, and partial setup failure;
- that `from fastmcp.dependencies import CurrentDocket` raises `ImportError` at import time, so a stale module fails to start rather than failing mid-request;
- authorization re-evaluation when dependencies load mutable actor or tenant state.
