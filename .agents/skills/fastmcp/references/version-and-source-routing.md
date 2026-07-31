# Version and Source Routing

## Purpose

Use this reference before every FastMCP task. It prevents a release-pinned project from accidentally adopting APIs documented for a newer branch.

**This file is the only place in this skill that names a release number.** Every other reference states behavior version-neutrally ("the pinned release", "installed source") and defers here for the baseline. When a pin moves, correct this file; the rest stay accurate by construction.

## Authority Order

1. Owning repository instructions and dependency declarations.
2. The active lockfile and environment that will run the code.
3. Installed FastMCP source, signatures, metadata, and bundled tests.
4. The Git tag and generated SDK reference matching the installed release.
5. Live FastMCP guides for concepts and recommended composition.
6. Upstream main only for an explicit upgrade or future-feature evaluation.

When these disagree, preserve the installed contract and report the mismatch. Do not silently adapt an example from main.

## Resolve the Runtime

Inspect the owner's Python declaration and lockfile first. Account for multiple environments, editable installs, containers, and command wrappers.

```bash
python --version
python -c "import fastmcp; print(fastmcp.__version__)"
python -c "from importlib.metadata import distribution; d = distribution('fastmcp'); print(d.version); print(*d.requires, sep='\n')"
python -c "import inspect, fastmcp; print(inspect.getfile(fastmcp))"
```

**In 4.x, `fastmcp` is a meta-package — the code lives in `fastmcp-slim`.** Verified on the installed release: the `fastmcp` distribution contains **6 files, all metadata and zero Python modules**, while `fastmcp-slim` owns **260 files including 245 Python modules**. `distribution('fastmcp').requires` returns `fastmcp-slim[client,server]==<same version>`, one `fastmcp-slim[<extra>]==<same version>` entry per optional extra (`anthropic`, `apps`, `azure`, `code-mode`, `gemini`, `openai`), and — note the different target — `fastmcp-tasks==<same version>` for the `tasks` extra.

Consequences when resolving the runtime:

- Listing files for the `fastmcp` distribution shows an apparently empty package. Query `fastmcp-slim` for file ownership.
- `import fastmcp` still resolves normally — the importable package name is unchanged — so `inspect.getfile()` remains the reliable way to locate source.
- Version pins must stay aligned: `fastmcp` and `fastmcp-slim` are pinned to the same exact version by construction.
- `tasks` is the one extra that does **not** resolve to `fastmcp-slim`. It pulls a separate `fastmcp-tasks` distribution, and `fastmcp-slim` declares no `tasks` extra at all. See [Background tasks](tasks.md).

Use the repository package manager when present, such as uv run, poetry run, or a project virtual environment. Read pyproject.toml plus uv.lock, poetry.lock, or the applicable requirements lock. Extras control whether Apps, Code Mode, tasks, auth providers, or optional integrations are importable; never infer extras from the base version alone.

For exact behavior, inspect the installed module and signature:

```bash
python -c "import inspect; from fastmcp import FastMCP; print(inspect.signature(FastMCP))"
python -c "import inspect; from fastmcp import Client; print(inspect.getsourcefile(Client)); print(inspect.signature(Client))"
```

## Installed Baseline

**Soleaux installs FastMCP `4.0.0b1`** — declared at `pyproject.toml`, locked in `uv.lock`, and confirmed in the root `.venv` on Python 3.14. It is a **pre-release**; the current _stable_ channel is `3.4.5`.

`4.0.0b1` is a **beta**. Upstream's pre-release warning lives on [What's New](https://gofastmcp.com/getting-started/whats-new), not on the SDK reference pages. Nothing on `/python-sdk/*` marks an API as beta-only or unstable, so **a symbol's stability cannot be judged from its reference page**. Assume any v4 surface may move between pre-releases and re-verify after every pin change.

Because Soleaux runs a v4 beta, the unversioned pages at `https://gofastmcp.com/python-sdk/*` and the v4 guides are useful design guidance, but they carry no version marker and cannot establish the installed API. Resolve every residual discrepancy in favour of installed source.

**Pre-release deltas are real, and they move in both directions.** This pin has gone `4.0.0a1` → `4.0.0a2` → `4.0.0b1` (with an `a1` rollback and restore along the way). Each move changed observable API surface, so a reference written against one pre-release is not automatically valid on its neighbour. The verified `a2` → `b1` delta:

| Surface | `4.0.0a2` | `4.0.0b1` (installed) |
| --- | --- | --- |
| MCP SDK floor | `mcp==2.0.0b2`, `mcp-types==2.0.0b2` (beta pins) | **`mcp>=2.0.0,<3`, `mcp-types>=2.0.0,<3`** (stable GA) |
| `mcp.types` | `ModuleNotFoundError` | **restored as a permanent alias** of `mcp_types` — same objects, snake_case fields |
| `ctx.sample`, `ctx.sample_step`, `ctx.list_roots` | deprecated and era-gated | **removed from the server API** |
| 3.x-era compatibility shims | present | **removed** |
| Authorization checks | `require_scopes`, `restrict_tag`, `run_auth_checks` | adds **`require_roles`** |
| `fastmcp-slim` distribution | 266 files, 251 Python modules | **260 files, 245 Python modules** |

`Settings` (33 fields), the 11-name `fastmcp.dependencies.__all__`, the 11-command CLI, `UserSession`/`SessionId`, `FastMCP.add_extension`, `TaskConfig`/`TASKS_EXTENSION_ID` in `fastmcp.utilities.tasks`, protocol eras, the `Client` constructor, `mount()`, `create_proxy`, `httpx2`, decorator options, and `McpError` construction are **identical on both**, which is why the version-neutral references survive a pin move untouched.

Note also that the aggregate `https://pypi.org/pypi/fastmcp/json` endpoint reports `info.version: "3.4.5"` because it excludes pre-releases; use the version-specific endpoints to confirm the 4.x line exists.

Because `fastmcp` is a meta-package, the manifest also pins `fastmcp-slim==4.0.0b1` explicitly. That is deliberate: uv's default `if-necessary-or-explicit` pre-release mode will not accept a transitive pre-release pin, and resolving with `--prerelease=allow` records a mode in `uv.lock` that `uv sync --locked` then rejects. Pinning the split package makes the pre-release explicitly requested, so the default mode resolves and CI's `--locked` sync stays green without a workspace-wide relaxation.

### Declared extras

**This repository declares zero extras.** The install is `fastmcp-slim[client,server]` only. Consequently the `apps`, `tasks`, `code-mode`, `openai`, `gemini`, `azure`, `anthropic`, and `mcp` surfaces are **not importable here**, and enabling one is a manifest change to `pyproject.toml` — not a code change. References documenting those surfaces say so at the top; treat their claims as upstream-documented rather than locally verified.

Importability is not availability in the other direction either: a module that imports cleanly may still fail at call time when its extra is missing — the auth providers are the standard case. Test the actual operation.

The full extras matrix and dependency floors live in [Settings and packaging](settings-and-packaging.md).

## Verified v4 Behavior

Confirmed by inspecting installed source. This is the delta a reader arriving from v3 must absorb; the individual references carry the detail.

### Protocol and wire format

- **Protocol models live in the standalone `mcp_types` package.** The stable MCP SDK 2.0.0 restored `mcp.types` as a **permanent wildcard alias** — every name is the same object (`mcp.types.Tool is mcp_types.Tool`), with the same snake_case fields. The rule of thumb: import `mcp_types` in library code (it is a core `fastmcp-slim` dependency, so it resolves without the `mcp` extra), and write `mcp.types` in code a user copies (anyone installing `fastmcp` gets the full SDK, so the alias always resolves there). This repository's application code imports `mcp_types`.
- **Protocol model attributes renamed camelCase → snake_case** (`inputSchema` → `input_schema`, `mimeType` → `mime_type`, `nextCursor` → `next_cursor`, and so on).

  Read this precisely: **the JSON wire format did not change.** The MCP spec still puts `inputSchema` on the wire, and the pydantic models keep camelCase serialization aliases. What moved is **Python attribute access**. So `tool.input_schema` is the attribute, while `model_dump(by_alias=True)` still emits `inputSchema` — and construction still accepts `Tool(name=..., inputSchema=...)` through the validation alias without warning. Do not "fix" camelCase appearing in a serialization or wire context; that is correct, and changing it silently alters what a client sees.

- **`mcp_camelcase_compat` defaults `True`**, installing warn-once `@property` shims in `fastmcp/_compat.py` that route a camelCase read to its snake_case attribute. Stale camelCase therefore still _runs_, emitting a single `FastMCPDeprecationWarning` per `(class, name)`. Treat the shim as a **migration aid, not a contract**.

  The shim re-reads the setting on **every access**, so it is a genuine runtime toggle: assigning `fastmcp.settings.mcp_camelcase_compat = False` after import — or exporting `FASTMCP_MCP_CAMELCASE_COMPAT=false` — takes effect immediately, and a camelCase read then raises `AttributeError` rather than warning. Use that to prove a migration is complete. Warnings alone will not: warn-once means the second read through the same shim is silent.

- **Protocol eras.** `Client(mode=...)` defaults to `"auto"`; against a FastMCP server that negotiates the sessionless `2026-07-28` era rather than a handshake era. This changes initialization, middleware, and state semantics. See [Protocol eras and sessions](protocol-eras-and-sessions.md).
- **`httpx` is replaced by `httpx2`.** `import httpx` raises `ModuleNotFoundError`.

### Removed server APIs

`FastMCP.as_proxy`, `import_server`, `add_tool_transformation`, and `remove_tool` are all absent.

- Proxying is now the module-level `from fastmcp.server import create_proxy` — **not** a `FastMCP` method, and **not** in a `fastmcp.server.proxy` module (no such module exists).
- `mount()` takes `namespace`, not `prefix`, and has no `as_proxy`. Both removed keywords raise `TypeError`.
- Tool transformation goes through `FastMCP.add_transform(...)`; tool removal belongs to the owning local provider.

### Removed decorator options

`@mcp.tool` no longer accepts `exclude_args`, `serializer`, or `decorator_mode`.

- Replace `exclude_args` with dependency injection: give the parameter a `Depends(factory)` default from `fastmcp.dependencies`. Dependency parameters are excluded from the MCP schema automatically and — unlike `exclude_args` — carry no requirement that the parameter have a default.
- Replace `serializer=` by returning a `ToolResult`.
- `decorator_mode` is gone; decorators return the original function, so retrieve the component with `await mcp.get_tool(name)`.

`task=` is **tool-only**. `@mcp.resource` and `@mcp.prompt` do not accept it and raise `TypeError`.

### Error construction

`McpError` (an alias of `MCPError`, importable from `fastmcp.exceptions`) takes `code` and `message` directly:

```python
from fastmcp.exceptions import McpError

McpError(code=-32602, message="...")   # preferred
McpError(-32602, "...")                # also valid — signature is (code, message, data=None)
```

What v4 removed is v3's **single-argument `ErrorData` wrapper** form. `McpError(ErrorData(...))` now raises `TypeError: missing 1 required positional argument: 'message'`. `ErrorData` itself still exists in `fastmcp.exceptions`. Note that `from mcp import McpError` fails — that package spells it `MCPError`; import from `fastmcp.exceptions`.

### Relocations and additions

- **`fastmcp.dependencies` exports exactly 11 names**: `Depends`, `Dependency`, `Shared`, `Progress`, `ProgressLike`, `TokenClaim`, `CurrentContext`, `CurrentFastMCP`, `CurrentHeaders`, `CurrentRequest`, `CurrentAccessToken`. `CurrentDocket` and `CurrentWorker` raise `ImportError` directing you to `fastmcp-tasks`.
- **Background tasks left core.** `fastmcp.server.tasks` raises `ModuleNotFoundError`; `TaskConfig` and `TASKS_EXTENSION_ID` relocated to `fastmcp.utilities.tasks`, and the engine ships in the separate `fastmcp-tasks` distribution. See [Background tasks](tasks.md).
- **Explicit session handles** `UserSession` and `SessionId` are present in `fastmcp/server/sessions.py`, alongside `FastMCP(session_state_store=...)`.
- **`FastMCP.add_extension()`** is present, backed by `fastmcp/server/extensions.py`.
- **Telemetry** lives at `fastmcp.telemetry` (not `fastmcp.utilities.telemetry`), with server and client seams in `fastmcp/server/telemetry.py` and `fastmcp/client/telemetry.py`.
- `StreamableHttpTransport(sse_read_timeout=)` is **removed**, not deprecated. Use `Client(transport, timeout=...)` or a custom `httpx_client_factory`.
- `ctx.sample`, `ctx.sample_step`, and `ctx.list_roots` are **removed from the server API** — server-initiated push requests have no transport on the sessionless era. Call an LLM from the server directly; take roots as tool arguments or ask through the guard pattern. See [Interactivity and observability](interactivity-and-observability.md).
- Path-traversal protection on templated resources is enabled by default via `ResourceSecurity`.
- `@mcp.completion` handlers are new.

Dependency floors carried by the installed extras — `mcp>=2.0.0,<3`, `mcp-types>=2.0.0,<3`, `httpx2>=2.5.0`, `starlette>=1.0.1`, `pydantic[email]>=2.12.0`, `cyclopts>=4.0.0`, `uncalled-for>=0.2.0`, `py-key-value-aio>=0.4.4,<0.5.0` — are enumerated in [Settings and packaging](settings-and-packaging.md).

## Live Documentation Surfaces

The v4 guides and generated SDK reference are the matching documentation for this pin. Load the focused page, not the corpus.

| Surface | Use |
| --- | --- |
| [What's New](https://gofastmcp.com/getting-started/whats-new) | The only page carrying the pre-release stability warning |
| [Upgrading from FastMCP 3](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3) | Authoritative v3 → v4 migration narrative |
| [llms.txt](https://gofastmcp.com/llms.txt) | Discover the relevant guide without loading the full corpus |
| [Python SDK reference](https://gofastmcp.com/python-sdk) | Public classes, methods, parameters, return types — carries no version marker |
| [FastMCP releases](https://github.com/PrefectHQ/fastmcp/releases) | Match a release number to its tag and notes |
| [llms-full.txt](https://gofastmcp.com/llms-full.txt) | Search only when the index and focused pages are insufficient |

Server, component, provider, composition, transform, authorization, deployment, and MCP Apps design guidance lives under `gofastmcp.com/servers/*`, `/deployment/*`, and `/apps/*`. The focused reference for each topic in this skill links the specific pages it depends on.

Live documentation can change; refresh it when provenance itself matters.

## Route a Question Through the Corpus

- Exact import, constructor, decorator option, or return type: installed source, then the matching generated SDK page.
- Intended usage and tradeoffs: focused live guide linked from llms.txt.
- Edge case or lifecycle semantics: matching release tests and examples.
- Regression or implementation detail: matching release source.
- v3 → v4 migration: the [upgrading guide](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3) plus both lockfiles.
- A v4 feature: **installed source first.** v4 is current here, not future-facing. Reach for upstream main only for work beyond the installed pre-release.
- A surface behind an undeclared extra: upstream documentation only, labelled as unverified. You cannot introspect what is not installed.
- Suspected framework defect: create a minimal reproduction against the installed version before reading issue discussions.

Prefer repository-local source over memory. Search narrowly by symbol and module; do not ingest the entire site or repository into working context.

## Provenance to Record

In implementation notes or the final handoff, record:

- Python and FastMCP versions;
- declared extras and lockfile;
- installed module path;
- release tag or commit used for exact APIs;
- focused guide or SDK pages consulted;
- whether any statement came only from main or from an undeclared extra;
- the negotiated protocol era, when client behavior is in scope;
- validation commands and host behavior not exercised.

Do not treat release management, maintainer triage, upstream contribution, or unreleased roadmap discussion as part of this workflow; those are maintainer concerns outside this skill's scope.
