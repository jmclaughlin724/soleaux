# Interactivity and Observability

## Context Contract

Use FastMCP `Context` only when a tool, resource, resource template, prompt, dependency, or middleware path needs request-scoped MCP capabilities. Context is dependency-injected, unique to one request, invalid after that request, and unavailable outside server execution.

Put durable shared dependencies in lifespan state or an owner-managed service. Put cross-request client state in the configured session store. Never cache Context globally, pass it to background work that outlives the request, or treat request/session/client identifiers as authorization.

The live Context guide is design guidance. Inspect the installed `Context` signatures because return shapes evolve. On the pinned release, `Context.read_resource()` returns `ResourceResult` (fields `contents`, `meta`); older examples that index a list are not authoritative.

**Two capabilities on this page are era-conditional or removed.** `ctx.elicit()` raises on the modern protocol era, and `ctx.sample()` / `ctx.sample_step()` are **removed from the server API** on the pinned release. Read [Protocol eras and sessions](protocol-eras-and-sessions.md) for the era model and [Version and source routing](version-and-source-routing.md) for the baseline before relying on either.

## Accessing Context

### Preferred Dependency

Prefer `CurrentContext()` so the parameter is explicitly dependency-injected and excluded from the MCP schema.

```python
from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

mcp = FastMCP("context-demo")

@mcp.tool
async def inspect(uri: str, ctx: Context = CurrentContext()) -> str:
    await ctx.info(f"Reading {uri}")
    result = await ctx.read_resource(uri)
    return str(result.contents[0].content)
```

Context methods are normally async, so request handlers that use them should usually be async.

### Legacy Type-Hint Injection

For compatibility, an argument annotated as `Context`, `Context | None`, or an `Annotated` Context can still be injected by type. The parameter name does not matter. Prefer `CurrentContext()` in new code.

### Deep Helper Access

`fastmcp.server.dependencies.get_context()` retrieves the active Context from a nested helper without threading it through every call. It is server-only and raises `RuntimeError` outside a request. Prefer explicit dependency passing when practical; use ambient access sparingly because it hides the request dependency.

## Context Capability Map

| Need | Installed API shape and usage |
| --- | --- |
| Client logging | `debug`, `info`, `warning`, `error`; messages may include a logger name and bounded structured extras. |
| Progress | `report_progress(progress, total=None, message=None)`; requires client progress support/token. |
| Resource discovery | `list_resources()` returns all visible resources, following pagination internally. |
| Resource read | `read_resource(uri)` returns `ResourceResult`. |
| Prompt discovery | `list_prompts()` returns all visible prompts, following pagination internally. |
| Prompt render | `get_prompt(name, arguments=None)` returns `GetPromptResult`. |
| User input | `elicit(message, response_type, ...)`. **Raises on the modern era and inside a background task.** |
| Model generation | **Removed from the server API.** `ctx.sample` / `ctx.sample_step` no longer exist; call an LLM from server-owned code. Client-side sampling handlers remain for answering legacy servers. |
| Guard-pattern input | `input_responses` carries client answers to a prior `InputRequiredResult.input_requests` (SEP-2322). |
| Session state | `get_state`, `set_state`, `delete_state`. Serializable values may persist across requests for the session. |
| Session visibility | `enable_components`, `disable_components`, `reset_visibility` affect only the current session. |
| Manual notifications | `send_notification(notification)`; usually unnecessary because catalog operations notify automatically. |
| Server instance | `ctx.fastmcp` exposes the underlying server for advanced server-owned operations. |
| Transport | `ctx.transport` is `stdio`, `sse`, `streamable-http`, or `None` outside a server transport. |
| Request identity | `request_id`, `origin_request_id`, optional `client_id`, and `session_id` when an MCP session exists. |
| Execution mode | `is_background_task` and `task_id` distinguish worker execution from a foreground request. |
| Low-level request | `request_context`, which can be `None` before initialization/handshake completes. |

## Client Logging

Context logging sends MCP log notifications to the connected client. It is distinct from process-side Python logging: use `fastmcp.utilities.logging.get_logger()` or the standard `logging` module for files, stderr, collectors, and operator-only diagnostics.

| API | Use |
| --- | --- |
| `ctx.log(message, level=None, logger_name=None, extra=None)` | Send any MCP logging level; omitted `level` means `info`. |
| `ctx.debug(...)` | Detailed diagnostics. |
| `ctx.info(...)` | Normal execution information. |
| `ctx.warning(...)` | A potentially harmful condition that does not prevent completion. |
| `ctx.error(...)` | A failure that may still allow the operation or server to continue. |

`ctx.log()` accepts all MCP levels in severity order: `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, and `emergency`. The convenience methods accept the optional `logger_name` and structured `extra` mapping as well.

```python
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

@mcp.tool
async def analyze(values: list[float], ctx: Context = CurrentContext()) -> float:
    await ctx.info(
        "Starting analysis",
        logger_name="analytics",
        extra={"item_count": len(values)},
    )
    if not values:
        await ctx.warning("No values supplied", logger_name="analytics")
        raise ValueError("values cannot be empty")
    result = sum(values) / len(values)
    await ctx.info("Analysis complete", logger_name="analytics")
    return result
```

### Threshold and Process-Side Copies

`FastMCP(..., client_log_level=...)` sets the default minimum client-visible severity; the `client_log_level` setting defaults to `None`, which applies no server default threshold. A client can replace it for its own session with MCP `logging/setLevel`; the per-session choice is kept in `fastmcp._client_log_levels` and **overrides** the server default for that session only.

Filtering compares MCP levels on an explicit ordinal from `_MCP_LEVEL_SEVERITY`, not on the level string:

| `debug` | `info` | `notice` | `warning` | `error` | `critical` | `alert` | `emergency` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |

**The threshold is applied once, before both the client send and the process-side copy.** `_log_to_server_and_client` returns early when the level is below the minimum, so a suppressed message produces **no local Python log line either** — it is not merely withheld from the client. Verified with `client_log_level="warning"`: `ctx.debug` and `ctx.info` produced no local record at all, while `ctx.warning` and `ctx.error` produced one each. Do not rely on the process-side logger as a complete audit trail of what a component tried to report.

Session overrides are keyed by connection session id. Transports without a session id (stdio, in-memory) share one sentinel gate, which matches their single-connection nature. Test filtering at the boundary values and confirm that one client's override does not affect another _networked_ session.

#### Two Loggers, Two Different Severity Behaviors

Messages that pass the threshold reach **two** Python loggers with **opposite** severity handling. Conflating them is the source of a long-running documentation dispute; both of the following are true.

| Logger | Side | Severity behavior |
| --- | --- | --- |
| `fastmcp.server.context.to_client` | Server, outbound | **Always `DEBUG`**, whatever the MCP level |
| `fastmcp.client.from_server` | Client, inbound | **Full mapping** — `notice`→`INFO`, `alert`/`emergency`→`CRITICAL` |

**Server side.** `_log_to_server_and_client` does call `to_client_logger.log(level=_mcp_level_to_python_level[level], ...)`, so the mapping is genuinely applied at the call. But a clamp installed at import overrides it:

```python
_clamp_logger(logger=to_client_logger, max_level="DEBUG")
```

`_ClampedLogFilter` is a `logging.Filter` that **rewrites `record.levelno` and `record.levelname` in place** and returns `True`, so records are demoted rather than dropped — after the mapped level was passed in. Verified end to end through a live `Client`: emitting all eight MCP levels from a tool yields eight records at a handler on that logger, every one `DEBUG`/`levelno=10`. Reading the call site alone is not sufficient; the filter is attached ~1570 lines earlier in the same module.

**Client side.** `default_log_handler` in `fastmcp/client/logging.py` routes each notification through a `level_map` to `from_server_logger` (name: `fastmcp.client.from_server`), which carries **no** filter. That is where `notice`→`INFO` and `alert`/`emergency`→`CRITICAL` actually take effect. The same eight-level run produced all eight records there — `DEBUG, INFO, INFO, WARNING, ERROR, CRITICAL, CRITICAL, CRITICAL` — exactly as the mapping predicts.

Set that handler to `DEBUG` when checking the mapping yourself. At the default effective level the `debug` record is filtered out before it reaches a handler, which makes the run look like seven mapped levels rather than eight and can be misread as `debug` messages not arriving at all.

Consequences:

- Attach operator alerting to `fastmcp.client.from_server`, which preserves severity — not to `fastmcp.server.context.to_client`, which does not.
- A handler on `fastmcp.server.context.to_client` must be set to `DEBUG` to see anything at all. Recover the real level from the message prefix (`Sending ERROR to client: ...`).
- The clamp is reversible through `fastmcp.utilities.logging._unclamp_logger`, but both that helper and `_clamp_logger` are private. Treat un-clamping as a debugging action, not a supported contract.
- Replacing the client's `log_handler` replaces the mapping too; a custom handler owns its own severity policy.

```python
import logging
from fastmcp.utilities.logging import get_logger

get_logger("fastmcp.server.context.to_client").setLevel(logging.DEBUG)  # demoted copies
get_logger("fastmcp.client.from_server").setLevel(logging.DEBUG)  # severity preserved
```

#### The MCP Logging Capability Is Itself Deprecated

`session.send_log_message` is decorated upstream in the `mcp` SDK with `@deprecated("The logging capability is deprecated as of 2026-07-28 (SEP-2577).", category=MCPDeprecationWarning)`. FastMCP calls it deliberately under a compatibility directive, so **every** `ctx.log()` on a connection that still supports the capability emits an `MCPDeprecationWarning` from `fastmcp/server/context.py`. Expect it in test output; do not silence it globally, because it marks a capability deprecated on the modern era — sampling has already been removed, and elicitation is era-gated. Plan process-side logging and your own telemetry as the durable path for operator diagnostics.

Logging rules:

- Keep messages payload-free by default. Do not log tool arguments, resource contents, elicitation data, sampled conversations, tokens, cookies, credentials, or raw exceptions containing them.
- Put only bounded, serializable correlation fields in `extra`; use `logger_name` for a stable subsystem, not user-controlled cardinality.
- Choose levels consistently and avoid duplicating the same event in middleware, components, and application services.
- Treat client delivery and process-side export as best-effort observability. Neither may change the operation result.
- Test a client that displays logs, a client that changes the minimum level, and a client that ignores them.

## Progress Reporting

Use `await ctx.report_progress(progress, total=None, message=None)` for long-running work. `progress` and `total` are floats on the public API; `message` is an optional short phase description.

| Pattern | Shape | Example |
| --- | --- | --- |
| Percentage | Fixed 0-100 range | `progress=75, total=100` |
| Absolute | Completed units out of a known count | `progress=3, total=10` |
| Indeterminate | Monotonic observed work with no endpoint | `progress=files_found` |

```python
@mcp.tool
async def process_items(items: list[str], ctx: Context) -> list[str]:
    results: list[str] = []
    total = len(items)
    for index, item in enumerate(items):
        await ctx.report_progress(index, total, f"Processing item {index + 1}")
        results.append(await process_one(item))
    await persist(results)
    await ctx.report_progress(total, total, "Complete")
    return results
```

For multi-stage work, allocate stable portions of one scale, such as validation 0-25, export 25-60, transform 60-80, and import 80-100. Do not reset the value at each phase.

Foreground MCP progress requires the original request to carry a `progress_token` in request meta. Without one, `report_progress` falls through to the background-task branch, which is gated on `fastmcp.server.dependencies.is_docket_available()`. **That gate returns `False` here** because this repository declares zero extras and therefore does not install the separate `fastmcp-tasks` distribution; the call performs no notification and does not raise. See [Background tasks](tasks.md) and [Version and source routing](version-and-source-routing.md).

When the tasks extra _is_ installed, the same method updates durable task progress visible through `tasks/get` and task status notifications. Treat foreground notification and durable task status as separate behaviors and test both against an environment that actually installs the worker; this repository cannot exercise the second path.

Progress rules:

- Report monotonic values on one consistent scale and never exceed a declared total.
- Supply a total only when it is meaningful; do not fabricate precision for discovery work.
- Throttle updates by time or useful work units rather than sending one notification per trivial item.
- Check cancellation between meaningful units and before costly commits.
- Send completion only after the result is durable. On failure or cancellation, return the real error/status instead of reporting 100%.
- Test token-present, token-absent, known-total, indeterminate, multi-stage, and cancellation paths with an explicit client progress handler.

## Resource and Prompt Access

Context can discover and invoke the server's currently visible resources and prompts. This is an internal server operation, not a substitute for direct business-layer calls.

- Expect list methods to honor pagination and current visibility.
- Re-apply authorization when a component reads another protected component.
- Treat returned resource content as untrusted input when it originates outside the process.
- Avoid recursive resource/prompt call graphs and bound fan-out.
- Do not depend on the live guide's example container shape; inspect the installed return models and exercise them through `Client`.

## Session State

Serializable session state is isolated by MCP session and persists across requests for that session. Confirm the installed storage expiration behavior and override only through an owned retention policy.

```python
@mcp.tool
async def increment(ctx: Context) -> int:
    value = (await ctx.get_state("counter")) or 0
    await ctx.set_state("counter", value + 1)
    return value + 1
```

Rules:

- **`set_state`, `get_state`, and `delete_state` are async — `await` every call.** `set_state(key, value, *, serializable=True)` and `get_state(key)` are coroutine functions. A missing `await` does **not** raise: the call returns a coroutine object, the write never lands, and `get_state` returns a truthy coroutine rather than the stored value. The only signal is `RuntimeWarning: coroutine 'Context.set_state' was never awaited`, which is easy to miss in a busy test run. Any v3-era example using the bare synchronous form is a silent-failure trap — convert it, and run tests with warnings escalated so the `RuntimeWarning` fails the suite.
- Store JSON-serializable data by default.
- `serializable=False` permits clients/connections and other runtime objects only for the current request; those values do not persist to later requests.
- Do not store raw credentials or use unsafe deserialization.
- Namespace keys when multiple tenants, principals, environments, or feature versions share a store.
- Define retention, encryption, serialization, migration, and deletion behavior.

Keys are automatically prefixed with the session identifier. The default in-memory store is appropriate only for one-process ephemeral operation and tests. Supply a `py-key-value-aio` compatible `AsyncKeyValue` backend, such as an owner-configured Redis, DynamoDB, or MongoDB implementation, when state must survive process replacement or span replicas. `FastMCP(session_state_store=...)` sets it explicitly.

Each `FastMCP` instance owns its store. Mounted parent and child servers do not share persisted state unless both receive the same store. Request-local `serializable=False` state follows the request into mounted children. Initialization middleware state persists only when initialization and later requests resolve to the same session/store; distributed HTTP deployments must preserve the MCP session ID across nodes.

### Session State Does Not Persist on the Modern Era

**Cross-request session state silently resets on every request when the negotiated era is `2026-07-28`.** State keys are session-prefixed through `Context._make_state_key`, and the modern era mints a **new session id per request**, so every read misses.

`ctx.session_id` does _not_ raise there — it returns a fresh UUID each time, which is why nothing errors and nothing warns. Verified by calling one tool three times on one client connection:

| Client mode | Distinct `ctx.session_id` across 3 calls | Read-modify-write counter |
| --- | --- | --- |
| `"auto"` (default) | 3 — new per request | `read=None` every call; never advances past 1 |
| `"legacy"` | 1 — stable | `read=None → 1`, then `read=1 → 2` |

Consequences:

- The `increment` example above returns `1` forever on the default client mode.
- Any confirm-then-apply, cursor, wizard, or accumulator built on session state is broken by default rather than degraded.
- This is a **silent** failure. Assert persistence explicitly in tests — call a state-writing tool twice on one connection and assert the second read sees the first write — rather than assuming a store misconfiguration would surface as an error.

Choose the era deliberately for any workflow that needs cross-request state, or move the state to an application-owned key derived from an identifier you control (an authenticated principal, a workflow id echoed by the client) instead of the MCP session. See [Protocol eras and sessions](protocol-eras-and-sessions.md).

## Session Visibility

`ctx.enable_components(...)`, `ctx.disable_components(...)`, and `ctx.reset_visibility()` alter only the current session.

Selectors are `names`, `keys`, `version`, `tags`, `components`, and `match_all`. Use session visibility for workflows such as activating a namespace after an explicit user choice, not as the sole authorization boundary. Verify that list and direct-access behavior change together and that another session remains unaffected. Selector semantics and ordering live in [Providers and transforms](providers-and-transforms.md).

These three methods are stored **in session state** under the `_visibility_rules` key, so they inherit the persistence limit above exactly: **session visibility is a silent no-op on the modern era.** Verified — the same enable/list sequence that reveals a tool under `mode="legacy"` changes nothing under the default `mode="auto"`. Treat it as unavailable there rather than as a fallback.

## Elicitation

Elicitation pauses a tool, resource, template, or prompt execution while a capable client collects structured user input. Use it for missing parameters, clarification, progressive multi-turn collection, confirmation, or a workflow choice that cannot be known when the initial request is sent.

### Era Gate — Read This First

`ctx.elicit()` depends on a server-initiated request over a client back-channel. The `2026-07-28` protocol era removed that back-channel (SEP-2577), so on that era `ctx.elicit()` **raises before touching the wire**:

```text
ToolError: elicitation via server-initiated requests is unavailable on 2026-07-28 connections.
```

`Client(mode=...)` defaults to `"auto"`, which against a FastMCP server negotiates exactly that era. **Elicitation therefore fails by default.** Verified against an in-memory server:

| Client mode | Negotiated era | `ctx.elicit()` |
| --- | --- | --- |
| `"auto"` (default) | `2026-07-28` | raises `ToolError` |
| `"legacy"` | `2025-11-25` | works; accept/decline/cancel all reachable |

The check is `Context._is_modern_protocol()`, which reads the negotiated `protocol_version` from the active request context and returns `False` when no request context exists. FastMCP raises deliberately rather than letting the SDK surface an opaque "Method not found". See [Protocol eras and sessions](protocol-eras-and-sessions.md) for how a deployment chooses its era.

A second, independent gate applies inside a background task. `ctx.is_background_task` is checked **before** the era gate, and imperative elicitation there raises:

```text
ToolError: Imperative ctx.elicit() is not supported inside a background task. Gather
input with the guard pattern instead: return an InputRequiredResult from the tool (with
input_requests), and read ctx.input_responses / ctx.request_state when the task re-runs
after the client answers.
```

That is the migration path for both gates: the SEP-2322 guard pattern. A guard tool returns `mcp_types.InputRequiredResult` (fields `input_requests`, `request_state`, `result_type`, `meta`) to _ask_, and on a later round reads `ctx.input_responses` — a mapping keyed by the `input_requests` the tool minted, whose values are `ElicitResult`, `CreateMessageResult`, or `ListRootsResult`. `ctx.input_responses` is `None` on the initial round.

The `Context.is_background_task` docstring still shows `await ctx.elicit(...)` inside a `@server.tool(task=True)` body and claims it "works transparently in both foreground and background task modes." **That docstring is stale and contradicted by the code directly beneath it.** Trust the raise.

### Result Handling on the Handshake Era

Call `await ctx.elicit(message, response_type, *, response_title=None, response_description=None)`. The result has one of three actions:

| Action | Typed result | Required handling |
| --- | --- | --- |
| `accept` | `AcceptedElicitation(data=...)` | Validate and use `data`. |
| `decline` | `DeclinedElicitation()` | Stop or take an explicitly safe alternative without treating it as consent. |
| `cancel` | `CancelledElicitation()` | Abort the containing workflow and preserve cancellation semantics. |

Branch on `result.action` or pattern-match the three installed result classes from `fastmcp.server.elicitation`. If the client does not implement elicitation, the call raises; provide a deterministic non-interactive input path or a clear capability error when the workflow must also work in such clients.

```python
from typing import Literal
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)

@mcp.tool
async def choose_priority(ctx: Context) -> str:
    result = await ctx.elicit(
        "Choose the priority for this task",
        response_type=Literal["low", "medium", "high"],
        response_title="Priority",
        response_description="Controls queue ordering, not authorization.",
    )
    match result:
        case AcceptedElicitation(data=priority):
            return priority
        case DeclinedElicitation():
            return "No priority selected"
        case CancelledElicitation():
            raise RuntimeError("Task creation cancelled")
```

Multiple calls may collect a complex decision progressively. Check every result before asking the next question, carry only accepted values forward, and re-read mutable state before applying the final operation.

### Response Types and Schemas

MCP elicitation uses an object schema with shallow primitive fields. The installed `parse_elicit_response_type` accepts these `response_type` shapes:

| Shape | Returned `AcceptedElicitation.data` | Guidance |
| --- | --- | --- |
| `str`, `int`, `float`, `bool` | The scalar | FastMCP wraps it as an object with one `value` field and unwraps the response. |
| `Literal[...]` or an `Enum` | The selected literal or Enum member | Emits `value` with an `enum` constraint. Use for typed single-select choices. |
| `list[str]` value such as `["low", "high"]` | One selected string | Convenient untitled single-select shorthand; same `enum` shape. |
| `dict[str, {"title": str}]` | One selected key | Titled single-select; emits the SEP-1330 `oneOf`/`const`/`title` shape. |
| Nested list value such as `[["bug", "feature"]]` | A list of selected strings | Untitled multi-select shorthand; emits an `array` with `enum` items. |
| `list[Enum]` | A list of Enum members | Typed multi-select. |
| One-item list containing a titled-option dict | A list of selected keys | Titled multi-select; emits the SEP-1330 `anyOf` item shape. |
| Dataclass, `TypedDict`, or Pydantic model | A validated instance/value | Use for multiple shallow fields. Put UI metadata on each field. |
| `None` | Empty value | Deprecated; emits `{"type": "object", "properties": {}}` and returns `{}` on accept, not `None`. Prefer an explicit `bool` or `Literal` confirmation. |

The wire schema must remain a flat object whose properties are supported primitives (`string`, `number`/`integer`, `boolean`) or compatible enum/const choices. Nullable primitive fields and supported multi-select arrays are allowed by the installed generator; nested objects and arrays of objects are not. Keep complex workflows multi-turn instead of forcing a deep form.

`response_title` and `response_description` replace the generated `value` field's default label/description for scalar, `Literal`, Enum, and dict/list shorthand forms. Passing them with a structured model, dataclass, or `None` raises `TypeError` with an explicit message naming the supported forms; use Pydantic `Field(title=..., description=...)` on structured fields instead.

Pydantic `Field(default=...)` supplies client-visible defaults for supported strings, integers, numbers, booleans, and enums. Defaulted fields become optional. Treat a displayed default as proposed input, not proof the user reviewed it.

### Elicitation Safety and Verification

1. Determine the negotiated era first. On the modern era, elicitation is unavailable — design the guard pattern instead of a fallback that silently degrades.
2. Confirm the client path implements elicitation or configure a fallback interaction outside the tool.
3. Ask for the narrowest response schema, explain why the value is needed, and never request secrets that should come from authentication or server configuration.
4. Handle accept, decline, cancel, invalid data, transport loss, timeout, unsupported-client, modern-era, and background-task paths explicitly.
5. Revalidate authorization, idempotency keys, resource versions, prices, and other stale state after the response.
6. Apply the operation only from validated accepted data. Elicitation does not replace authentication, authorization, server-side validation, or a durable plan/confirm/apply contract.

## Sampling — Removed from the Server API

`ctx.sample()`, `ctx.sample_step()`, and `ctx.list_roots()` are **removed** on the pinned release: `Context` has no such attributes, and `FastMCP(sampling_handler=..., sampling_handler_behavior=...)` no longer exists. Server-initiated push requests have no transport on the sessionless `2026-07-28` era (SEP-2577), and a method that only works against old clients is a trap, so the removal is total rather than era-gated.

There is **no in-framework replacement**, by design. The migration is architectural:

- **Generation:** call an LLM directly from server-owned application code with your own provider client, credentials, timeout, retry, and budget policy, under the active tool span (see Telemetry below). A model call is ordinary application code; it was never protocol surface.
- **Roots:** take paths as tool arguments, or ask through the guard pattern — an `InputRequiredResult.input_requests` map can still carry a `ListRootsRequest` (see [Protocol eras and sessions](protocol-eras-and-sessions.md)).

The **client** side is retained: `Client(sampling_handler=..., roots=...)` and the provider handlers at `fastmcp.client.sampling.handlers.{openai,anthropic,google_genai}` still answer a legacy server's requests, and `ProxyClient`'s relay handlers stay for the same interop reason. Those handlers are extra-gated and **not importable here** (this repository declares zero extras); verify the extra, credentials, privacy boundary, and billing owner before enabling one.

Treat sampled or elicited output as untrusted until validated against the business contract, and never treat a model tool choice as user consent.

## Change Notifications

FastMCP sends tool/resource/prompt list-change notifications automatically when catalog mutations occur within an active request. Initialization-time assembly does not notify clients.

Use `ctx.send_notification(notification)` only for an owned advanced case that the automatic catalog path cannot express; it takes an `mcp_types.ServerNotification`. Validate notification type, client support, refresh behavior, ordering, and duplicate delivery. Do not emit notifications to compensate for an inconsistent catalog.

## Server, Transport, and Request Metadata

Use `ctx.fastmcp` only for server-owned advanced operations; application services should not depend on the whole server object.

`ctx.transport` is fixed for the server lifetime. Transport-specific behavior should be rare and tested across every supported transport. Do not weaken output, auth, or timeout policy merely because the request uses stdio.

Request properties:

- `request_id` is per operation, and `origin_request_id` correlates a derived operation back to the originating request;
- `client_id` is optional client-provided initialization identity;
- `session_id` identifies the MCP session and raises before a session exists;
- `task_id` and `is_background_task` identify worker execution;
- `request_context` exposes low-level MCP SDK state and may be `None` during pre-handshake middleware/initialization;
- client request `meta`, when present, is available through request-context attribute access and is client-defined.

Treat all IDs and metadata as correlation or untrusted hints, never authenticated identity. Validate known fields and ignore unexpected fields. For HTTP headers/request data before MCP session establishment, use the installed HTTP dependency helpers rather than assuming `request_context` exists.

## Telemetry

FastMCP ships native OpenTelemetry instrumentation for server operations, client operations, mounted-server delegation, and proxy-provider chains.

### Dependency and Runtime Toggle

The instrumentation depends on **`opentelemetry-api` only**. Span creation is a no-op unless an OpenTelemetry **SDK and exporter** are separately installed and configured, so the application owns collection, sampling, processing, and export. Verified in this environment: `opentelemetry-api` is installed, `import opentelemetry.sdk` raises `ModuleNotFoundError`. **Nothing is exported from this repository as installed, and enabling instrumentation cannot by itself create egress.**

`settings.enable_telemetry` defaults to `True`. It is a genuine runtime toggle read on every `get_tracer()` call:

```python
import fastmcp
fastmcp.settings.enable_telemetry = False   # or FASTMCP_ENABLE_TELEMETRY=false
```

When disabled, `get_tracer()` returns `_DisabledTracer`, a `NoOpTracer` subclass whose `start_as_current_span` yields `INVALID_SPAN` **without attaching it to the OTel context**. That distinction matters: the stock `NoOpTracer` attaches a `NonRecordingSpan`, which would hide an enclosing application span from `trace.get_current_span()` inside a handler. Disabling FastMCP telemetry therefore leaves a surrounding ASGI/HTTP trace intact. Verified: `True` → `ProxyTracer`, `False` → `_DisabledTracer`, and the toggle is reversible in-process.

### Provider Registration Is Lazy — No Import Ordering Requirement

An earlier revision of this file instructed readers to call `trace.set_tracer_provider(provider)` **before importing FastMCP**. **That requirement does not exist.** `fastmcp.telemetry.get_tracer()` calls `opentelemetry.trace.get_tracer(...)` at call time and returns a `ProxyTracer`, whose `_tracer` property resolves the global `_TRACER_PROVIDER` lazily on first span creation and caches it thereafter.

Verified by importing `FastMCP` first and observing `get_tracer()` return `ProxyTracer` — the API's own deferred-resolution type. Register the provider wherever your application owns startup; a provider registered after FastMCP is imported is picked up normally. The only real constraint is the OTel API's own: register the provider before the first span is _created_, because `ProxyTracer` caches the resolved tracer.

### Enable and Configure Export

For auto-instrumentation, install `opentelemetry-distro` and `opentelemetry-exporter-otlp`, run `opentelemetry-bootstrap -a install`, then launch through `opentelemetry-instrument`. Set `--service_name` / `OTEL_SERVICE_NAME` and `--exporter_otlp_endpoint` / `OTEL_EXPORTER_OTLP_ENDPOINT` explicitly.

For programmatic control, create a `TracerProvider`, add a `BatchSpanProcessor` with the chosen exporter, and call `trace.set_tracer_provider(provider)` during owned startup. The owner chooses sampler, resource/service attributes, batching, exporter credentials/TLS, and shutdown flushing.

Use `ConsoleSpanExporter` or `otel-desktop-viewer` for quick local work, Jaeger for a fuller local UI, and OTLP for shared backends such as Tempo, Datadog, New Relic, Logfire, or another compatible collector. Tune SDK sampling when traces are too noisy; do not remove framework spans selectively and break end-to-end context.

### Module Surfaces

`fastmcp.telemetry` is the core module. **It is not `fastmcp.utilities.telemetry`, which does not exist.** Its eight public names:

| Name | Kind | Purpose |
| --- | --- | --- |
| `INSTRUMENTATION_NAME` | `str` = `"fastmcp"` | Instrumentation scope name passed to `get_tracer`. Filter framework spans by scope with this. |
| `TRACE_PARENT_KEY` | `str` = `"traceparent"` | Meta key carrying W3C trace parent across the MCP wire. |
| `TRACE_STATE_KEY` | `str` = `"tracestate"` | Meta key carrying W3C trace state. |
| `get_tracer(version=None)` | `-> Tracer` | The only tracer entry point. Honors `enable_telemetry`; resolves the provider lazily. |
| `inject_trace_context(meta=None)` | `-> dict \| None` | Merge current trace context into an MCP request `meta` dict. Returns `meta` unchanged when there is no context to inject. |
| `extract_trace_context(meta)` | `-> Context` | Build a parent context from request `meta`. **Returns the current context unchanged when already inside a valid trace**, so HTTP-level propagation is never overridden by meta. |
| `record_span_error(span, exception)` | `-> None` | Record the exception and set `StatusCode.ERROR`. |
| `restore_dropped_attributes(span, attrs)` | `-> None` | Re-set creation-time attributes that a non-forwarding custom `Sampler` discarded. |

`restore_dropped_attributes` exists because `Tracer.start_span` builds the span from `SamplingResult.attributes`, not the `attributes=` kwarg — a custom sampler whose `SamplingResult.attributes` defaults to `None` silently drops everything FastMCP passed. The restore is deliberately all-or-nothing: it fires only when the span has **no** attributes _and_ `dropped_attributes == 0`. A sampler that supplied any attributes of its own — forwarding, redacting, or substituting — is left untouched, as is an SDK attribute-limit eviction. **If you write a custom sampler, forward or deliberately replace attributes; do not return `None`.**

`fastmcp.server.telemetry` carries the server seam:

| Name | Purpose |
| --- | --- |
| `SEAM_SPAN_MARKER` | Attribute `fastmcp.span.seam` marking the per-request SERVER span. |
| `seam_span(method, server_name)` | Opens the per-request SERVER span at the middleware seam. |
| `server_span(name, method, server_name, component_type, component_key, ...)` | Enriches the seam span, or opens a SERVER span when no seam is active. |
| `delegate_span(name, provider_type, component_key, method=None)` | INTERNAL span named `delegate {name}` for provider delegation. |
| `get_auth_span_attributes()` | `enduser.id` and `enduser.scope` from the access token, empty when unauthenticated. |
| `get_session_span_attributes()` | `mcp.session.id` when a session exists. |
| `get_protocol_span_attributes()` | `mcp.protocol.version` for the negotiated era. |
| `record_span_exception(span, e)` | Sets `error.type`, records the exception, sets error status. Guarded on `span.is_recording()`. |

`fastmcp.client.telemetry` exports exactly one name, `client_span(name, method, component_key, session_id=None, resource_uri=None, tool_name=None, prompt_name=None)`, which opens a CLIENT-kind span.

### Span Model — One Enriched SERVER Span Per Request

The span shape changed. FastMCP drops the MCP SDK's `OpenTelemetryMiddleware` to avoid emitting a duplicate SERVER span, and instead opens **one** SERVER span per inbound request at its own middleware seam, then enriches it in place.

1. `FastMCPServerMiddleware.__call__` enters `_seam_span(...)` for every request. `seam_span()` opens a `SpanKind.SERVER` span named after the method, parented from `extract_trace_context(request meta)`, carrying `SEAM_SPAN_MARKER`, `mcp.method.name`, `fastmcp.server.name`, and the protocol/auth/session attributes. It stores itself in an `_active_seam_span` ContextVar.
2. Requests that never reach the high-level path — `initialize`, `ping`, `logging/setLevel`, auth rejections, not-found mapping, middleware vetoes, params failures — are still fully traced here, with exceptions recorded on that span. **This is the main practical gain: pre-dispatch rejections previously produced no SERVER span at all.**
3. When the high-level path reaches `server_span(...)`, it checks whether the seam span is still the active, recording, valid span. If so it calls `update_name(name)` and `set_attributes(component attrs)` on **that same span** and yields it — no second span is opened.
4. Only outside a seam context — for example an in-process `mcp.call_tool()` that bypasses the dispatcher — does `server_span` start its own SERVER span.

Notifications (`request_id is None`) are skipped deliberately: a SERVER span models a request/response, not fire-and-forget.

| Operation | Span behavior |
| --- | --- |
| Any request method | One SERVER span opened at the seam, named after the method |
| Tool call | Same span renamed `tools/call {name}`, enriched with `gen_ai.tool.name` |
| Resource read | Same span renamed `resources/read`; URI goes in `mcp.resource.uri`, not the name |
| Prompt render | Same span renamed `prompts/get {name}`, enriched with `gen_ai.prompt.name` |
| Mounted server | Child INTERNAL `delegate {name}` span from `FastMCPProvider` |
| FastMCP client | CLIENT-kind span from `client_span` |
| Proxy provider | Local provider span plus propagated context when the transport/backend preserves it |

**Do not write assertions that expect a separate seam span and component span for one tool call** — that shape does not occur on the pinned release. Expect a client span to parent a server span; mounted providers add an internal delegate span before the child server operation. Verify actual propagation across every proxy, reverse proxy, and remote transport rather than inferring it from one-process traces.

Errors mark the span `ERROR`, set `error.type` to `tool_error` for `ToolError` or the exception's `__qualname__` otherwise, and record an exception event with stack trace. Treat exported exception messages and stack traces as sensitive production data and sanitize/mask errors at the application boundary.

### Custom Spans

Use `from fastmcp.telemetry import get_tracer` and create child spans inside the active component operation only around expensive or hard-to-debug work:

- database, vector-store, HTTP, queue, or other external calls;
- multi-stage tool logic where parse/fetch/rank/write latency needs separation;
- prompt/resource generation that fans out;
- direct model/provider calls and their surrounding application stages.

Name children `{component_name}.{operation}`, such as `search.fetch`, `search.rank`, or `docs.render`. Add bounded workload-shape attributes such as counts, byte sizes, cache hits, or non-sensitive opaque IDs. Do not span every small helper or in-memory transformation.

Keep model calls under the active tool span — this matters more now that sampling is deprecated and provider calls move into server-owned code. When the provider has an owned OpenTelemetry integration, enable it so its request/token spans nest naturally instead of wrapping every call manually. Let exceptions propagate to the FastMCP operation span unless application recovery is intentional.

### Attribute Contract

MCP semantic conventions:

| Attribute | Meaning |
| --- | --- |
| `mcp.method.name` | `tools/call`, `resources/read`, `prompts/get`, or any seam-only method |
| `mcp.protocol.version` | Negotiated protocol era; restored by FastMCP after dropping the SDK middleware |
| `mcp.session.id` | MCP session correlation identifier, when a session exists |
| `mcp.resource.uri` | Resource URI for read operations |
| `gen_ai.tool.name` | Tool name |
| `gen_ai.prompt.name` | Prompt name |
| `error.type` | `tool_error` for `ToolError`, otherwise the exception `__qualname__` |
| `enduser.id` | Authenticated client ID from the access token |
| `enduser.scope` | Space-separated OAuth scopes |

FastMCP-owned attributes use the `fastmcp.` prefix:

| Attribute | Meaning |
| --- | --- |
| `fastmcp.span.seam` | Marks the SERVER span opened at the middleware seam |
| `fastmcp.server.name` | Server name |
| `fastmcp.component.type` | `tool`, `resource`, `resource_template`, or `prompt` |
| `fastmcp.component.key` | Canonical component identity such as `tool:greet@` |
| `fastmcp.provider.type` | Provider class, such as `LocalProvider`, `FastMCPProvider`, or `ProxyProvider` |
| `fastmcp.delegate.original_name` | Original delegated tool/prompt name before namespace changes |
| `fastmcp.delegate.original_uri` | Original delegated resource URI |
| `fastmcp.proxy.backend_name` | Remote backend tool/prompt name |
| `fastmcp.proxy.backend_uri` | Remote backend resource URI |

Dashboards written against older FastMCP releases may still query removed `rpc.system`, `rpc.service`, or `rpc.method`. Migrate them to `mcp.method.name` plus the appropriate `fastmcp.*` identity attributes.

### Privacy, Reliability, and Tests

- Propagate trace/correlation context without treating it as authenticated identity.
- Record component identity, latency, status, cancellation, and bounded workload metrics.
- Exclude arguments, resource contents, raw query text, prompts, sampled conversations, tokens, cookies, request metadata, elicited data, and secrets by default.
- Make export batched/non-blocking and failure-tolerant; an exporter outage must not fail MCP work.
- Flush providers during owned shutdown and define sampling/retention/access controls.
- Redirect telemetry to an in-memory exporter in deterministic tests rather than relying on an external collector.

For focused tests, install a `TracerProvider` with `SimpleSpanProcessor(InMemorySpanExporter())`, run a Client operation, call `get_finished_spans()`, and assert the expected span name, parentage, kind, safe attributes, and error status. Clear the exporter between tests and restore/isolate the provider so global OpenTelemetry state does not leak across the suite. **These tests require the OpenTelemetry SDK, which this repository does not install** — add it to the owning manifest before writing them, and assert against the single-enriched-span model above rather than a nested seam/component pair.

## Verification

Test through `fastmcp.Client` with explicit handlers for elicitation, sampling, logs, progress, and notifications. Pass `mode=` explicitly so the negotiated era is a decision rather than a default.

Cover:

- preferred, legacy, and nested Context access plus failure outside a request;
- installed return shapes for resource/prompt access and paginated list behavior;
- elicitation on both eras: the modern-era `ToolError`, the background-task `ToolError`, the guard-pattern round trip, and accept/decline/cancel on the handshake era;
- sampling on both eras with and without a server handler, plus the deprecation warning, callback failure, invalid typed output, time/token limits, and recursion bounds;
- client log threshold filtering, per-session `logging/setLevel` override, and the `DEBUG`-clamped process-side copies;
- serializable session persistence **asserted across two requests on one connection** for each era you support, plus request-local non-serializable values, expiry, store failure, session isolation, and mounted-server behavior;
- session visibility changes and isolation from other clients, asserting the modern-era no-op explicitly so it cannot be mistaken for a passing test;
- request-context absence before initialization, untrusted metadata, and every supported transport;
- monotonic progress with and without a progress token, log redaction, catalog notifications, and transport cancellation;
- telemetry only after adding an OpenTelemetry SDK: one enriched SERVER span per request, a traced pre-dispatch rejection, and the `enable_telemetry=False` pass-through.

Use the live [Context](https://gofastmcp.com/servers/context), [elicitation](https://gofastmcp.com/servers/elicitation), [sampling](https://gofastmcp.com/servers/sampling), [progress](https://gofastmcp.com/servers/progress), [client logging](https://gofastmcp.com/servers/logging), [OpenTelemetry](https://gofastmcp.com/servers/telemetry), and [documentation index](https://gofastmcp.com/llms.txt) guides for current design guidance, then confirm every API in installed source. Route cross-cutting execution, storage, tasks, protocol eras, and server lifecycle through [Middleware](middleware.md), [Storage backends](storage-backends.md), [Background tasks](tasks.md), [Protocol eras and sessions](protocol-eras-and-sessions.md), and [Lifespan](lifespan.md).
