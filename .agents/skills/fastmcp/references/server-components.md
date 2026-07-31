# Server Components

## Purpose and Source Discipline

Use this reference for the FastMCP server and its tools, resources, resource templates, and prompts. It consolidates the focused live guides without turning the skill into a copy of the documentation site.

Exact imports, signatures, defaults, return shapes, and feature availability come from installed source, not from the live pages. Start with [Version and source routing](version-and-source-routing.md) for the pinned baseline, inspect the installed signatures, and treat this reference as an implementation checklist.

Known routing facts:

- use the unified `FastMCP(..., on_duplicate=...)`; `on_duplicate_tools`, `on_duplicate_resources`, and `on_duplicate_prompts` were removed in v3;
- use `mcp.enable()` and `mcp.disable()`; the live component pages still show decorator-level `enabled` as deprecated, but the installed decorator signatures do not accept it;
- decorators expose version-specific options such as `title`, `auth`, `task`, `app`, or `security`; use only the options present on the selected component signature, because the three decorators no longer share one option set;
- `Context.read_resource()` returns `ResourceResult`, not a bare list; the live Context page still shows older examples.

### Protocol Types Moved and Renamed

Two v3 habits break here, and both are worth a pre-migration grep.

**Import path.** Protocol types come from the standalone `mcp_types` distribution. The stable MCP SDK restored `mcp.types` as a permanent alias of `mcp_types` (same objects), so `from mcp.types import ...` now _runs_ — but `mcp.types` requires the full `mcp` package, which sits behind an extra, while `mcp_types` is a core dependency that always resolves. Write `from mcp_types import ...` in library code so the import survives a bare `fastmcp-slim` install.

**Field spelling.** MCP SDK v2 renamed protocol fields from camelCase to snake_case: `inputSchema` → `input_schema`, `outputSchema` → `output_schema`, `mimeType` → `mime_type`, `uriTemplate` → `uri_template`, `nextCursor` → `next_cursor`, `structuredContent` → `structured_content`, `isError` → `is_error`, `serverInfo` → `server_info`, `readOnlyHint` → `read_only_hint`.

`settings.mcp_camelcase_compat` defaults to `True`, which installs warn-once `@property` shims over a documented subset of these reads. A stale `page.nextCursor` therefore still returns the right value while emitting one `FastMCPDeprecationWarning` per class/field pair. Treat that as a **migration aid, not a contract**: the shim covers only fields the bridge enumerates, setting `fastmcp.settings.mcp_camelcase_compat = False` turns it into an `AttributeError` at read time, and it is documented in source as slated for removal. Two gaps worth knowing:

- `Icon` is **not** bridged — `icon.mimeType` raises `AttributeError` even with compat enabled.
- The bridge is read-side only. Construction still accepts camelCase through pydantic aliases with no warning at all, so `Icon(mimeType=...)` silently succeeds and gives no migration signal.

Rename on sight rather than relying on the shim, and run the suite with the setting disabled to find every remaining read.

## The FastMCP Server

`FastMCP` owns the exposed component catalog, MCP communication, and server lifecycle.

```python
from fastmcp import FastMCP

mcp = FastMCP(
    "analytics",
    instructions="Use summary before requesting detailed analysis.",
)
```

Server instructions are client-visible usage guidance. Keep authentication, authorization, input validation, and mutation policy in executable server code rather than relying on instructions.

### Constructor Options

Confirm the constructor in the installed release; it is 26 parameters wide. The options group as follows.

| Group | Option | Guidance |
| --- | --- | --- |
| Identity | `name` | Human-readable server identity. Choose a stable product or capability name. The signature accepts `None`, and an unnamed server gets a random per-process name — which also defeats `request_state_security` audience binding across replicas, so name production servers explicitly. |
| Identity | `instructions` | Explain purpose, sequencing, and intended component usage to clients and models. |
| Identity | `version` | Server version advertised to clients; do not confuse it with individual component versions. |
| Identity | `website_url` | Optional information URL shown by capable clients. |
| Identity | `icons` | Optional MCP `Icon` representations for capable clients. |
| Identity | `experimental_capabilities` | Mapping placed only in `capabilities.experimental`; it does not change derived tools/resources/prompts capabilities. Use only for an owned interop contract. |
| Composition | `tools` | Sequence of `Tool` objects or callables to register without decorators. |
| Composition | `auth` | HTTP transport authentication provider. Application authorization remains separate. |
| Composition | `middleware` | Ordered interceptors for requests, responses, and notifications. |
| Composition | `providers` | Dynamic or composed component providers queried during catalog access. |
| Composition | `transforms` | Server-wide component transforms, including catalog search or presentation changes. |
| Composition | `lifespan` | Composable startup/shutdown owner for shared dependencies. |
| Behavior | `on_duplicate` | One of `warn`, `error`, `replace`, or `ignore`; prefer `error` during assembly unless intentional replacement is tested. |
| Behavior | `strict_input_validation` | `False` (default) permits Pydantic-compatible coercion; `True` rejects JSON Schema type mismatches before invocation. |
| Behavior | `mask_error_details` | Replaces internal tool/resource failures with a generic client error; otherwise follows `FASTMCP_MASK_ERROR_DETAILS`. |
| Behavior | `list_page_size` | Maximum page size for list operations; `None` returns the full catalog in one response. Zero and negative values raise `ValueError("list_page_size must be a positive integer")` during construction. |
| Behavior | `tasks` | Declares intent only. It does **not** start a worker — see the tool `task` option below for the extension requirement. |
| Behavior | `client_log_level` | Default client log threshold: `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, or `emergency`. A client may override it per session. |
| Behavior | `dereference_schemas` | Defaults to `True` so clients receive flattened schemas rather than `$ref` graphs. Disable only for clients known to support references. |
| Security | `resource_security` | Path-safety policy screening extracted resource-template parameters. **Defaults on** to `ResourceSecurity(reject_path_traversal=True, reject_absolute_paths=True, reject_null_bytes=True, exempt_params=frozenset())`. Pass `None` to disable screening server-wide; prefer a per-component `security=` exemption. |
| Security | `request_state_security` | `RequestStateSecurity` policy sealing `requestState`: codec, TTL, principal binding, and audience. Defaults to `None` (per-process ephemeral key). Takes exactly one of `keys=` or `codec=`; `keys[0]` seals and every key unseals, giving a rotation ring. A shared-key multi-replica deployment must also set a stable `audience=` or a named server, or replicas will reject each other's sealed state. |
| Caching | `cache_ttl` / `cache_scope` | Server-level cache hints applied uniformly to every cacheable result. `cache_scope` is `"public"` or `"private"`. Invalid values raise during construction. |
| Handler/state | `session_state_store` | `AsyncKeyValue` implementation for session state; defaults to in-memory storage. |

Version-gate options before using them, and note that `**kwargs` absorbs unknown names — a typo in an option is not a `TypeError`. Assert the effective setting on the constructed server rather than trusting that the keyword took effect.

### Resource Path-Safety Screening

`resource_security` is the one constructor default that rejects requests on its own, so it is worth understanding before a template read starts failing.

Screening runs **after** a URI matches a template and its parameters are extracted and percent-decoded, and **before** the resource function is called. That ordering catches traversal regardless of encoding — literal `../`, `%2F`, `%5C`, and `%2E%2E` are all normalized first. A failing read raises `ResourceSecurityError`, which subclasses `NotFoundError` so an attacker cannot distinguish a screened parameter from a missing resource.

The traversal check is component-based, not substring-based: `HEAD~3..HEAD`, `v1..v2`, and `file.tar.gz` all pass because none contains a standalone `..` segment. Wildcard `{path*}` parameters are screened element by element.

Exempt a parameter only when its values legitimately contain traversal-shaped text:

```python
from fastmcp.resources import ResourceSecurity

@mcp.resource("git://diff/{ref}", security=ResourceSecurity(exempt_params={"ref"}))
def git_diff(ref: str) -> str: ...
```

`exempt_params` accepts either spelling of a hyphenated URI variable: `{git-ref}` extracts as `git_ref`, and an exemption written either way matches. The per-component `security=` parameter defaults to the `INHERIT_SECURITY` sentinel, which defers to the server policy; an explicit `None` disables screening for that one component. Exempting a parameter removes the check, not the risk — the resource function still owns path containment.

### Running and Transports

Guard direct execution so subprocess-based MCP clients and the FastMCP CLI can import the module without starting a second server.

```python
if __name__ == "__main__":
    mcp.run()
```

Select the transport deliberately:

- `stdio` is the default. One client launches and owns one server process, MCP messages use stdin/stdout, and the process normally exits with that client. Use it for desktop clients, command-line tools, local development, tests, and other single-user process integrations. Keep stdout protocol-clean.
- `http` / `streamable-http` exposes the server at a URL, supports multiple concurrent clients and bidirectional MCP operations, and is the normal network/remote-deployment choice. A direct `run(transport="http", host=..., port=...)` server is the simplest standalone shape; an ASGI app from `http_app()` is the more controllable production or web-framework shape.
- `sse` is the legacy HTTP transport. Keep it only for clients that cannot use Streamable HTTP; it has weaker bidirectional behavior and does not support auto-reload or stateless mode on the pinned release.
- Host, port, path, Uvicorn configuration, middleware, JSON response mode, statelessness, origin protection, and allowlists are transport/run options, not `FastMCP` constructor options. `http_app()` takes them as named parameters (`path`, `middleware`, `json_response`, `stateless_http`, `transport`, `event_store`, `retry_interval`, `host_origin_protection`, `allowed_hosts`, `allowed_origins`, `session_idle_timeout`), while `run()`/`run_async()` accept `transport` and `show_banner` and forward the rest as `**transport_kwargs`. Because that forwarding is untyped, a misspelled run option is not a `TypeError` — assert the bound host, port, and path on the running server.

`run()` is a synchronous wrapper that creates and owns an event loop. Use `await run_async(...)` from an existing async application; calling `run()` inside an async function fails because an event loop is already running. Both accept the same high-level transport choice and forward transport-specific options.

Choose one initialization owner:

- A directly executed module uses the guarded `mcp.run(...)` call.
- A CLI-only module exports a discoverable `mcp`, `server`, or `app` object and needs no guarded run call.
- An ASGI deployment exports `app = mcp.http_app(...)`, or calls a factory that constructs the server and returns that app, for Uvicorn, Gunicorn, Hypercorn, FastAPI, or Starlette.

The default installed HTTP endpoint is `/mcp`; deployed examples may render it with a trailing slash. Construct and test the exact external URL after every proxy or framework mount instead of assuming slash normalization.

The CLI may run the same server and override transport settings without source edits. Load [CLI, testing, and migrations](cli-testing-and-migrations.md) for environment flags, argument forwarding, reload limits, and `fastmcp.json`. Verify the installed command and deployment wrapper rather than assuming `mcp.run()` is the production entrypoint.

### Lifespan and Shared Dependencies

Use lifespan for shared clients, pools, caches, workers, and other dependencies that require deterministic startup and shutdown. Do not open expensive resources at import time. Make lifespan state available through supported dependency injection, and prove teardown under normal exit, failure, and cancellation.

### Visibility and Tags

Visibility applies to tools, resources, templates, and prompts and affects both listing and access.

- `mcp.enable(...)` and `mcp.disable(...)` can select by `names`, canonical `keys`, `version`, `tags`, and component kinds (`tool`, `resource`, `template`, `prompt`).
- `enable(..., only=True)` creates an allowlist view.
- Later rules win, so apply exclusions after a broad allowlist.
- Use keys for exact identity (`tool:name`, `resource:uri`, `prompt:name`) and tags for grouped policy.
- Visibility metadata is not authorization. Re-check the principal and target inside protected operations.

```python
mcp.enable(tags={"public"}, only=True).disable(tags={"deprecated"})
```

Inspect the final catalog after providers, transforms, version resolution, and visibility rules.

### Catalog Pagination

FastMCP can paginate the four MCP component-list operations. Leave `list_page_size=None` for the backward-compatible single response. Set a positive integer to cap every page; zero and negative values raise `ValueError` during server construction.

```python
from fastmcp import FastMCP

mcp = FastMCP("component-registry", list_page_size=50)
```

The one setting applies uniformly:

| MCP operation | Auto-paginating client method | Raw/manual client method |
| --- | --- | --- |
| `tools/list` | `list_tools(max_pages=250)` | `list_tools_mcp(cursor=...)` |
| `resources/list` | `list_resources(max_pages=250)` | `list_resources_mcp(cursor=...)` |
| `resources/templates/list` | `list_resource_templates(max_pages=250)` | `list_resource_templates_mcp(cursor=...)` |
| `prompts/list` | `list_prompts(max_pages=250)` | `list_prompts_mcp(cursor=...)` |

Each raw result contains the operation's items plus **`next_cursor`** — snake_case, renamed from v3's `nextCursor`. Start with no cursor, pass each non-null cursor unchanged to the same operation and session, and stop when it is absent. Cursors are opaque MCP values even though the installed implementation currently encodes a base64 offset; never parse, construct, persist as a durable bookmark, or reuse one across servers/catalogs.

```python
from fastmcp import Client

async with Client(mcp) as client:
    # Convenience path: fetch and combine every page.
    all_tools = await client.list_tools()

    # Manual path: process one page at a time.
    page = await client.list_tools_mcp()
    while True:
        await process(page.tools)
        if page.next_cursor is None:
            break
        page = await client.list_tools_mcp(cursor=page.next_cursor)
```

`page.nextCursor` still resolves under the default camelCase compat shim and emits one `FastMCPDeprecationWarning`, so a v3 pagination loop keeps working rather than breaking loudly. That makes it easy to miss; grep for the camelCase spelling instead of waiting for a failure.

`list_tools_mcp` also takes `cache_mode` (`"use"`, `"refresh"`, or `"bypass"`), which selects how the client response cache participates in the page fetch. Use `"bypass"` when a multi-page walk must observe live catalog state.

The convenience methods default to `max_pages=250`, stop with a warning if a server repeats a cursor, and raise if the page limit is exhausted. Choose manual pagination for bounded memory, early termination, per-page progress, or a catalog that can exceed the auto-fetch limit. Set a deliberate lower `max_pages` when an untrusted remote server could force excessive enumeration.

Enable pagination when providers or configuration can expose many components, initial catalog latency matters, or clients need incremental processing. For a fixed modest catalog (the live guide uses fewer than 100 as a heuristic), one response is usually simpler. Page formation occurs after provider/transform resolution, visibility, and version deduplication. Because the installed cursors decode to offsets, a catalog that changes during a multi-page walk can cause duplicates or omissions; this is an inference from the implementation, so keep one enumeration logically stable or make consumers tolerant of identity-based deduplication.

Verify disabled/session-visible components, provider and transform output, exact page boundaries, an exact multiple of the page size, an empty catalog, malformed/stale cursors, repeated cursors from a test server, `max_pages`, and all four operations. Ensure pagination changes discovery only, not direct access or authorization.

### Icons and Website Presentation

Icons are accepted on the server and on tools, concrete resources, resource templates, and prompts. They are optional presentation metadata: clients may ignore them, and they must never carry identity, state, authorization, or instructions required for correct execution.

`mcp_types.Icon` has four fields:

| Field | Requirement | Guidance |
| --- | --- | --- |
| `src` | Required | Stable HTTPS URL or `data:` URI for the image. Never include credentials or expiring bearer query parameters. |
| `mime_type` | Optional | Accurate media type such as `image/png` or `image/svg+xml`; supply it when known. Renamed from v3's `mimeType`. |
| `sizes` | Optional | Size descriptors such as `["48x48", "96x96"]`; use `["any"]` only when the format is genuinely scalable. |
| `theme` | Optional | `"light"` or `"dark"`. Supply a pair of icons when the asset needs different treatment per client theme; omit it for a theme-neutral image. |

`Icon` is imported from `mcp_types`, and it is one of the models the camelCase compat shim does **not** bridge. `Icon(mimeType=...)` still constructs successfully through the pydantic alias, but reading `icon.mimeType` afterwards raises `AttributeError` — so a stale spelling survives registration and fails only in whatever asserts on it. Write `mime_type=` on both sides.

Server icons are advertised with server identity. Pair them with `website_url` when a capable client should show a product/home page, and provide multiple resolutions when clients need different display densities.

```python
from fastmcp import FastMCP
from mcp_types import Icon

mcp = FastMCP(
    "weather",
    website_url="https://weather.example.com",
    icons=[
        Icon(
            src="https://weather.example.com/icon-48.png",
            mime_type="image/png",
            sizes=["48x48"],
        ),
        Icon(
            src="https://weather.example.com/icon-96.png",
            mime_type="image/png",
            sizes=["96x96"],
        ),
    ],
)
```

Use the same `icons=[...]` option on `@mcp.tool`, `@mcp.resource` for concrete resources or templates, and `@mcp.prompt`:

```python
calculator_icon = Icon(
    src="https://example.com/calculator.png",
    mime_type="image/png",
    sizes=["48x48"],
)

@mcp.tool(icons=[calculator_icon])
def add(left: int, right: int) -> int:
    return left + right

@mcp.resource(
    "user://{user_id}/profile",
    icons=[Icon(src="https://example.com/user.svg", mime_type="image/svg+xml", sizes=["any"])],
)
def profile(user_id: str) -> str:
    return load_profile(user_id)

@mcp.prompt(icons=[Icon(src="https://example.com/review.png", mime_type="image/png")])
def review_code(code: str) -> str:
    return f"Review this code:\n\n{code}"
```

For a small local asset, embed a data URI to remove hosting/network availability from rendering. `fastmcp.utilities.types.Image(path=...).to_data_uri()` reads and base64-encodes the file and infers the MIME type from the extension. `Image(data=...)` defaults to PNG; set `format=` on `Image` or pass `mime_type=` to `to_data_uri()` when the raw bytes use another format.

```python
from fastmcp.utilities.types import Image

embedded = Icon(
    src=Image(path="./assets/brand/favicon.png").to_data_uri(),
    mime_type="image/png",
    sizes=["48x48"],
)
```

Prefer hosted HTTPS assets when the same optimized image is reused broadly and can be served with stable cache/content-type policy. Prefer data URIs when the asset is small and self-containment matters. Account for base64 expansion and repeated catalog payload size; do not embed large images. Treat remote SVG/URLs and data URIs as untrusted presentation input at the client boundary, and follow the owning client's content-security and sanitization policy.

Verify server initialization metadata plus all four component catalogs through a capable client. Assert exact `src`, `mime_type`, `sizes`, and `theme`, test URL and data-URI forms, fetch hosted assets from the deployment network, check multiple display densities, and confirm a client that ignores icons still exposes every component correctly.

### Custom HTTP Routes

`@mcp.custom_route(path, methods, name=None, include_in_schema=True)` adds an ASGI route beside the MCP endpoint when using HTTP. Use it for small health, readiness, status, or simple webhook endpoints. Mount the MCP app into FastAPI or Starlette when the surrounding web application is more complex.

Custom routes are **not** protected by the server `AuthProvider`. Keep health/readiness responses public, minimal, and secret-free. If a neighboring HTTP route requires authentication or framework dependencies, mount FastMCP in FastAPI/Starlette and secure that route through the parent application. Apply the deployment's origin, body-size, rate-limit, and error-sanitization contract explicitly to every custom route.

## Choose the Component

| Requirement                                     | Component         |
| ----------------------------------------------- | ----------------- |
| Execute an action or calculation from arguments | Tool              |
| Read one addressable, passive data item         | Resource          |
| Read a URI-parameterized family of passive data | Resource template |
| Construct reusable model/user messages          | Prompt            |
| Supply or compose a component group dynamically | Provider          |

Preserve this semantic distinction because clients discover, authorize, cache, and present each type differently.

## Tools

Tools expose typed Python functions for client invocation. FastMCP derives the name, description, input schema, validation, and result conversion from the function plus decorator metadata. The invocation flow is: client arguments, boundary validation, function execution, then MCP content and optional structured output.

### Registration and Decorator Options

Use `@mcp.tool` for functions. It registers immediately, so for instance/class methods apply standalone `fastmcp.tools.tool` metadata and add the bound method with `mcp.add_tool(...)`; otherwise `self` or `cls` can leak into the schema.

Tools cannot use `*args` or `**kwargs` because MCP requires a complete argument schema.

`@mcp.tool` exposes 16 parameters:

| Option | Guidance |
| --- | --- |
| `name_or_fn` | Positional; the decorated function, or a name string when used as `@mcp.tool("custom_name")`. |
| `name` | Stable action-oriented MCP name; defaults to the function name. |
| `version` | Component version used by version resolution. |
| `title` | Human-friendly display title where supported. |
| `description` | Client/model-facing purpose; overrides the function docstring summary. |
| `icons` | Optional component icons. |
| `tags` | Visibility and grouping labels. |
| `output_schema` | Explicit object-root JSON Schema, `None`, or inferred behavior; validate the return against it. |
| `annotations` | `ToolAnnotations` or mapping of behavioral hints. |
| `meta` | Static definition metadata returned with the listed tool. |
| `app` | MCP Apps configuration; load the Apps reference and verify host support before use. |
| `task` | Background-task intent. **Tool-only** — see below. |
| `timeout` | Foreground execution timeout in seconds. |
| `auth` | Component-level authorization checks. |
| `run_in_thread` | For synchronous functions, defaults to thread-pool dispatch; `False` runs inline for true thread-affinity requirements. |

Three v3 options are **absent** from the installed signature; each is a `TypeError`, not a warning:

| Removed | Replacement |
| --- | --- |
| `exclude_args` | A `Depends(factory)` default from `fastmcp.dependencies`. Dependency parameters are excluded from the MCP schema automatically with no defaults requirement, whereas `exclude_args` covered only top-level parameters that already had defaults and never reached fields nested inside a Pydantic request model. |
| `serializer` | Return `ToolResult` explicitly with the representation you want. |
| `decorator_mode` | No equivalent; the decorator's call forms are fixed. |

Decorator `enabled` was deprecated in the live guide and is absent here. Use server visibility controls.

`task=` is the option most likely to move between components during a migration, and it does not travel. It exists on `@mcp.tool` only:

```text
@mcp.resource("data://x", task=True) -> TypeError: FastMCP.resource() got an unexpected keyword argument 'task'
@mcp.prompt(task=True)              -> TypeError: FastMCP.prompt() got an unexpected keyword argument 'task'
```

A task-enabled tool must also be `async def` — a synchronous function raises `ValueError` at registration: _"uses a sync function but has task execution enabled. Background tasks require async functions."_

`task=True` is **declared intent, not a running worker**. Server startup validates that a `ServerExtension` with the tasks identifier is registered and refuses to connect otherwise:

```text
RuntimeError: Client failed to connect: Task-enabled tools (slow) require the tasks
extension, but no extension with identifier 'io.modelcontextprotocol/tasks' is
registered. Install it with `pip install 'fastmcp[tasks]'` and register it via
`mcp.add_extension(TasksExtension(...))`.
```

The failure is at connect time, not at decoration time, so a task tool looks fine until the first client attaches. Load the tasks reference before enabling it.

### Sync, Async, Thread Affinity, and Timeouts

- Prefer `async def` for I/O-bound tools.
- Normal synchronous tools run in a thread pool so they do not block the event loop.
- Set `run_in_thread=False` only for libraries bound to the event-loop thread, such as some COM, UI, GPU, or hardware APIs. Inline sync work blocks every other request.
- Inline sync calls have no cancellation checkpoint. The installed release rejects combining `run_in_thread=False` with `timeout`.
- Tool `timeout` applies only to foreground execution, returns MCP error code `-32000` when exceeded, and has no server-wide default.
- A background `task=True` is not governed by the tool timeout; use the tasks extension's timeout dependency and retry contract.

### Arguments and Validation

Use normal Python annotations and defaults. Parameters without defaults are required; defaults make them optional.

Supported Pydantic field shapes include:

- scalars (`int`, `float`, `str`, `bool`) and raw `bytes` strings;
- dates/times, paths, UUIDs, enums, and literals;
- lists, sets, mappings, optionals, and unions;
- Pydantic models and custom Pydantic-supported field types.

Important wire behavior:

- `bytes` input is not automatically base64-decoded; accept a string and decode explicitly when base64 is the contract;
- enum clients send values, while the function receives the Enum member;
- paths and UUIDs arrive as strings and are converted;
- Pydantic models must arrive as JSON objects, not stringified JSON, even under flexible validation;
- strict validation rejects string-to-number/boolean and element coercion; flexible validation accepts compatible values but still rejects invalid ones.

Parameter descriptions may come from Google, NumPy, or Sphinx docstrings, `Annotated[T, "description"]`, or `Annotated[T, Field(...)]`. Explicit `Annotated`/`Field` descriptions win over docstring descriptions. Use `Field` for descriptions, numeric bounds, lengths, patterns, and defaults. Sections such as Returns/Raises/Examples are not included in the tool description.

Use `Depends(...)` or the installed dependency system for user IDs, credentials, connections, and other runtime-only values. Those parameters stay out of the client schema; never accept secrets from the model merely to hide them later.

### Content, Structured Output, and Schemas

FastMCP returns traditional MCP content blocks for compatibility and may add `structured_content` for machine use. That wire field, and `is_error` alongside it, were `structuredContent` and `isError` in v3; both are bridged read-side by the compat shim on `CallToolResult`.

Content conversion rules:

| Python result           | MCP content                         |
| ----------------------- | ----------------------------------- |
| `str`                   | `TextContent`                       |
| `bytes`                 | Base64 blob in an embedded resource |
| FastMCP `Image`         | `ImageContent`                      |
| FastMCP `Audio`         | `AudioContent`                      |
| FastMCP `File`          | Base64 embedded resource            |
| MCP SDK content block   | Passed through                      |
| List of supported items | Item-by-item conversion             |
| `None`                  | Empty response                      |

`Image`, `Audio`, and `File` accept either `path=` or `data=` (not both). Path mode infers MIME type from the extension; data mode needs `format=`. `File` data can also take `name=`, and helpers accept content annotations. Automatic helper conversion applies only to a direct helper or a list; nested helpers must be converted to MCP content explicitly.

Structured-output rules:

- dictionaries, dataclasses, and Pydantic objects produce structured content even without an explicit output schema;
- primitives and collections produce structured content only when a return annotation or explicit schema provides a schema;
- primitive/collection roots are wrapped as `{"result": value}` with FastMCP's wrap marker because MCP output schemas must have an object root;
- traditional content is still emitted for backward compatibility;
- explicit `output_schema` must be an object schema and the structured result must match it;
- inferred schemas support common scalars, collections, unions, TypedDicts, dataclasses, and Pydantic models.

Return `ToolResult(content=None, structured_content=None, meta=None, is_error=False)` for full control. At least content or structured content is required. Structured content must be a dictionary for the MCP wire contract; if it is the only value, FastMCP also renders it as JSON text content. `meta` is runtime execution metadata and is distinct from the decorator's static `meta`. Verify how the target client presents `is_error`.

For YAML, Markdown, or another custom representation, return a `ToolResult` explicitly. This is now the only route: the per-tool `serializer` option and global `tool_serializer` configuration are both gone.

### Errors, Annotations, Notifications, and Versioning

- Raise normal exceptions for internal failures. With `mask_error_details=True`, their details are hidden.
- Raise `ToolError` only for a deliberately client-safe message; its details remain visible even when masking is enabled.
- Tool annotations are advisory, not enforcement. `ToolAnnotations` fields are `title`, `read_only_hint`, `destructive_hint`, `idempotent_hint`, and `open_world_hint` — snake_case here, camelCase in v3, and bridged read-side by the compat shim. Every field defaults to `None` (absent) on the model; the MCP spec's interpretation of an absent hint is respectively unset, `false`, `true`, `false`, and `true`, so a client assumes destructive and open-world behavior unless you say otherwise. Set them explicitly rather than relying on that default.
- Mark genuinely read-only tools accurately; omit/set false for writes, and set destructive true for irreversible changes. Repeated identical calls are idempotent only if they add no further effect.
- Tool add/remove/enable/disable operations send `notifications/tools/list_changed` only when performed inside an active MCP request. Initialization-time assembly does not notify clients.
- Remove a local tool through the installed local provider API only when dynamic catalog mutation is required.
- Versioned tools may share a name; normal resolution exposes the highest compatible version. Load the versioning reference before relying on constraints or migrations.

Use unified `on_duplicate`. The four behaviors are: `warn` (replace with a warning), `error`, `replace` silently, or `ignore` the new registration.

## Resources and Resource Templates

Resources expose passive, addressable data. A resource function is lazy: it runs only after `resources/read` requests its URI. Use a stable domain URI rather than a filesystem accident.

### Resource Registration and Results

`@mcp.resource(uri, ...)` requires a unique URI. FastMCP infers name from the function and description from its docstring unless overridden. For bound methods, use the standalone decorator plus `mcp.add_resource(...)` pattern.

The installed resource controls are `uri` (positional, required), then `name`, `version`, `title`, `description`, `icons`, `mime_type`, `tags`, `annotations`, `meta`, `app`, `auth`, and `security`.

Two differences from the tool decorator matter:

- there is **no `task`** — `@mcp.resource(..., task=True)` raises `TypeError`. Resources are passive reads and have no background-execution path;
- `security` is new and defaults to the `INHERIT_SECURITY` sentinel, deferring to the server's `resource_security` policy. See Resource Path-Safety Screening above.

Verify Apps and component auth separately before enabling them. Decorator `enabled` is not an option.

Prefer these documented result forms:

- `str` for text (`text/plain` by default);
- `bytes` for a base64 MCP blob with an accurate MIME type;
- `ResourceResult` for multiple items, per-item MIME types, and metadata.

Serialize ordinary dict/list payloads deliberately when returning a simple resource, or use `ResourceContent`, whose installed implementation can JSON-serialize objects. Do not rely on ambiguous implicit conversion across versions.

`ResourceResult(contents, meta=None)` accepts one string, one bytes value, or a list of `ResourceContent`. `ResourceContent(content, mime_type=None, meta=None)` defaults to text/plain for strings, application/octet-stream for bytes, and application/json for serialized objects. Result-level and per-item `meta` are runtime response metadata; both are distinct from the resource decorator's static listing `meta`.

For predefined sources, register installed resource classes directly:

- `TextResource` for fixed text;
- `BinaryResource` for fixed bytes;
- `FileResource` for lazy local file reads with encoding/binary handling;
- `HttpResource` for HTTP(S) content when its optional dependency is installed;
- `DirectoryResource` for JSON directory listings with optional recursion;
- `FunctionResource` is the internal wrapper created by the decorator.

Keep reads bounded. Constrain paths to an allowed root, cap sizes, validate encodings/content types, restrict outbound URLs, and never expose secrets by URI guessing.

### Async, Context, Visibility, Annotations, and Notifications

Synchronous resource functions run in a thread pool; prefer async functions for network, database, or filesystem APIs with async support. Inject Context only when the resource needs request-scoped behavior.

Visibility keys use `resource:<uri>` for concrete resources and the installed template key form for templates. Disabled items are absent from lists and cannot be read.

Resource annotations use `Annotations`, not `ToolAnnotations`, and its three fields are `audience`, `priority`, and `last_modified` — all defaulting to `None`. There is no `read_only_hint` or `idempotent_hint` on a resource: passing one is silently dropped rather than rejected, because the model ignores extras. A resource registered with `annotations={"read_only_hint": True}` therefore lists with all three real fields still `None`, and no error anywhere reveals the mistake. Assert the annotations you expect through a client rather than trusting registration to have taken.

Add/enable/disable operations send `notifications/resources/list_changed` only during an active request. Test client refresh behavior when the catalog is mutable.

### Resource Templates

A URI containing placeholders registers a template through the same decorator. Required URI variables map to required function parameters.

FastMCP implements RFC 6570 forms used by the guide:

- `{name}` matches one encoded URI segment before decoding;
- `{path*}` captures multiple path segments, stopping at the next literal or parameter boundary;
- `{?format,limit}` declares optional query parameters.

Template rules:

1. Every required function parameter must appear in the URI path.
2. Every URI path/query variable must exist in the function signature.
3. Query variables must have function defaults.
4. Optional parameters included in `{?...}` are client-configurable; optional parameters omitted from the URI always use their defaults.
5. Templates reject `*args` but allow `**kwargs` because the URI declares the collected names.
6. One function may be registered under multiple templates by applying `mcp.resource(template)(function)` repeatedly.

Type hints coerce query-string values into supported scalar types. Keep nested/complex input out of query strings.

Decoded parameters are untrusted. A one-segment match such as an encoded slash may decode to a value containing `/`. Resolve the final filesystem path and prove it remains under the allowed root; use wildcard parameters only when multi-segment paths are intentional.

### Resource Errors, Duplicates, and Versions

Use `ResourceError` for intentionally client-visible resource failures. Other exceptions follow `mask_error_details`. Avoid revealing absolute paths, credentials, backend queries, or authorization facts.

Unified `on_duplicate` governs concrete resources and templates with the same `warn`, `error`, `replace`, and `ignore` semantics. Versioned resources/templates may share an identity, with normal resolution selecting the highest compatible version.

## Prompts

Prompts generate reusable user/assistant message sequences. They guide a model but do not enforce server policy, authorization, validation, or mutation confirmation.

### Registration, Arguments, and Descriptions

`@mcp.prompt` uses the function name by default. Prompts reject `*args` and `**kwargs`. For bound methods, apply standalone prompt metadata and register the bound method.

The installed prompt controls are `name_or_fn` (positional), `name`, `version`, `title`, `description`, `icons`, `tags`, `meta`, and `auth`. `meta` is static listing metadata. Decorator `enabled` is absent; use server visibility.

Prompts have the narrowest option set of the three decorators. Both `task=` and `app=` raise `TypeError` here — a prompt cannot run in the background and cannot carry MCP Apps configuration.

MCP transports prompt arguments as strings. FastMCP can convert JSON strings into simple typed parameters and augments argument descriptions with the expected JSON schema. Prefer straightforward shapes such as scalar values, `list[int]`, or `dict[str, str]`; avoid deep models or custom classes whose string conversion is fragile. Direct internal rendering may still pass already-typed values.

Required parameters have no default; optional parameters do. Describe arguments through supported Google/NumPy/Sphinx docstrings, `Annotated`, or `Field`. Explicit descriptions win over docstring entries, while the free text before a docstring argument section becomes the prompt description.

### Prompt Results

Return one of:

- `str`, converted to one user message;
- `list[Message | str]`, converted into a conversation, with strings as user messages;
- `PromptResult` for messages plus response description and runtime metadata.

`Message(content, role="user")` accepts user or assistant role. Strings pass through; dicts, lists, and Pydantic models are serialized to JSON text.

`PromptResult(messages, description=None, meta=None)` accepts a single string or message list. Result `meta` applies to that render response and is distinct from decorator `meta`. Keep runtime metadata bounded and non-secret.

Synchronous prompt functions run in a thread pool. Prefer async when prompt construction performs I/O. Context injection is available for request-scoped information, but avoid turning prompt rendering into an implicit side-effect channel.

### Prompt Visibility, Notifications, Duplicates, and Versions

Use `prompt:<name>` keys or tags with server visibility. Add/enable/disable operations send `notifications/prompts/list_changed` only during an active MCP request.

Unified `on_duplicate` controls duplicate prompt names. Versioned prompts may share a name, and normal resolution exposes the highest compatible version.

## Argument Completion

`@mcp.completion` registers one server-wide handler that answers `completion/complete` requests for prompt arguments and resource-template parameters. The handler receives the reference being completed, the argument (name plus the partial value typed so far), and the context of arguments already supplied — so suggestions can narrow on what the caller has already filled in. Return a list of strings, a `Completion` (from `mcp_types`, for pagination hints), or `None` when the reference is not one the handler owns; an unhandled reference yields an empty completion, not an error.

Registering a handler declares the completions capability; a server with none does not advertise it. Completion works identically on the handshake and modern protocol eras. Both `@mcp.completion` and `@mcp.completion()` forms are accepted.

Keep candidates bounded, authoritative, and free of secrets — a completion list is client-visible data derived from server state, so apply the same tenancy and authorization screening as a read.

## Identity and Collision Rules

Component identity is public API:

- tools and prompts are primarily identified by name;
- resources by URI and templates by URI template;
- provider namespaces and transforms can change the final exposed identity;
- preserve stable names, descriptions, schemas, tags, annotations, icons, metadata, and URIs across refactors;
- introduce a deliberate new version or identity for a contract-breaking change.

Prefer assembly-time duplicate errors. Inspect the composed catalog rather than assuming local registration identity is the final client identity.

## Verification

Exercise every component through `fastmcp.Client`, not only by calling its underlying Python function.

```python
from fastmcp import Client

async with Client(mcp) as client:
    tools = await client.list_tools()
    result = await client.call_tool("calculate_total", {"subtotal": 100, "tax_rate": 0.05})
```

Verify the applicable surface:

- server identity, instructions, version, icons, experimental capabilities, transport, pagination, custom routes, and clean lifespan shutdown;
- exposed names/URIs/templates, descriptions, argument and output schemas, tags, annotations, MIME types, icons, static metadata, and version selection;
- flexible and strict validation, required/optional parameters, hidden dependencies, invalid input, timeout/cancellation, and safe error masking;
- content blocks, structured output, explicit schemas, primitive wrapping, `ToolResult`, `ResourceResult`, `PromptResult`, media, binary data, and runtime metadata;
- exact/tag/session visibility, duplicate rejection, dynamic remove/add notifications, and provider/transform composition;
- URI expansion, wildcard boundaries, percent-decoding, query coercion/defaults, path containment, and multiple resource contents;
- resource path-safety screening: an encoded traversal payload rejected as `ResourceSecurityError`, a legitimate dotted value such as `HEAD~3..HEAD` still accepted, and every `exempt_params` entry justified;
- authentication plus application authorization for every protected or mutating component;
- the v4 migration surface: protocol types imported from `mcp_types` (not `mcp.types`, which needs the full SDK package), no camelCase field reads (run the suite with `mcp_camelcase_compat=False` to surface them as `AttributeError`), and no `task=`/`app=` passed to a resource or prompt.

Use the live [server](https://gofastmcp.com/servers/server), [tools](https://gofastmcp.com/servers/tools), [resources](https://gofastmcp.com/servers/resources), [prompts](https://gofastmcp.com/servers/prompts), [pagination](https://gofastmcp.com/servers/pagination), [icons](https://gofastmcp.com/servers/icons), and [Python SDK](https://gofastmcp.com/python-sdk) pages for current design guidance, then resolve every API against the installed release.
