# Providers and Composition

## Purpose and Source Discipline

Use this reference when FastMCP components come from local registration, Python files, another MCP server, agent-skill directories, or a custom dynamic catalog, and when multiple servers must be composed. Use [Providers and transforms](providers-and-transforms.md) separately for transform pipelines, tool reshaping, search, visibility, resource/prompt bridges, fingerprinting, and Code Mode.

This implementation-oriented guide was reconciled on 2026-07-14 against every section of these live FastMCP pages:

- [Providers overview](https://gofastmcp.com/servers/providers/overview)
- [Local provider](https://gofastmcp.com/servers/providers/local)
- [Filesystem provider](https://gofastmcp.com/servers/providers/filesystem)
- [MCP proxy provider](https://gofastmcp.com/servers/providers/proxy)
- [Skills provider](https://gofastmcp.com/servers/providers/skills)
- [Composing servers](https://gofastmcp.com/servers/composition)
- [Custom providers](https://gofastmcp.com/servers/providers/custom)

Exact imports, signatures, defaults, return types, and precedence come from installed source, not from the linked pages. Start with [Version and source routing](version-and-source-routing.md) for the pinned baseline, and load [Server components](server-components.md) for component option semantics.

### v3 → v4 Composition Migration

These composition APIs changed shape between eras. Each statement below was re-verified against installed source.

| v3 surface | Status on the pinned release | Replacement |
| --- | --- | --- |
| `mount(server, prefix=...)` | `TypeError: FastMCP.mount() got an unexpected keyword argument 'prefix'` | `mount(server, namespace=...)` |
| `mount(server, as_proxy=...)` | `TypeError: ... unexpected keyword argument 'as_proxy'` | `mount(create_proxy(target), namespace=...)` |
| `FastMCP.import_server()` | Absent (`hasattr` is `False`) | `mount()` for a live link, or copy components into a `LocalProvider` for a static snapshot |
| `FastMCP.as_proxy()` | Absent (`hasattr` is `False`) | Module-level `create_proxy()` |
| `FastMCP.create_proxy()` | Never a method — `hasattr(FastMCP, "create_proxy")` is `False` | `from fastmcp.server import create_proxy` |
| `import httpx` | `ModuleNotFoundError: No module named 'httpx'` | `import httpx2` |

`import_server` copied a child's components into the parent once; `mount()` keeps a live link. When a one-time snapshot is genuinely required, enumerate the child catalog and register the copies explicitly so the parent owns them.

### Known Live-Guide Differences

Surface these concrete differences instead of hiding them behind a generic version warning:

- The Local Provider page presents `"error"` as the duplicate default. On the pinned release, standalone `LocalProvider()` defaults to `"error"`, while `FastMCP(...)` without an explicit `on_duplicate` normalizes its local provider to `"warn"`. Set it explicitly.
- The Providers overview simplifies lookup as registration-order, first-provider-wins, while the Composition page says the most recently mounted server wins conflicts. The installed `AggregateProvider` queries providers together, chooses the highest component version, and for equal versions retains the first registered result. `LocalProvider` is registered first. Do not use a collision as routing.
- Standalone decorators expose more options than the Filesystem Provider page inventories, including versions, authorization, tool execution controls, and resource path-safety screening. Inspect the installed signatures.
- `CodexSkillsProvider` scans `/etc/codex/skills/` and `~/.codex/skills/`. Current Codex repository and user authoring roots include `.agents/skills/` and `~/.agents/skills/`; use an explicit `SkillsDirectoryProvider` for those roots.

## Choose the Provider Boundary

| Need | Preferred surface |
| --- | --- |
| Components defined directly on one server | The built-in `LocalProvider` through decorators or `add_*` |
| Reusable local component registry shared by servers | Standalone `LocalProvider` |
| Python modules discovered from a trusted directory | `FileSystemProvider` |
| One live in-process FastMCP server included in another | `mount()` / `FastMCPProvider` |
| Remote, subprocess, URL, transport, or MCP-config source | `create_proxy()` / `ProxyProvider` |
| One agent skill directory | `SkillProvider` |
| One or more roots containing many skills | `SkillsDirectoryProvider` or a verified vendor provider |
| Database, API, configuration, tenant, or plugin-defined catalog | Custom `Provider` |
| Request logging, rate limiting, authentication, or request interception | Middleware, not a provider |
| Namespace, rename, schema adaptation, search, or catalog filtering | A provider- or server-level transform |

A provider supplies component objects. It does not replace component execution, middleware, authentication, application authorization, transport policy, or lifecycle ownership.

## Provider Model and Resolution

Every `FastMCP` server aggregates providers. Its `LocalProvider` is registered first, followed by constructor providers and later `add_provider()` or `mount()` providers. Providers can source tools, resources, resource templates, and prompts.

When a client lists a component type, the server aggregates successful providers, applies provider transforms, then server and session transforms, visibility, and authorization filtering. On the pinned release:

- list operations retain components from all successful providers and warn on duplicate component keys;
- provider failures are logged and skipped under the default `"warn"` aggregate error strategy;
- exact get operations query providers and select the highest compatible version;
- equal-version collisions retain the first registered result;
- local components therefore win an equal identity and version;
- `provider_error_strategy="raise"` is available on `AggregateProvider` and `FastMCPProxy` when a failure must be fatal.

Namespace independent owners and assert the final catalog. Never treat duplicate precedence as a public routing contract.

Provider transforms affect one source. Server transforms affect the aggregate catalog. `mount(server, namespace="api")` applies a `Namespace` transform to the mounted provider. Load the transforms reference before composing additional adaptations because order changes public identity and lookup.

## LocalProvider

### Register Components

For an ordinary server, use decorators; they register with its `LocalProvider` immediately.

```python
from fastmcp import FastMCP

mcp = FastMCP("MyServer", on_duplicate="error")

@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

@mcp.resource("data://config")
def get_config() -> str:
    return '{"version": "1.0"}'

@mcp.prompt
def analyze(topic: str) -> str:
    return f"Please analyze: {topic}"
```

Add pre-built component objects directly when another owner constructed them:

```python
from fastmcp.tools import Tool

tool = Tool.from_function(greet, name="custom_greet")
mcp.add_tool(tool)
mcp.add_resource(resource_object)
mcp.add_prompt(prompt_object)
```

The decorator and component-object contracts live in Server Components. This provider reference owns where they are stored and exposed.

### Duplicate Modes

Set unified `on_duplicate` on `FastMCP` or `LocalProvider`:

| Mode        | Result                                                      |
| ----------- | ----------------------------------------------------------- |
| `"error"`   | Raise `ValueError` and preserve the existing registration   |
| `"warn"`    | Log a warning and replace the existing registration         |
| `"replace"` | Replace silently                                            |
| `"ignore"`  | Keep the existing component and ignore the new registration |

Prefer `"error"` for deterministic assembly. Use replacement only when reload or a dynamic registry intentionally owns the identity, and test it.

### Remove and Control Visibility

Remove only locally owned components through the local provider:

```python
mcp.local_provider.remove_tool("my_tool")
mcp.local_provider.remove_resource("data://info")
mcp.local_provider.remove_prompt("my_prompt")
```

Each removal method takes `(name_or_uri, version=None)`; the optional version selects one registration when several share an identity. Removal during a live request is catalog mutation; verify list-change notifications. There is no `FastMCP.remove_tool()` — removal is a local-provider operation only.

Use visibility to tailor a catalog without deleting its owner:

```python
mcp.disable(tags={"admin"})
mcp.enable(keys={"tool:get_status@"}, only=True)
```

Use actual installed component keys rather than hand-building them. Disabled components are not listed or callable through the server. Visibility is not authorization.

### Standalone LocalProvider

Use a standalone provider to share a trusted registry, test it independently, or attach it to multiple servers:

```python
from fastmcp import FastMCP
from fastmcp.server.providers import LocalProvider

shared = LocalProvider(on_duplicate="error")

@shared.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

server_a = FastMCP("A", providers=[shared])
server_b = FastMCP("B", providers=[shared])
```

Standalone providers support `enable()` and `disable()`. Decide whether sharing the provider also intentionally shares later registration and visibility mutation.

## FileSystemProvider

### Choose Filesystem Discovery

`FileSystemProvider(root=".", reload=False)` recursively discovers standalone-decorated Python functions without requiring component files to import the server or the server to import each module. Use it when a file-based registry reduces real coordination cost. Prefer direct imports for small catalogs or when explicit assembly is easier to audit.

Discovery imports and executes Python modules. Treat the root as trusted executable code, resolve it from the server file rather than the process working directory, and never scan user-writable or unreviewed content.

```python
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers import FileSystemProvider

provider = FileSystemProvider(
    root=Path(__file__).parent / "components",
    reload=False,
)
mcp = FastMCP("FilesystemServer", providers=[provider])
```

### Standalone Decorators and Options

Component files import standalone decorators:

```python
from fastmcp.prompts import prompt
from fastmcp.resources import resource
from fastmcp.tools import tool

@tool(name="add-numbers", description="Add two numbers.", tags={"math"})
def add(a: float, b: float) -> float:
    return a + b

@resource("users://{user_id}/profile", mime_type="application/json")
def get_profile(user_id: str) -> str:
    return f'{{"id": "{user_id}"}}'

@prompt(name="explain-concept", tags={"education"})
def explain(topic: str) -> str:
    return f"Explain {topic} with examples."
```

The live page inventories:

- tool: `name`, `title`, `description`, `icons`, `tags`, `output_schema`, `annotations`, and `meta`;
- resource: required `uri`, then `name`, `title`, `description`, `icons`, `mime_type`, `tags`, `annotations`, and `meta`;
- prompt: `name`, `title`, `description`, `icons`, `tags`, and `meta`.

The installed standalone decorators also expose:

- all three: `version` and `auth`;
- tool: `task`, `timeout`, and `run_in_thread`;
- resource: `security` for path-safety screening of extracted template parameters;
- automatic resource-template detection from URI parameters and function arguments.

Two shape differences matter when moving code between the server decorators and the standalone ones:

- `task=` is **tool-only**. `@resource(task=...)` and `@prompt(task=...)` raise `TypeError` on the pinned release.
- `app=` exists only on `@mcp.tool` and `@mcp.resource`. The standalone `@tool` and `@resource` decorators do not accept it, so a filesystem-discovered component cannot declare MCP Apps configuration.

`exclude_args` and `serializer` were v3 tool options and are **absent in v4** — use dependency injection and `ToolResult` respectively. Use the Server Components reference for option semantics and inspect installed signatures before selecting them.

### Discovery and Import Rules

The provider:

- recursively scans only `.py` files;
- skips `__init__.py` and `__pycache__`;
- ignores decorated functions whose names start with `_`;
- silently skips files without standalone `@tool`, `@resource`, or `@prompt` functions;
- allows any number and mix of component types per file;
- uses the function name unless the decorator overrides it;
- treats parameterized resource URIs or functions with arguments as templates.

Directory structure is organizational only. Organize by component kind or domain; discovery is the same.

If the root has `__init__.py`, modules load as package members and relative imports work. Without it, files load directly through `importlib.util.spec_from_file_location`. Exercise the same layout and entrypoint production uses.

### Reload and Import Failures

With `reload=True`, the provider re-discovers files on every request, re-imports changed modules, and updates new, changed, or removed components. It adds per-request overhead and belongs in development, not production.

An import failure logs a warning and does not prevent other files or the server from loading. Failed files are re-logged only after their modification time changes. Because graceful degradation can silently remove required capabilities, production validation must assert the expected catalog.

The live guide's example is exercised with:

```bash
fastmcp inspect examples/filesystem-provider/server.py
fastmcp run examples/filesystem-provider/server.py
```

Resolve commands against the installed CLI, then call representative components through `Client`.

## ProxyProvider and create_proxy

### Choose Proxying

Use a proxy to bridge transports, aggregate independent MCP backends, add a controlled gateway, or provide a stable endpoint while a backend location changes. A proxy mirrors another server over MCP; it is not the same as mounting an in-process FastMCP server.

```python
from fastmcp.server import create_proxy

proxy = create_proxy("https://example.com/mcp", name="MyProxy")

if __name__ == "__main__":
    proxy.run()
```

`create_proxy` is a **module-level function exported from `fastmcp.server`**, not a `FastMCP` method — `hasattr(FastMCP, "create_proxy")` is `False`, and there is no `fastmcp.server.proxy` module. The proxy classes live one level deeper, in `fastmcp.server.providers.proxy`.

```text
create_proxy(target, *, mode: str | None = None, **settings) -> FastMCPProxy
```

`target` accepts a connected or disconnected FastMCP `Client`, client transport, FastMCP server, SDK server, URL/`AnyUrl`, server-script `Path`, MCP configuration object or dictionary, or compatible string. `mode` selects the upstream protocol era for the proxy's own client; see [Version and source routing](version-and-source-routing.md) for era negotiation. Extra settings pass to `FastMCPProxy`, including ordinary server settings such as `name`.

### Connection and Session Semantics

Proxies are lazy. Constructing or starting the local proxy does not contact the upstream. A downstream `initialize` request initializes the upstream before the proxy responds. An unavailable server, wrong endpoint, failed subprocess, or upstream authentication failure therefore fails downstream initialization.

After initialization, the proxy forwards ping, component lists and lookups, calls, resource reads, prompt rendering, and negotiated MCP features.

The normal factory path creates isolated backend sessions so concurrent downstream work does not mix context. Passing an already-connected client intentionally reuses its session:

```python
from fastmcp import Client
from fastmcp.server import create_proxy

async with Client("backend_server.py") as connected_client:
    shared_session_proxy = create_proxy(connected_client)
```

Use a connected shared session only for a synchronized or deliberately stateful single-session workflow. Test concurrency, cancellation, and teardown.

### Transport Bridging

The local and upstream transports are independent:

```python
from fastmcp.server import create_proxy

# Remote HTTP backend exposed locally over stdio.
http_to_stdio = create_proxy("https://example.com/mcp")
http_to_stdio.run()

# Local subprocess backend exposed over HTTP.
stdio_to_http = create_proxy("local_server.py")
stdio_to_http.run(transport="http", host="0.0.0.0", port=8080)
```

Binding `0.0.0.0` is network exposure. Apply the owning HTTP authentication, application authorization, TLS, origin, body-size, timeout, and error-masking contracts.

### Feature Forwarding and Selective Handlers

The proxy forwards:

| Feature     | Forwarded behavior                                             |
| ----------- | -------------------------------------------------------------- |
| Roots       | Backend filesystem-root requests reach the downstream client   |
| Sampling    | Backend completion requests reach the downstream sampling path |
| Elicitation | Backend user-input requests reach the downstream client        |
| Logging     | Backend log messages reach the downstream client               |
| Progress    | Backend progress notifications reach the downstream client     |

Disable an unwanted callback at the upstream client boundary:

```python
from fastmcp.server.providers.proxy import ProxyClient

backend = ProxyClient(
    "backend_server.py",
    sampling_handler=None,
    log_handler=None,
)
```

Inspect the installed `ProxyClient` for its complete callback surface. Forwarded sampling, elicitation, roots, and logs expand the gateway trust boundary.

### Configuration and Multi-Server Proxies

`create_proxy()` accepts MCP configuration dictionaries:

```python
config = {
    "mcpServers": {
        "weather": {
            "url": "https://weather.example.com/mcp",
            "transport": "http",
        },
        "calendar": {
            "url": "https://calendar.example.com/mcp",
            "transport": "http",
        },
    }
}

composite = create_proxy(config, name="Composite")
```

Multi-server configuration prefixes components automatically:

| Component         | Prefixed form              |
| ----------------- | -------------------------- |
| Tool              | `{prefix}_{tool_name}`     |
| Prompt            | `{prefix}_{prompt_name}`   |
| Resource          | `protocol://{prefix}/path` |
| Resource template | `protocol://{prefix}/...`  |

Inspect final names and URIs; do not assume a configuration key is a safe public namespace.

### Mirrored Components

Proxy components mirror remote state and are not locally mutable. Copy one into a local provider when local visibility or mutation is required:

```python
mirrored = await proxy.get_tool("useful_tool")
if mirrored is None:
    raise RuntimeError("backend tool missing")

local = mirrored.copy()
server.add_tool(local)
server.disable(keys={local.key})
```

The copy is a local snapshot. Define how schema, metadata, version, and backend behavior drift will be detected.

### Catalog Caching and Session Reuse

Proxying adds connection and network latency. The live guide illustrates local operations around 1–2 ms and HTTP-proxied operations in the hundreds of milliseconds; treat those values as examples, not budgets.

`ProxyProvider(client_factory, cache_ttl=None)` caches raw upstream tool, resource, template, and prompt lists. The cache:

- defaults to 300 seconds;
- refreshes on an explicit `list_*` operation;
- is shared across proxy sessions;
- is followed by per-session visibility, authorization, and transforms;
- accepts a custom TTL or `cache_ttl=0` for a dynamic catalog.

```python
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider

default_cache = ProxyProvider(lambda: ProxyClient("https://backend/mcp"))
short_cache = ProxyProvider(
    lambda: ProxyClient("https://backend/mcp"),
    cache_ttl=60,
)
dynamic_catalog = ProxyProvider(
    lambda: ProxyClient("https://backend/mcp"),
    cache_ttl=0,
)
```

For a known stateless HTTP backend, one reference-counted client can avoid repeated initialization:

```python
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

base = ProxyClient("https://backend:8000/mcp")
shared = base.new()

proxy = FastMCPProxy(
    client_factory=lambda: shared,
    name="ReusedSessionProxy",
)
```

Do not reuse sessions for subprocess backends or servers with session state. Fresh sessions are the safe default.

### Lower-Level Control and Existing Servers

Use `FastMCPProxy(client_factory=..., provider_error_strategy="warn" | "raise", ...)` for an explicit session factory and aggregate error policy. Use `ProxyProvider` when only the provider belongs inside an existing server.

```python
from fastmcp import FastMCP
from fastmcp.server import create_proxy

server = FastMCP("Gateway")
external = create_proxy("https://external.example.com/mcp")
server.mount(external, namespace="external")
```

Verify unavailable and auth-failed initialization, callback failure, cancellation, transport loss, cache refresh, concurrent sessions, and representative calls.

## Skills Providers

### Resource Model

Skills providers expose agent-skill directories as MCP resources so clients can discover main instructions, inspect supporting files, and download content.

A valid skill directory contains a main file, defaulting to `SKILL.md`; the directory name is the skill identifier. YAML frontmatter may supply `description`. Without it, the installed provider uses up to 200 characters from the first meaningful body line.

Each skill exposes:

| URI                         | Meaning                               |
| --------------------------- | ------------------------------------- |
| `skill://{skill}/SKILL.md`  | Main instruction file                 |
| `skill://{skill}/_manifest` | Synthetic JSON manifest               |
| `skill://{skill}/{path}`    | Supporting file or template expansion |

The manifest contains the skill name plus every file path, byte size, and `sha256:` hash. It is discovery and integrity metadata, not proof that a skill is trusted or authorized.

### Single Skill and Directory Providers

Use `SkillProvider(skill_path, main_file_name="SKILL.md", supporting_files="template")` for one skill:

```python
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillProvider

mcp = FastMCP("One Skill")
mcp.add_provider(
    SkillProvider(
        Path("/srv/skills/pdf-processing"),
        supporting_files="template",
    )
)
```

Use `SkillsDirectoryProvider(roots, reload=False, main_file_name="SKILL.md", supporting_files="template")` for one or more roots:

```python
from pathlib import Path

from fastmcp.server.providers.skills import SkillsDirectoryProvider

provider = SkillsDirectoryProvider(
    roots=[
        Path.cwd() / ".agents" / "skills",
        Path.home() / ".agents" / "skills",
    ],
    reload=False,
    main_file_name="SKILL.md",
    supporting_files="template",
)
```

Each immediate child directory containing the main file becomes a skill. With multiple roots, the first root wins duplicate directory names. Missing roots and invalid child directories are skipped; assert required skills.

### Vendor Providers

The installed convenience providers use fixed roots:

| Provider                 | Root(s)                                       |
| ------------------------ | --------------------------------------------- |
| `ClaudeSkillsProvider`   | `~/.claude/skills/`                           |
| `CursorSkillsProvider`   | `~/.cursor/skills/`                           |
| `VSCodeSkillsProvider`   | `~/.copilot/skills/`                          |
| `CodexSkillsProvider`    | `/etc/codex/skills/`, then `~/.codex/skills/` |
| `GeminiSkillsProvider`   | `~/.gemini/skills/`                           |
| `GooseSkillsProvider`    | `~/.config/agents/skills/`                    |
| `CopilotSkillsProvider`  | `~/.copilot/skills/`                          |
| `OpenCodeSkillsProvider` | `~/.config/opencode/skills/`                  |

Vendor providers accept `reload` and `supporting_files`; roots and the main filename are fixed. `CodexSkillsProvider` gives its system root precedence. `VSCodeSkillsProvider` and `CopilotSkillsProvider` resolve to the same root; pick one rather than registering both.

For current Codex repository skills, configure `.agents/skills/` explicitly. The Codex manual identifies repository roots from the working directory through the repository root and the user root at `~/.agents/skills/`; the vendor provider does not implement that chain.

### Supporting-File Disclosure

Choose one exact mode:

| Mode | Listing behavior | Use |
| --- | --- | --- |
| `"template"` (default) | List only the main file and manifest; serve supporting paths through a resource template | Keep large catalogs compact; clients read the manifest and fetch selected files |
| `"resources"` | List every supporting file as an individual resource | Support clients that require flat up-front enumeration |

Requested paths are resolved and rejected if they escape the skill directory. Keep roots allowlisted anyway; every in-scope file, including an accidentally stored secret or binary, is legitimately exposable.

### Reload Mode

With `reload=True`, `SkillsDirectoryProvider` re-discovers skills on every list or read. New skills appear, removed skills disappear, and modified content is read from disk. Use it during active development, not on a production hot path.

### Client Utilities

`fastmcp.utilities.skills` exposes `list_skills`, `get_skill_manifest`, `download_skill`, and `sync_skills`; `overwrite` is keyword-only on the two writing helpers:

```python
from pathlib import Path

from fastmcp import Client
from fastmcp.utilities.skills import (
    download_skill,
    get_skill_manifest,
    list_skills,
    sync_skills,
)

async with Client("https://skills.example.com/mcp") as client:
    skills = await list_skills(client)
    manifest = await get_skill_manifest(client, "pdf-processing")
    path = await download_skill(
        client,
        "pdf-processing",
        Path.home() / ".agents" / "skills",
        overwrite=False,
    )
    paths = await sync_skills(
        client,
        Path.home() / ".agents" / "skills",
        overwrite=False,
    )
```

Preserve these behaviors:

- `list_skills()` discovers resources matching `skill://{name}/SKILL.md`;
- `get_skill_manifest()` validates manifest shape and returns paths, sizes, and hashes;
- `download_skill()` creates one directory and raises `FileExistsError` if it exists with `overwrite=False`;
- `sync_skills()` downloads all listed skills and skips existing ones with `overwrite=False`;
- `overwrite=True` permits replacement only after an explicit trust and ownership decision;
- the installed utilities constrain target paths but do not verify every downloaded byte against its advertised SHA256 hash; security-sensitive consumers must verify hashes before activation.

Treat downloaded skills as executable instructions and potentially executable scripts. Authenticate and authorize the source, stage downloads, review content, verify hashes, and activate only after validation.

## Composing Servers

### Live In-Process Mounting

`mount()` creates a live provider link from a parent to a child FastMCP server. Components added after mounting become visible through the parent. Mounted lifespans and middleware run for child operations.

```python
from fastmcp import FastMCP

weather = FastMCP("Weather")

@weather.tool
def get_forecast(city: str) -> str:
    return f"Sunny in {city}"

main = FastMCP("Main")
main.mount(weather, namespace="weather")
```

The installed signature is three parameters wide:

```text
mount(server: FastMCP, namespace: str | None = None,
      tool_names: dict[str, str] | None = None) -> None
```

- `namespace` applies a `Namespace` transform to the mounted provider;
- `tool_names` renames selected child tools before namespacing;
- mounting a server onto itself raises `ValueError("Cannot mount a server onto itself")`;
- `mount()` returns `None`; construct `FastMCPProvider` explicitly when later provider transforms are needed.

**v3's `prefix=` and `as_proxy=` were removed, not deprecated.** Both now raise `TypeError` at the call site rather than warning:

```text
mount(child, prefix="api")    -> TypeError: FastMCP.mount() got an unexpected keyword argument 'prefix'
mount(child, as_proxy=True)   -> TypeError: FastMCP.mount() got an unexpected keyword argument 'as_proxy'
```

Migrate `prefix="api"` to `namespace="api"`. Migrate `as_proxy=True` by proxying explicitly and mounting the result:

```python
from fastmcp.server import create_proxy

main.mount(create_proxy(child), namespace="api")
```

Because the failure is a `TypeError` rather than a deprecation warning, a v3 call site fails at import or startup rather than degrading silently. Grep for both keywords before running a migrated server.

Do not build cycles or make a child depend on its parent's request path.

### External Servers and Packages

Proxy an external source, then mount it:

```python
from fastmcp import FastMCP
from fastmcp.server import create_proxy

mcp = FastMCP("Orchestrator")
mcp.mount(create_proxy("https://api.example.com/mcp"), namespace="api")
mcp.mount(create_proxy("./my_server.py"), namespace="local")
```

Use MCP configuration for npm or uvx packages:

```python
github = {
    "mcpServers": {
        "default": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
        }
    }
}

sqlite = {
    "mcpServers": {
        "default": {
            "command": "uvx",
            "args": ["mcp-server-sqlite", "--db", "data.db"],
        }
    }
}

mcp.mount(create_proxy(github), namespace="github")
mcp.mount(create_proxy(sqlite), namespace="db")
```

Installed `NpxStdioTransport` and `UvxStdioTransport` are typed alternatives. Pin packages and constrain side effects; `npx -y` and `uvx` may resolve and execute remote code.

### Namespacing

For `namespace="api"`:

| Child identity         | Parent identity   |
| ---------------------- | ----------------- |
| Tool `my_tool`         | `api_my_tool`     |
| Prompt `my_prompt`     | `api_my_prompt`   |
| Resource `data://info` | `data://api/info` |
| Template `data://{id}` | `data://api/{id}` |

Namespacing is a transform. Choose a stable public namespace and test URI schemes, paths, templates, versions, and any `tool_names` mappings.

### Dynamic Catalogs, Filters, and Routes

Because mounting is live, later child registrations appear in the parent. Parent visibility and tag filters apply recursively:

```python
production = FastMCP("Production")
production.mount(api_server, namespace="api")
production.enable(tags={"production"}, only=True)
```

Custom HTTP routes registered with `@child.custom_route()` are forwarded to the parent's ASGI app: a child route at `/healthz` appears verbatim in `parent.http_app().routes` after `parent.mount(child, namespace="c")`. The route path is **not** namespaced — a component namespace does not create an HTTP route prefix. Two children exposing `/healthz` therefore collide silently. Confirm path conflicts, authentication, schema inclusion, and parent HTTP controls; custom routes bypass the server `AuthProvider` in the parent exactly as they do in the child.

### Collisions and Performance

The live Composition page says the most recently mounted server wins same-namespace conflicts. The pinned release's exact lookup preserves the first equal-version provider. Treat this as a documentation/implementation difference and prohibit duplicate identities.

Parent listing includes every mounted provider. Remote HTTP, slow initialization, deep mount graphs, cache misses, and dynamic discovery add latency and failure modes. Keep composition shallow, use proxy caching only when staleness is acceptable, and measure the complete client-visible path.

## Custom Providers

### When to Build One

Build a custom provider only when `LocalProvider`, `FileSystemProvider`, `FastMCPProvider`, `ProxyProvider`, Skills providers, OpenAPI/FastAPI providers, or composition cannot own the source. Appropriate cases include:

- database-defined tools;
- API-backed resources;
- YAML or JSON configuration-defined components;
- tenant- or permission-dependent catalogs;
- runtime plugin registries.

Providers answer where components come from. Middleware handles request-specific logging, limits, authentication, and interception. Prefer a provider for the candidate catalog, then authorization, visibility, or middleware for each request. Visibility is not authorization.

### Implement the Interface

Subclass `Provider` and override only protected source methods you need:

- `_list_tools()` and optionally `_get_tool(name, version=None)`;
- `_list_resources()` and optionally `_get_resource(uri, version=None)`;
- `_list_resource_templates()` and optionally `_get_resource_template(uri, version=None)`;
- `_list_prompts()` and optionally `_get_prompt(name, version=None)`;
- `lifespan()` when the provider owns startup and shutdown dependencies.

Public `list_*` and `get_*` methods apply transforms; do not bypass them. Base list methods return empty sequences, and base get methods search list results. Override `_get_*` only for a more correct or efficient direct lookup.

Providers return ready `Tool`, `Resource`, `ResourceTemplate`, and `Prompt` objects. Components execute themselves through `run()`, `read()`, or `render()`; the provider only sources them. Construct function-backed objects with `Tool.from_function`, `Resource.from_function`, and `Prompt.from_function` where appropriate.

### Minimal Provider

```python
from collections.abc import Callable, Sequence

from fastmcp import FastMCP
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool

class DictProvider(Provider):
    def __init__(self, tools: dict[str, Callable[..., object]]) -> None:
        super().__init__()
        self._tools = [
            Tool.from_function(function, name=name)
            for name, function in tools.items()
        ]

    async def _list_tools(self) -> Sequence[Tool]:
        return self._tools

def add(a: int, b: int) -> int:
    return a + b

server = FastMCP("Calculator", providers=[DictProvider({"add": add})])
```

Register providers in `FastMCP(..., providers=[...])` or with `server.add_provider(provider, namespace="...")`.

### Lifecycle and API-Backed Components

Use async lifespan for shared database or HTTP clients. The HTTP dependency is **`httpx2`** — `import httpx` raises `ModuleNotFoundError` on the pinned release, and the built-in `OpenAPIProvider` types its client parameter as `httpx2.AsyncClient`:

```python
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx2

from fastmcp.resources import Resource
from fastmcp.server.providers import Provider

class ApiResourceProvider(Provider):
    def __init__(self, base_url: str, token: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.token = token
        self.client: httpx2.AsyncClient | None = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        client = httpx2.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.client = client
        try:
            yield
        finally:
            self.client = None
            await client.aclose()

    async def _list_resources(self) -> Sequence[Resource]:
        if self.client is None:
            raise RuntimeError("provider is not started")
        response = await self.client.get("/resources")
        response.raise_for_status()
        return [self._make_resource(item) for item in response.json()["items"]]

    def _make_resource(self, data: dict[str, Any]) -> Resource:
        resource_id = str(data["id"])

        async def read_content() -> str:
            if self.client is None:
                raise RuntimeError("provider is not started")
            response = await self.client.get(
                f"/resources/{resource_id}/content"
            )
            response.raise_for_status()
            return response.text

        return Resource.from_function(
            read_content,
            uri=f"api://resources/{resource_id}",
            name=str(data["name"]),
            description=str(data.get("description", "")),
            mime_type=str(data.get("mime_type", "text/plain")),
        )
```

Capture stable values such as each resource ID when constructing closures. Bound list size and latency, validate upstream schemas, and do not expose credentials, exception detail, or unauthorized component names.

### Custom Provider Checklist

- Prove no built-in provider or composition route fits.
- Define deterministic list/get identity and version behavior for every component type.
- Preserve schemas, annotations, tags, icons, metadata, URIs, and authorization checks.
- Define refresh, cache invalidation, tenant isolation, and unavailable-source behavior.
- Keep expensive I/O out of every catalog call or make it bounded and explicit.
- Decide whether provider failure should degrade with a warning or fail the path.
- Close connections under normal shutdown, startup failure, cancellation, and transport loss.
- Test list/get consistency, collisions, refresh, auth filtering, execution, and shutdown through `Client`.

## Verification Matrix

Exercise every surface through a FastMCP `Client` or the owner's configured integration harness:

| Surface | Required proof |
| --- | --- |
| All providers | Final tools/resources/templates/prompts; names, URIs, versions, schemas, tags, annotations, metadata, visibility, authorization, duplicate behavior |
| Local | Decorator and direct registration, explicit duplicate mode, removal, standalone sharing, visibility |
| Filesystem | Trusted-root resolution, package and non-package imports, discovery rules, required catalog, reload add/change/remove, import failure |
| Proxy | Lazy initialize, unavailable/auth-failed upstream, each enabled callback, transport bridge, cache TTL/refresh, concurrent isolation or intentional reuse, transport loss |
| Skills | Root precedence, main/manifest/supporting URIs, both disclosure modes, reload, path containment, manifest/hash validation, overwrite policy |
| Mounting | Live child changes, lifespan and middleware, namespace mapping, tag filtering, forwarded custom routes and their un-namespaced paths, collisions, complete-path latency |
| v3 migration | No `prefix=`/`as_proxy=` call sites remain, no `import_server`/`as_proxy` attribute access, no bare `import httpx`, no `task=` on a resource or prompt |
| Custom | List/get consistency, version resolution, cache/refresh, provider error strategy, upstream validation, teardown |

## Source Coverage Checklist

This mapping prevents a future refresh from silently dropping a requested live-guide section:

| Source | Covered sections |
| --- | --- |
| Providers overview | Provider definition, composition/proxy/dynamic rationale, built-ins, transforms, order/precedence, when providers matter |
| Local provider | Decorators, direct methods, removal, every duplicate mode, visibility, standalone reuse |
| Filesystem provider | Motivation, quick start, every decorator inventory, layouts, discovery/package rules, reload, import errors, example CLI |
| Proxy provider | Use cases, creation, lazy connection, transport bridge, isolated/shared sessions, feature forwarding/disable, config/multi-server, prefixing, mirroring, latency, cache, stateless reuse, low-level classes, mounting |
| Skills provider | Resource rationale, structure/frontmatter, all URIs/manifest fields, single/directory/vendor providers, root precedence, both disclosure modes, reload, list/download/sync/manifest utilities and overwrite behavior |
| Composition | Local/external/package mounts, namespaces, live updates, recursive tags, performance, custom routes, conflicts |
| Custom providers | When to build, provider versus middleware, protected interface, returned components, registration, simple provider, lifespan, API-backed source pattern |
