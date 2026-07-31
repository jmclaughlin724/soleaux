# Lifespan

## Source and Version Contract

Use this reference for the complete workflow represented by the live [FastMCP lifespan guide](https://gofastmcp.com/servers/lifespan), verified 2026-07-14. Lifespans are available in FastMCP 3.0+ and run once per server start/stop, regardless of how many clients or MCP sessions connect. Confirm exact types against installed source. See [Version and source routing](version-and-source-routing.md) for the pinned baseline.

The lifespan surface described here was re-verified intact on the pinned release: `@lifespan`, `ContextManagerLifespan`, `|` composition, and `combine_lifespans` all match this document.

## Define Startup and Teardown

Use `@lifespan` for server-level resources such as connection pools, shared HTTP clients, registries, or initialized application services. Yield a dictionary; its values become the server lifespan context.

```python
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

@lifespan
async def service_lifespan(server: FastMCP):
    client = await create_api_client()
    try:
        yield {"api_client": client}
    finally:
        await client.aclose()

mcp = FastMCP("service", lifespan=service_lifespan)
```

Always protect cleanup with `try/finally` so cancellation and normal shutdown take the same teardown path. If startup fails before `yield`, close any resources already acquired and let the server fail to start rather than publishing a partially initialized service.

Lifespan is not per request or per session. Do not put actor identity, request metadata, mutable tenant selection, or one client's state in the lifespan context.

## Access Lifespan Context

Inside a component, use the injected `Context` and read `ctx.lifespan_context`.

```python
from fastmcp import Context

@mcp.tool
async def health_probe(ctx: Context) -> dict[str, bool]:
    client = ctx.lifespan_context["api_client"]
    return {"upstream_ready": await client.health()}
```

`api_client` here is the upstream service client the lifespan created, not a FastMCP `Client`, so `health()` is whatever that client exposes. Do not reach for `Client.ping()` in a probe like this: it is unavailable on the modern protocol era. See [Clients and transports](clients-and-transports.md).

Treat context keys as an internal typed contract. Prefer a dataclass or narrow service object when a loose dictionary would obscure ownership. Validate a required key during startup rather than discovering it on the first request.

## Compose Lifespans

Compose FastMCP lifespan objects with `|`:

```python
combined = configuration_lifespan | database_lifespan | telemetry_lifespan
mcp = FastMCP("service", lifespan=combined)
```

Composition semantics are observable:

- entry occurs left to right;
- exit occurs right to left;
- yielded context dictionaries are merged;
- a later lifespan overwrites an earlier value with the same key.

Use unique owned keys. Do not rely on overwrite behavior to patch another lifespan's value. If one lifespan depends on another, make the ordering explicit and test both startup and reverse teardown.

## Legacy Async Context Managers

An existing `@asynccontextmanager` lifespan can still be passed directly to `FastMCP`.

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def legacy_lifespan(server):
    yield {"legacy_service": await create_service()}

mcp = FastMCP("service", lifespan=legacy_lifespan)
```

To compose that callable with `@lifespan` objects, wrap it in `ContextManagerLifespan` first:

```python
from fastmcp.server.lifespan import ContextManagerLifespan

combined = ContextManagerLifespan(legacy_lifespan) | modern_lifespan
```

Prefer `@lifespan` for new FastMCP-owned code. Preserve a legacy context manager when another framework owns its lifecycle or migration is outside scope.

## FastAPI Integration

When a FastMCP ASGI app is mounted inside FastAPI, both applications' lifespans must run. Build the MCP app first, combine the FastAPI lifespan with `mcp_app.lifespan`, then mount the same app instance.

```python
from fastapi import FastAPI
from fastmcp.utilities.lifespan import combine_lifespans

mcp_app = mcp.http_app()
app = FastAPI(
    lifespan=combine_lifespans(fastapi_lifespan, mcp_app.lifespan),
)
app.mount("/mcp", mcp_app)
```

Do not call `mcp.http_app()` twice and combine one instance while mounting another. Validate route ownership, proxy/root path behavior, and shutdown under the production ASGI server.

## Startup Validation for Task-Enabled Tools

Server startup now performs one extra check that can fail a server which previously started fine. After the user lifespan and all provider lifespans have entered, `_validate_task_extension_registered()` runs (`fastmcp/server/mixins/lifespan.py`).

`@mcp.tool(task=True)` is **declared intent only** — the engine that runs it lives in the separate tasks package. If any registered tool declares task support and **no `ServerExtension` with the tasks extension identifier is registered**, startup raises:

```text
RuntimeError: Task-enabled tools (<names>) require the tasks extension, but no
extension with identifier 'io.modelcontextprotocol/tasks' is registered. Install
it with `pip install 'fastmcp[tasks]'` and register it via
`mcp.add_extension(TasksExtension(...))`.
```

This is deliberate: a task-configured tool serving without the extension would silently never run as a task, so the failure is moved to serve time. Two details matter when diagnosing it. A **mounted child defers to the root**, which owns the registration — the check returns early for a child, so validate at the root server. And the candidate list is re-filtered by actual task config after server-level transforms run, because transforms can inject synthetic non-task tools into `get_tasks()`.

Because this repository does not install the tasks extra, any `task=True` tool will fail startup here. See [Background tasks](tasks.md).

## Implementation Rules

- Initialize only server-lifetime resources; use [Dependency injection](dependency-injection.md) for request-level acquisition and cleanup.
- Keep initialization bounded with explicit timeouts and fail closed when a required dependency is unavailable.
- Make teardown idempotent and cancellation-safe.
- Start background workers only through the installed task/lifespan owner; do not create untracked `asyncio` tasks.
- Drain or cancel owned work before closing dependencies it uses.
- Do not expose secrets or mutable service handles directly in MCP results.
- For mounted servers and custom providers, identify which owner starts and stops each resource exactly once.
- Avoid reusing a resource across event loops or process forks unless its library explicitly supports it.

## Verification

Exercise the actual server lifecycle, not only the lifespan generator. Cover:

- setup runs once across multiple client sessions;
- context values are available to tools, resources, templates, and prompts;
- cleanup runs once on normal shutdown and cancellation;
- partial startup failure closes earlier acquisitions and prevents serving;
- composed entry order, reverse exit order, and context-key collisions;
- legacy direct use and explicit `ContextManagerLifespan` composition;
- FastAPI and FastMCP lifespans both run for the mounted app;
- a `task=True` tool without the tasks extension fails startup with the extension `RuntimeError`, and a mounted child defers that check to its root;
- providers, middleware, and task workers do not double-start or outlive their dependencies;
- shutdown honors production drain timeouts and leaves no untracked work.
