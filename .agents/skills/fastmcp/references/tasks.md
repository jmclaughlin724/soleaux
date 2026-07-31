# Background Tasks

## Nothing On This Page Runs Here

> **This repository declares the `fastmcp` dependency with no extras, so the `fastmcp-tasks` distribution is not installed and no task on this page is executable in this environment.**

```bash
python -c "import fastmcp_tasks"
# ModuleNotFoundError: No module named 'fastmcp_tasks'
```

Core still accepts the declaration — `@mcp.tool(task=True)` registers fine — but the server refuses to serve it. See [The Extension Model](#the-extension-model).

Enabling tasks is a **manifest change**, not a code change: add the `tasks` extra to the `fastmcp` pin in `tools/soleaux/pyproject.toml`, relock, and resync. Do not attempt to work around the missing extra in source.

For the pinned release and authority order, read [Version and source routing](version-and-source-routing.md). The governing proposal is **SEP-2663**, cited in installed source at `fastmcp/utilities/tasks.py` and `fastmcp/utilities/components.py`.

## What Changed From v3

v3 shipped a Docket-backed task engine inside core. v4 removed the engine, kept the declaration, and moved execution behind a server extension. Every row below was probed against installed source.

| v3 surface | Probe | Result |
| --- | --- | --- |
| `fastmcp.server.tasks` | `python -c "import fastmcp.server.tasks"` | `ModuleNotFoundError: No module named 'fastmcp.server.tasks'` |
| `Client.get_task_status` / `get_task_result` / `cancel_task` / `list_tasks` | `hasattr(Client, ...)` for each | all `False` |
| `call_tool(task=…, task_id=…, ttl=…)` | `inspect.signature(Client.call_tool)` | keyword-only params are `version`, `timeout`, `progress_handler`, `raise_on_error`, `meta` — no task keywords |
| `ToolTask` / `ResourceTask` / `PromptTask` | membership in `dir()` of `fastmcp.tools.tool`, `fastmcp.resources.resource`, `fastmcp.prompts.prompt` | all `False` |
| `check_background_task` | `"check_background_task" in dir(fastmcp.utilities.tasks)` | `False` |
| Nested `docket` settings and `client_task_poll_interval` | `hasattr(Settings(), "docket")`, `hasattr(Settings(), "client_task_poll_interval")` | both `False`; `Settings` exposes 33 fields, none matching `task` or `docket` |
| `FASTMCP_DOCKET__*` environment variables | `FASTMCP_DOCKET__URL=… python -c "…"` | inert — no field consumes it |
| `fastmcp tasks worker` | `fastmcp tasks worker` | `Unknown command "tasks".` The CLI has eleven top-level commands: `auth`, `call`, `dev`, `discover`, `generate-cli`, `inspect`, `install`, `list`, `project`, `run`, `version` |

`pydocket`, `Docket`, and the worker CLI are gone from core entirely. Any migration guide, snippet, or agent memory that names them describes an era this release does not implement.

## `TaskConfig` Relocated, Not Removed

`TaskConfig` survived the engine removal. It moved to `fastmcp.utilities.tasks` and remains the declaration type on the tool decorator.

**A migration that deletes `TaskConfig` usage on the assumption it is gone is wrong.** Rewrite the import; keep the declaration.

`fastmcp/utilities/tasks.py` owns the whole declaration vocabulary:

| Name | Kind | Value / shape |
| --- | --- | --- |
| `TASKS_EXTENSION_ID` | `str` | `"io.modelcontextprotocol/tasks"` |
| `TaskMode` | `Literal` | `"forbidden"` \| `"optional"` \| `"required"` |
| `TaskConfig` | dataclass | `mode: TaskMode = "optional"`, `poll_interval: timedelta` |
| `TaskMeta` | dataclass | `ttl: int \| None`, `fn_key: str \| None` |
| `DEFAULT_POLL_INTERVAL` | constant | `timedelta(seconds=5)` |
| `DEFAULT_POLL_INTERVAL_MS` | constant | `5000` |
| `DEFAULT_TTL_MS` | constant | `60000` |

Mode semantics:

| Mode        | Meaning                                         |
| ----------- | ----------------------------------------------- |
| `forbidden` | Foreground execution only                       |
| `optional`  | Client may request foreground or task execution |
| `required`  | Client must use task execution                  |

`TaskConfig.from_bool(True)` yields `mode="optional"`; `from_bool(False)` yields `mode="forbidden"`. `supports_tasks()` is true for `optional` and `required`. The bare `TaskConfig()` default is `optional`, but every non-tool component carries an explicit `forbidden` config on `FastMCPBaseModel.task_config`.

## The Extension Model

`task=True` is a **declaration of intent only**. Core ships no executor. Installed source states this directly at `fastmcp/utilities/tasks.py`:

> Reverse-DNS identifier of the SEP-2663 tasks extension. A tool declared with `task=True` requires an extension with this identifier to be registered on the server (`mcp.add_extension(...)`); the `fastmcp-tasks` package provides it. Kept here as pure declaration so core can check for the extension without importing the tasks package.

The engine arrives as a `ServerExtension` registered through `FastMCP.add_extension` (SEP-2133). Extensions contribute a negotiated capability, additive request methods, a `tools/call` interceptor, and an optional lifespan. A mounted child's extensions do not propagate to the root; the root serves the wire, so register there.

### The startup guard

`fastmcp/server/mixins/lifespan.py` defines `_validate_task_extension_registered`, called from the server lifespan **after** the user lifespan, shared-context lifespan, extension lifespans, and all provider lifespans have entered, and **before** `self._started.set()`.

What it actually does:

1. Returns immediately if the mounted-child root-deferral flag is active — the root owns the extension and its aggregated `get_tasks()` already covers the child.
2. Returns if `TASKS_EXTENSION_ID` is already among the registered extensions.
3. Otherwise awaits `self.get_tasks()` and re-filters by `task_config.supports_tasks()`, because `get_tasks()` applies server-level transforms that can inject non-task tools into the result.
4. Returns if nothing survives the filter.
5. Otherwise raises `RuntimeError`.

The verbatim message, reproduced by connecting a `Client` to a server with one task tool and no extension:

> Task-enabled tools (export) require the tasks extension, but no extension with identifier 'io.modelcontextprotocol/tasks' is registered. Install it with `pip install 'fastmcp[tasks]'` and register it via `mcp.add_extension(TasksExtension(...))`.

**No worker is auto-started.** The guard exists precisely because a task-configured tool serving without the extension would silently never run as a task — a correctness bug that the release converts into a loud startup failure.

The failure surfaces at connect time, not at import or registration:

```
RuntimeError: Client failed to connect: Task-enabled tools (export) require the tasks extension, …
```

Registration itself succeeds, and `await mcp.get_tasks()` returns the declared tool with its config intact. Declaration and execution are fully decoupled.

## Declaration Rules

`task=` is **tool-only**, typed `bool | TaskConfig | None` with default `None`.

```python
from datetime import timedelta

from fastmcp import FastMCP
from fastmcp.utilities.tasks import TaskConfig

mcp = FastMCP("service")

@mcp.tool(task=TaskConfig(mode="required", poll_interval=timedelta(seconds=2)))
async def export(report_id: str) -> str:
    return await build_report(report_id)
```

`FastMCP.resource` and `FastMCP.prompt` do not accept the keyword. Passing it raises `TypeError`:

> FastMCP.resource() got an unexpected keyword argument 'task'

> FastMCP.prompt() got an unexpected keyword argument 'task'

A synchronous function with tasks enabled raises `ValueError` at registration, from `TaskConfig.validate_function`:

> 'sync_tool' uses a sync function but has task execution enabled. Background tasks require async functions.

The check unwraps callable instances, `functools.partial`, and `staticmethod` before testing for a coroutine function, so wrapping a sync callable does not evade it.

`Context` retains the task-side accessors regardless of whether the extension is installed: `ctx.is_background_task`, `ctx.task_id`, and `ctx.origin_request_id`.

## Resolution Helper

`fastmcp.decorators.resolve_task_config` normalizes the decorator argument before it reaches component construction:

```python
from fastmcp.decorators import resolve_task_config

resolve_task_config(task: bool | TaskConfig | None) -> bool | TaskConfig
```

It maps `None` to `False` and passes everything else through unchanged. Use it when building a decorator layer or component factory that forwards `task=` rather than reimplementing the default.

## Packaging Split

`tasks` is the one extra that does not resolve to the slim distribution. Reading the real metadata:

```bash
python -c "from importlib.metadata import distribution; print(*[r for r in distribution('fastmcp').requires if 'extra ==' in r], sep='\n')"
```

Every other extra maps to `fastmcp-slim[<extra>]==<version>` — `anthropic`, `apps`, `azure`, `code-mode`, `gemini`, `openai`. The `tasks` extra maps to `fastmcp-tasks==<version>`, a **separate distribution**.

`fastmcp-slim` declares no `tasks` extra at all:

```bash
python -c "from importlib.metadata import metadata; print(metadata('fastmcp-slim').get_all('Provides-Extra'))"
# ['anthropic', 'apps', 'azure', 'client', 'code-mode', 'gemini', 'mcp', 'openai', 'server']
```

`fastmcp[tasks]` is therefore the only correct spelling. `fastmcp-slim[tasks]` does not resolve.

## Moved Dependencies

`CurrentDocket` and `CurrentWorker` are no longer part of `fastmcp.dependencies`, whose `__all__` now holds eleven names: `CurrentAccessToken`, `CurrentContext`, `CurrentFastMCP`, `CurrentHeaders`, `CurrentRequest`, `Dependency`, `Depends`, `Progress`, `ProgressLike`, `Shared`, `TokenClaim`.

Importing either raises `ImportError` with a redirect:

> 'CurrentDocket' moved to the fastmcp-tasks package. Install it with `pip install 'fastmcp[tasks]'` and import from `fastmcp_tasks.dependencies`.

> 'CurrentWorker' moved to the fastmcp-tasks package. Install it with `pip install 'fastmcp[tasks]'` and import from `fastmcp_tasks.dependencies`.

`Progress()` remains in core and serves ordinary request progress. Keep progress monotonic, bounded, throttled, and free of secrets.

## Inside `fastmcp-tasks` — Upstream-Documented, Not Verified Here

The extra is not installed in this repository, so nothing below was executed. Treat it as upstream documentation to confirm against installed source **after** the manifest change, not as established fact.

- The `TasksExtension` constructor and its storage/backend arguments.
- Worker lifecycle, concurrency, redelivery, and shutdown.
- Task store schema, TTL enforcement, and result retention.
- `fastmcp_tasks.dependencies` and whatever it exports.
- Client-side task methods, if any are reintroduced there.

What upstream's own package documentation adds (still unverified here): `fastmcp-tasks` publishes to PyPI at versions matching the FastMCP pre-release line; the extension reads `FASTMCP_DOCKET_URL` (default `memory://`, `redis://…` for distributed workers), `FASTMCP_DOCKET_NAME`, and `FASTMCP_DOCKET_CONCURRENCY`; and out-of-process workers run as `python -m fastmcp_tasks.worker_cli worker server.py`, sharing a queue with any server on the same URL and name. A `task=True` tool on a server with no registered extension fails loudly at startup rather than silently running inline.

Do not write code against these surfaces, and do not copy an upstream snippet into this repository, until `import fastmcp_tasks` succeeds and the signatures have been read from the installed package.

## Related References

- [Version and source routing](version-and-source-routing.md) — the pinned baseline, the meta-package split, and the authority order. It is the only file in this skill that names a release number.
- [Protocol eras and sessions](protocol-eras-and-sessions.md) — `await ctx.elicit(...)` inside a background task raises `ToolError`. Background tasks gather input with the guard/return pattern instead: return an `InputRequiredResult` carrying `input_requests`, then read `ctx.input_responses` and `ctx.request_state` when the task re-runs.
- [Lifespan](lifespan.md) — where the startup guard runs relative to user, extension, and provider lifespans.

## Design Rules

Before enabling the extra, define:

- task ownership and actor authorization;
- durable input, result, and error serialization;
- idempotency and duplicate-delivery behavior;
- retry, redelivery, timeout, cancellation, and retention;
- progress semantics;
- backend topology and worker lifecycle;
- status and result access after the originating request ends.

A task identifier is a locator, not a secret or an authorization grant. Authorize create, status, result, and cancel independently. Keep tokens, raw headers, and sensitive values out of task payloads and telemetry.

Use ordinary foreground execution when the result is short-lived and the client can wait. Adopt MCP tasks only when protocol-native progress, resumable result retrieval, worker distribution, or long execution materially improves the contract.

## Verification

Re-establish the boundary before any task work:

```bash
python -c "import fastmcp_tasks"
python -c "import fastmcp.server.tasks"
python -c "from fastmcp.dependencies import CurrentDocket"
python -c "from fastmcp.utilities.tasks import TASKS_EXTENSION_ID; print(TASKS_EXTENSION_ID)"
python -c "from fastmcp.settings import Settings; print(len(Settings.model_fields))"
python -c "from importlib.metadata import distribution; print(*[r for r in distribution('fastmcp').requires if 'tasks' in r], sep='\n')"
fastmcp --help
```

Expected in this repository:

- the first three fail — `ModuleNotFoundError`, `ModuleNotFoundError`, `ImportError`;
- `TASKS_EXTENSION_ID` prints `io.modelcontextprotocol/tasks`;
- `Settings` reports 33 fields;
- the `tasks` extra resolves to `fastmcp-tasks`, not `fastmcp-slim`;
- the CLI lists eleven commands and no `tasks`.

After adding the extra, re-read the installed `fastmcp_tasks` source before writing against it, then exercise: forbidden, optional, and required modes; the extension lifespan and worker startup/shutdown; status, result, and cancellation; progress and polling; backend restart and redelivery; duplicate delivery and idempotency; TTL expiry; the guard/return input pattern; and actor authorization across every task operation.

Resolve every discrepancy against installed source and record the exact manifest, lockfile, environment, and command used.
