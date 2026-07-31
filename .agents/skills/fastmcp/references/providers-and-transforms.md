# Providers, Transforms, Visibility, and Tool Catalogs

## Scope and Source Contract

Use this reference for provider composition, transform pipelines, tool reshaping, namespaces, visibility, large-catalog search, resource/prompt tool bridges, code mode, and tool contract fingerprints.

This playbook is reconciled against the repository's installed FastMCP runtime with its project-selected extras. For the pinned release and how to resolve it, see [Version and source routing](version-and-source-routing.md). These live FastMCP guides are covered here:

| Guide | Covered here |
| --- | --- |
| [Transforms overview](https://gofastmcp.com/servers/transforms/transforms) | Pipeline model, provider/server levels, ordering, and custom transforms |
| [Tool transformation](https://gofastmcp.com/servers/transforms/tool-transformation) | Deferred/immediate transforms, argument changes, forwarding, and factories |
| [Tool search](https://gofastmcp.com/servers/transforms/tool-search) | Regex/BM25 search, configuration, proxy calls, auth, and visibility |
| [Namespace](https://gofastmcp.com/servers/transforms/namespace) | Collision-free names and resource/template URI rewriting |
| [Component visibility](https://gofastmcp.com/servers/visibility) | Global, provider, and session rules, selectors, ordering, and notifications |
| [Resources as tools](https://gofastmcp.com/servers/transforms/resources-as-tools) | Tool-only resource discovery and reads |
| [Prompts as tools](https://gofastmcp.com/servers/transforms/prompts-as-tools) | Tool-only prompt discovery and rendering |
| [Code Mode](https://gofastmcp.com/servers/transforms/code-mode) | Sandboxed code execution over a discovered tool catalog |
| [Tool fingerprinting](https://gofastmcp.com/servers/tool-fingerprinting) | Stable application-owned schema fingerprints and CI drift detection |

Live guides are design guidance. The installed source and matching release tag own exact imports, signatures, return types, and behavior. The current guides contain several details that differ from installed behavior; apply these installed facts:

| Surface | Installed behavior |
| --- | --- |
| `FastMCP.mount()` | Returns `None`; it does not return a provider that can receive later transforms. Construct a `FastMCPProvider` explicitly when provider-specific post-composition transforms are required. |
| Standalone `@tool` | Returns the original callable with FastMCP metadata attached — unconditionally. `decorator_mode` no longer exists, so there is no alternative mode. `Tool.from_tool()` accepts that callable and creates the transformed tool. |
| `FastMCP.add_tool_transformation()` | **Removed.** Register a `ToolTransform` through `FastMCP.add_transform(transform)` instead. |
| `Tool.from_tool(serializer=...)` | **Removed.** Return a `ToolResult` from a `transform_fn` to control the result representation. |
| Deferred tool options | `ToolTransformConfig` and `ArgTransformConfig` support a smaller option set than `Tool.from_tool()` and `ArgTransform`; use the matrices below. |
| Visibility selector composition | Different selector categories are intersected. Values inside one set are alternatives. Use separate calls when union behavior is required. |
| Component keys | Exact keys include a version delimiter, such as `tool:greet@` or `tool:greet@1.0`; prefer the component's actual `.key` over hand-built examples. |
| Global visibility notifications | `enable()` and `disable()` append transforms and return `Self`, but do not themselves notify already-connected clients. Session visibility methods do emit list-change notifications. |
| Search arguments and annotations | Regex search exposes `pattern`; BM25 exposes `query`. Their generated search tools carry no annotations, so add an outer annotation transform when a client contract requires those hints. |

Two import-level changes affect nearly every example on this page:

- **`mcp.types` no longer exists.** Protocol models moved to the standalone `mcp_types` package; `import mcp.types` raises `ModuleNotFoundError`. Every `from mcp.types import ...` becomes `from mcp_types import ...`.
- **Annotation and schema fields are snake_case.** `ToolAnnotations` declares `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`, and `title`. The camelCase spellings still validate as Pydantic aliases, but `model_dump()` emits snake_case; write snake_case in source.

Refresh this comparison whenever the installed pin changes.

## Provider-First Composition

A provider owns a component catalog. Use the narrowest built-in provider that matches the source. The installed `fastmcp.server.providers` exports:

| Provider | Source |
| --- | --- |
| `Provider` | Base abstraction for a custom catalog |
| `LocalProvider` | An explicitly registered local component group |
| `FastMCPProvider` | Dynamic composition of another FastMCP server |
| `ProxyProvider` | Forwarding a remote MCP server |
| `AggregateProvider` | Combining several providers behind one catalog |
| `OpenAPIProvider` | Generated components from an OpenAPI/FastAPI description |
| `FileSystemProvider` | Filesystem-backed content |
| `SkillProvider`, `SkillsProvider`, `SkillsDirectoryProvider`, `ClaudeSkillsProvider` | Skill-backed catalogs |

Inspect `fastmcp.server.providers` in the installed environment before choosing a class. Preserve component schemas, annotations, URI identities, tags, metadata, authentication, authorization, and child lifecycle behavior through composition. Do not wrap every child component in a pass-through function.

For a composition decision:

1. Identify the owner of registration and lifecycle.
2. Choose local composition, remote proxying, or generated components.
3. Define public names, namespaces, and duplicate behavior before clients bind.
4. Apply only transforms required by the consumer contract.
5. Inspect the final catalog and invoke representative components through a FastMCP client.

Keep providers cohesive by domain, trust boundary, or lifecycle. Avoid a global provider that becomes a second application router. For mounting, proxying, and multi-server assembly, see [Providers and composition](providers-and-composition.md).

## Transform Pipeline

Transforms modify components between a provider and a client:

```text
Provider -> provider transforms -> server transforms -> client
```

Provider transforms affect one source and run first. Server transforms affect the aggregate catalog and see provider-transformed identities. Transforms stack in insertion order: the first is innermost, and each later transform sees the earlier result. A direct lookup reverses name/URI mappings while traversing back toward the provider.

Use provider-level transforms for source-specific adaptation and server-level transforms for an intentional policy or namespace over every provider.

```python
from fastmcp import FastMCP
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.transforms import Namespace, ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig

child = FastMCP("Child")

@child.tool
def verbose_name(value: str) -> str:
    return value

provider = FastMCPProvider(child)
provider.add_transform(Namespace("api"))
provider.add_transform(
    ToolTransform(
        {
            # Namespace runs first, so ToolTransform sees api_verbose_name.
            "api_verbose_name": ToolTransformConfig(name="short_name"),
        }
    )
)

main = FastMCP("Main", providers=[provider])
```

When only a mount namespace is needed, use `main.mount(child, namespace="api")`. Because `mount()` returns `None`, create and register a `FastMCPProvider` explicitly when later provider-specific transforms are needed; do not assign the result of `mount()`.

### Installed Transform Inventory

Every built-in transform lives under `fastmcp/server/transforms/`:

| Module | Public name | Purpose |
| --- | --- | --- |
| `namespace.py` | `Namespace` | Prefix tool/prompt names and rewrite resource/template URIs |
| `tool_transform.py` | `ToolTransform` | Deferred tool reshaping from a name-to-config mapping |
| `visibility.py` | `Visibility` | Enable/disable components by selector |
| `version_filter.py` | `VersionFilter` | Filter a catalog by component version range |
| `resources_as_tools.py` | `ResourcesAsTools` | Bridge resources to tools for clients lacking resource support |
| `prompts_as_tools.py` | `PromptsAsTools` | Bridge prompts to tools for clients lacking prompt support |
| `catalog.py` | `CatalogTransform` | Base class for transforms that need the real auth-filtered catalog |
| `search/base.py` | `BaseSearchTransform`, `serialize_tools_for_output_json`, `serialize_tools_for_output_markdown` | Shared search machinery and result serializers |
| `search/regex.py` | `RegexSearchTransform` | Case-insensitive `re.search` over searchable text |
| `search/bm25.py` | `BM25SearchTransform` | Lazily built BM25 Okapi relevance ranking |

`fastmcp.server.transforms.__all__` re-exports `Namespace`, `PromptsAsTools`, `ResourcesAsTools`, `ToolTransform`, `Transform`, `VersionFilter`, `VersionSpec`, `Visibility`, and `is_enabled`. `CatalogTransform` and the search classes are imported from their own modules — `fastmcp.server.transforms.catalog` and `fastmcp.server.transforms.search`.

### List and Direct-Lookup Methods

`Transform` is the base contract. List methods are sequence-to-sequence transformations. Direct lookups use a middleware-style `call_next` so a public identity can map back to its source.

| Component | List method | Direct lookup |
| --- | --- | --- |
| Tool | `list_tools(tools)` | `get_tool(name, call_next, *, version=...)` |
| Resource | `list_resources(resources)` | `get_resource(uri, call_next, *, version=...)` |
| Resource template | `list_resource_templates(templates)` | `get_resource_template(uri, call_next, *, version=...)` |
| Prompt | `list_prompts(prompts)` | `get_prompt(name, call_next, *, version=...)` |

Override only the methods the custom transform owns. Preserve version routing, return `None` for identities outside the transform, and make list and direct lookup behavior agree.

```python
from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec

class TagFilter(Transform):
    def __init__(self, required_tags: set[str]) -> None:
        self.required_tags = required_tags

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if tool.tags & self.required_tags]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool is None or not tool.tags & self.required_tags:
            return None
        return tool
```

### When the Transform Needs the Real Catalog

Subclass `CatalogTransform` instead of `Transform` when the transform must **read** the real catalog to produce its output — the pattern behind search and code mode. Subclasses override `transform_tools()`, `transform_resources()`, `transform_prompts()`, or `transform_resource_templates()`, and read the auth-filtered catalog through `get_tool_catalog()` and friends.

The base class owns the `list_*()` methods and sets a bypass flag while a `get_*_catalog()` call is in flight, so the subclass hook never sees a re-entrant call from its own catalog read. Do not reimplement that guard, and do not override `list_*()` on a `CatalogTransform` subclass.

Prefer a built-in transform. For a custom transform, test listing, direct lookup, version selection, collisions, transform order, and calls after the public identity is rewritten.

## Tool Transformation

Choose the transformation time based on ownership:

- Use deferred `ToolTransform` for mounted, proxied, or provider-owned tools whose source cannot or should not be edited.
- Use immediate `Tool.from_tool()` when the callable/tool is locally available and the transformed tool should be registered as a concrete object.

### Deferred Transformation

`ToolTransform(transforms: dict[str, ToolTransformConfig])` maps each source-visible tool name to a config. The mapping key must match the identity at that point in the transform chain. Register it with `FastMCP.add_transform(transform)`; `add_tool_transformation()` no longer exists.

```python
from fastmcp import FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import (
    ArgTransformConfig,
    ToolTransformConfig,
)

mcp = FastMCP("Search")

@mcp.tool
def internal_search(q: str, limit: int = 10) -> list[str]:
    return []

mcp.add_transform(
    ToolTransform(
        {
            "internal_search": ToolTransformConfig(
                name="find_items",
                description="Find items matching a query.",
                arguments={
                    "q": ArgTransformConfig(
                        name="query",
                        description="Terms to search for.",
                    )
                },
            )
        }
    )
)
```

Duplicate transformed target names are rejected. Inspect the final schema and call the transformed name; listing success alone does not prove argument mapping.

### Immediate Transformation

`Tool.from_tool()` accepts a FastMCP `Tool` or a callable and returns a `TransformedTool`. The standalone `fastmcp.tools.tool` decorator attaches tool metadata to a callable and returns the original function, so it feeds this pattern directly.

```python
from fastmcp import FastMCP
from fastmcp.tools import Tool, tool
from fastmcp.tools.tool_transform import ArgTransform

@tool
def search(q: str, limit: int = 10) -> list[str]:
    return []

public_search = Tool.from_tool(
    search,
    name="find_items",
    description="Find items matching a query.",
    transform_args={
        "q": ArgTransform(name="query", description="Terms to search for."),
    },
)

mcp = FastMCP("Search")
mcp.add_tool(public_search)
```

Because the decorator returns the plain function rather than a component object, retrieve a registered component with `await mcp.get_tool(name)` when you need the `Tool` itself.

### Installed Option Matrix

Tool-level options:

| Option | Deferred `ToolTransformConfig` | Immediate `Tool.from_tool()` | Use |
| --- | --- | --- | --- |
| `name` | Yes | Yes | Public tool name |
| `version` | Yes | No | Deferred version rewrite |
| `title` | Yes | Yes | Human-readable title |
| `description` | Yes | Yes | Model-facing behavior and selection guidance |
| `tags` | Yes | Yes | Grouping, visibility, and discovery |
| `annotations` | No | Yes | MCP behavioral hints |
| `output_schema` | No | Yes | Protocol-facing structured result contract |
| `meta` | Yes | Yes | Bounded application metadata |
| `enabled` | Yes | No | Visibility mark at the transform position |
| `arguments` / `transform_args` | Yes | Yes | Argument-level transforms |
| `transform_fn` | No | Yes | Custom execution adapter |
| `serializer` | **Removed** | **Removed** | Return a `ToolResult` from `transform_fn` instead |

Argument options:

| Option | Deferred `ArgTransformConfig` | Immediate `ArgTransform` | Rule |
| --- | --- | --- | --- |
| `name` | Yes | Yes | Rename the client-visible argument |
| `description` | Yes | Yes | Explain the public argument |
| `default` | JSON scalar or `None` | Any supported value | Make the underlying argument optional or inject a hidden constant |
| `default_factory` | No | Yes | Requires `hide=True`; evaluate per call |
| `hide` | Yes | Yes | Remove from the client schema and supply a configured value |
| `required` | Only `True` | Only `True` | Make an originally optional argument required |
| `type` | No | Yes | Change the public validation/schema type |
| `examples` | Yes | Yes | Add schema examples |

Visible defaults must be JSON-schema representable. Never hide a credential in a static tool definition and mistake schema invisibility for secret handling. Resolve credentials and authenticated identity through server-owned auth or dependency injection. Use hidden constants/factories for non-secret adaptation such as a deployment name or generated request ID.

### Execution Adapters

An immediate `transform_fn` can validate transformed inputs, modify results, or add deliberately owned logic. Call `await forward(**public_arguments)` to map the transformed names back to the source. Call `await forward_raw(**source_args)` only when deliberately bypassing argument-name mapping. Both return a `ToolResult`; preserve errors, content, structured content, and metadata.

Returning a `ToolResult` from `transform_fn` is also the replacement for the removed `serializer=` option — build the exact content and structured content the client should receive rather than delegating to a serializer callable.

```python
from fastmcp.tools import Tool, tool
from fastmcp.tools.tool_transform import ArgTransform, forward

@tool
def divide(a: float, b: float) -> float:
    return a / b

async def safe_divide(numerator: float, denominator: float):
    if denominator == 0:
        raise ValueError("denominator must be non-zero")
    return await forward(numerator=numerator, denominator=denominator)

safe = Tool.from_tool(
    divide,
    name="safe_divide",
    transform_fn=safe_divide,
    transform_args={
        "a": ArgTransform(name="numerator"),
        "b": ArgTransform(name="denominator"),
    },
)
```

A factory may produce environment- or principal-specific transformed tools, but bind authenticated identity at the correct connection/request boundary. Do not capture one user's identity into a shared global server catalog.

## Namespace Transform

`Namespace(prefix)` prevents collisions across composed catalogs:

| Component | Source        | `Namespace("api")` |
| --------- | ------------- | ------------------ |
| Tool      | `my_tool`     | `api_my_tool`      |
| Prompt    | `my_prompt`   | `api_my_prompt`    |
| Resource  | `data://info` | `data://api/info`  |
| Template  | `data://{id}` | `data://api/{id}`  |

Prefer `mount(child, namespace="api")` for ordinary mounted-server composition. Apply `Namespace("api")` directly when a provider or the entire server transform chain needs the prefix.

Choose the namespace before clients depend on names and URIs. Confirm URI scheme/authority behavior, transformed tool and prompt calls, template matching, and collisions after every composition change. A namespace changes public identity; it is not cosmetic.

## Component Visibility

Visibility removes disabled tools/resources/prompts/templates from discovery and direct access, but it is not authorization. Clients may cache an earlier catalog, and a visibility rule does not establish principal identity or grant a business permission. Apply component auth and application authorization to every protected operation.

### Global and Provider Rules

Use `enable()` and `disable()` on a server or provider. Both return `Self` for chaining. Server rules apply after provider rules and therefore have final say. Later matching transforms override earlier ones.

```python
from fastmcp import FastMCP

mcp = FastMCP("Server")

@mcp.tool(tags={"admin", "write"})
def reset_system() -> str:
    return "reset"

@mcp.tool(tags={"public", "read"})
def status() -> str:
    return "ok"

mcp.disable(tags={"admin"})
mcp.enable(names={"reset_system"})  # Later rule re-enables this one tool.
```

`enable(..., only=True)` switches to an effective allowlist by appending a disable-all rule followed by the requested enable rule. A later `enable(..., only=True)` establishes a new effective allowlist. `only` exists on `enable()` only; `disable()` has no such keyword.

```python
mcp.enable(tags={"safe"}, only=True)
mcp.disable(names={"safe_but_unavailable"})
```

### Selectors and Exact Semantics

| Selector | Meaning |
| --- | --- |
| `names` | Match a component name; resources/templates also match URI/template text |
| `keys` | Exact component keys, including the `@version` or trailing `@` portion |
| `version` | A `VersionSpec`; unversioned components do not match |
| `tags` | Match when the component has at least one tag from the set |
| `components` | Limit to `tool`, `resource`, `template`, and/or `prompt` |
| `only` | `enable()` only; create an allowlist |
| `match_all` | Direct `Visibility` and session methods; match every component |

All non-empty selector categories in one rule must match. Values inside a set are alternatives. For union behavior, issue separate calls:

```python
# Disable this exact component OR anything tagged dangerous.
mcp.disable(keys={actual_component_key})
mcp.disable(tags={"dangerous"})
```

Do not copy shortened key examples from a floating guide. Read keys from the installed component catalog; the installed release uses forms such as `tool:greet@`, `tool:greet@1.0`, and `resource:data://config@`.

The direct transform is useful for an explicit chain:

```python
from fastmcp.server.transforms import Visibility

mcp.add_transform(Visibility(False, tags={"internal"}))
mcp.add_transform(Visibility(True, names={"safe_internal_tool"}))
```

`Visibility(enabled, *, names=None, keys=None, version=None, tags=None, components=None, match_all=False)` takes the enabled flag positionally. An empty rule matches nothing; use `match_all=True` deliberately instead of depending on an empty selector.

### Per-Session Visibility

Use Context session rules when clients need different views. The rules layer on top of global transforms, accumulate for the current MCP session, and follow the same later-rule-wins behavior.

```python
from fastmcp import FastMCP
from fastmcp.server.context import Context

mcp = FastMCP("Progressive catalog")

@mcp.tool(tags={"namespace:finance"})
def analyze_portfolio(symbols: list[str]) -> str:
    return ", ".join(symbols)

@mcp.tool
async def activate_finance(ctx: Context) -> str:
    await ctx.enable_components(
        tags={"namespace:finance"},
        components={"tool"},
    )
    return "finance tools activated"

@mcp.tool
async def reset_catalog(ctx: Context) -> str:
    await ctx.reset_visibility()
    return "catalog reset"

mcp.disable(tags={"namespace:finance"})
```

Session methods are:

- `await ctx.enable_components(...)`;
- `await ctx.disable_components(...)`;
- `await ctx.reset_visibility()`.

They accept `names`, `keys`, `version`, `tags`, `components`, and `match_all`. Session changes send list-change notifications only to the affected session. Supplying `components={"tool"}` limits notification fan-out to tool changes; omitting it can notify tool, resource, and prompt catalogs. Reset returns to the global default.

Global and provider `enable()` / `disable()` merely add transforms and do not themselves send notifications to existing clients. Build the catalog before serving, or implement an owner-tested refresh/reconnect contract when global runtime mutation is unavoidable.

#### Session Visibility Is a Silent No-Op on the Modern Era

Session rules are persisted through `save_visibility_rules`, which writes them to **session state** under `_visibility_rules`; `get_session_transforms` reads them back and returns `[]` when no session is resolvable. Session state keys are session-prefixed, and the `2026-07-28` era mints a **new session id per request** — so the write and the later read never agree.

`Client(mode=...)` defaults to `"auto"`, which negotiates that era against a FastMCP server. **The example above therefore does nothing by default.** Verified with the exact code in this section:

| Client mode | Before | After `activate_finance` | After `reset_catalog` |
| --- | --- | --- | --- |
| `"auto"` (default) | `activate_finance`, `reset_catalog` | **unchanged** | unchanged |
| `"legacy"` | `activate_finance`, `reset_catalog` | `activate_finance`, `analyze_portfolio`, `reset_catalog` | back to two |

Nothing raises and nothing warns — `ctx.enable_components()` returns normally and the list-change notifications are still sent. Do not build progressive-disclosure catalogs on session visibility unless the deployment pins a handshake era. Use global/provider visibility, a search transform, or per-principal server composition instead. See [Protocol eras and sessions](protocol-eras-and-sessions.md) and the session-state limits in [Interactivity and observability](interactivity-and-observability.md).

For debugging, a `Visibility` transform marks matching component copies under the internal `meta.fastmcp._internal.visibility` field, and final provider filtering honors the last matching mark. Treat that metadata path as an implementation detail, not an application-owned contract or a field to mutate directly.

## Tool Search

Use search when sending the entire catalog would waste context or degrade tool selection. Search transforms live in `fastmcp/server/transforms/search/`:

| Module | Provides |
| --- | --- |
| `base.py` | `BaseSearchTransform` (a `CatalogTransform` subclass owning the synthetic search/call tools, pinning, and auth-filtered catalog access, with a single `_search` hook for subclasses) plus `serialize_tools_for_output_json` and `serialize_tools_for_output_markdown` |
| `regex.py` | `RegexSearchTransform` — case-insensitive `re.search` |
| `bm25.py` | `BM25SearchTransform` — lazily built BM25 Okapi index |

A search transform replaces `list_tools()` with:

- a search tool (`search_tools` by default) that returns complete MCP tool definitions, including input schemas;
- a proxy (`call_tool` by default) that executes a discovered tool;
- any configured pinned tools.

Source tools remain directly callable even though they are absent from the listing — `get_tool()` delegates unknown names downstream. Search governs discovery, not access. Tool names, descriptions, parameter names, and parameter descriptions form the searchable text.

### Regex and BM25

```python
from fastmcp import FastMCP
from fastmcp.server.transforms.search import (
    BM25SearchTransform,
    RegexSearchTransform,
)

regex_server = FastMCP(
    "Regex catalog",
    transforms=[RegexSearchTransform(max_results=10)],
)

bm25_server = FastMCP(
    "Natural-language catalog",
    transforms=[BM25SearchTransform(max_results=5)],
)
```

`RegexSearchTransform` performs case-insensitive `re.search`. Results retain catalog order and stop at `max_results`. An invalid regular expression returns an empty result instead of raising.

`BM25SearchTransform` builds an in-memory BM25 Okapi index lazily, ranks by relevance, and returns the highest-scoring results. A hash of all searchable text triggers rebuilds when names, descriptions, or parameter text changes.

Choose regex for deterministic targeted patterns and easy debugging. Choose BM25 for natural-language queries and relevance ranking.

### Search Options

Both transforms take the same keyword-only options:

| Option | Default | Purpose |
| --- | --- | --- |
| `max_results` | `5` | Bound definitions returned per search |
| `always_visible` | `None` | Pin named tools beside the two synthetic tools; pinned tools are excluded from search results |
| `search_tool_name` | `"search_tools"` | Avoid a collision or fit the client vocabulary |
| `call_tool_name` | `"call_tool"` | Avoid a collision or fit the client vocabulary |
| `search_result_serializer` | MCP-style JSON serializer | Sync or async `Callable[[Sequence[Tool]], Any]`; keep enough schema data for a valid subsequent call |

`serialize_tools_for_output_json` and `serialize_tools_for_output_markdown` are exported for reuse when a client prefers one shape; both are importable from `fastmcp.server.transforms.search`.

Reserve the synthetic names or configure alternatives. The call proxy accepts `name` and optional `arguments`, runs the normal server tool pipeline, and rejects recursive calls to either synthetic tool. A discovered source tool may also be called directly through the MCP client.

Call Regex search with `{"pattern": "..."}` and BM25 search with `{"query": "..."}`. Renaming the generated search tool does not rename its argument. Assert the generated input schema instead of assuming both search strategies share one request shape.

Neither generated search tool declares behavioral annotations. When an owning client requires `search_tools` to be explicitly read-only and non-destructive, add a later transform that annotates only the search tool. Do not apply those hints to `call_tool`: the proxy inherits the side-effect profile of whichever source tool it invokes.

```python
from collections.abc import Sequence

from mcp_types import ToolAnnotations

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.server.transforms.search import RegexSearchTransform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec

SEARCH_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

class AnnotateSearchTool(Transform):
    def __init__(self, name: str = "search_tools") -> None:
        self.name = name

    def annotate(self, tool: Tool) -> Tool:
        if tool.name != self.name:
            return tool
        return Tool.from_tool(tool, annotations=SEARCH_ANNOTATIONS)

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self.annotate(tool) for tool in tools]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self.annotate(tool)

mcp = FastMCP("Search catalog")
mcp.add_transform(RegexSearchTransform())
mcp.add_transform(AnnotateSearchTool())  # Outer transform sees search_tools.
```

Search resolves the visible/auth-filtered catalog at search time. Visibility middleware and component auth therefore affect results, including session visibility changes. Still authorize the eventual source call independently; search results are neither permission grants nor durable catalog snapshots.

Test listing, search, result serialization, direct and proxy calls, invalid regex, no results, pinned tools, synthetic-name collisions, visibility changes, unauthorized tools, and catalog/index refresh.

## Resources as Tools

Use `ResourcesAsTools` only for a client that lacks native resource support. Its parameter is typed `Provider`, but pass the owning `FastMCP` server rather than a raw child provider, because the generated tools then route back through server middleware, auth, visibility, and rate limits. To expose only a subset, compose a dedicated FastMCP server that owns that subset and apply the bridge there.

```python
from fastmcp import FastMCP
from fastmcp.server.transforms import ResourcesAsTools

mcp = FastMCP("Resource bridge")

@mcp.resource("config://app")
def app_config() -> str:
    return '{"version":"1"}'

@mcp.resource("user://{user_id}/profile")
def user_profile(user_id: str) -> str:
    return f'{{"user_id":"{user_id}"}}'

mcp.add_transform(ResourcesAsTools(mcp))
```

The transform adds fixed tool names:

- `list_resources`: JSON metadata for static resources and templates;
- `read_resource(uri)`: read a concrete URI, including a URI matched from a template.

Static entries have `uri`, `name`, `description`, and `mime_type`. Template entries have `uri_template`, `name`, and `description`. Clients distinguish the two URI fields and fill template placeholders before calling `read_resource`.

Both generated tools carry `read_only_hint=True`. Single text content is returned as text; single binary content is base64; multiple contents are a JSON list of `content` plus `mime_type`, with each binary item base64-encoded. Bound sizes and validate content even though the bridge is read-only.

Reserve `list_resources` and `read_resource` or prove collision behavior. Test static and template reads, percent-decoding/path safety, binary and multi-part content, auth rejection, visibility, rate limits, and parity with native resource protocol calls.

## Prompts as Tools

Use `PromptsAsTools` only for a client that lacks native prompt support. It follows the same ownership rule: pass the owning `FastMCP` server so calls route through its middleware, auth, and visibility. Isolate a subset in a dedicated server rather than passing a raw provider.

```python
from fastmcp import FastMCP
from fastmcp.server.transforms import PromptsAsTools

mcp = FastMCP("Prompt bridge")

@mcp.prompt
def analyze_code(code: str, language: str = "python") -> str:
    return f"Analyze this {language} code:\n{code}"

mcp.add_transform(PromptsAsTools(mcp))
```

The transform adds fixed tool names:

- `list_prompts`: JSON prompt metadata with `name`, `description`, and arguments containing `name`, optional `description`, and `required`;
- `get_prompt(name, arguments=None)`: render one prompt and return JSON with an ordered `messages` array.

Unlike the resource bridge, these two generated tools carry **no** annotations. Add an outer annotation transform if a client contract requires read-only hints.

Arguments may be omitted for an argument-free prompt or passed as an empty mapping. Each rendered message has a `user` or `assistant` role. Text content is returned as text in the JSON message; non-text MCP content, such as an embedded resource, is preserved as its JSON protocol object rather than returned as raw binary.

Reserve `list_prompts` and `get_prompt` or prove collision behavior. Prompts are model guidance, not an authorization or validation boundary. Test required and optional arguments, multiple messages, embedded content, errors, auth, visibility, and parity with native prompt protocol calls.

## Code Mode

**`fastmcp.experimental.*` carries no stability guarantee.** Symbols there may move, change signature, or disappear between releases without a deprecation cycle. Do not build a production contract on this module without pinning an exact version and re-verifying after every bump.

Code Mode collapses the entire tool catalog into discovery meta-tools plus one sandboxed `execute` tool, letting a model compose operations in Python instead of issuing many round trips.

```python
from fastmcp import FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode

mcp = FastMCP("Code mode catalog")
mcp.add_transform(CodeMode())
```

`CodeMode` is a `CatalogTransform` subclass. Its constructor is keyword-only:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `sandbox_provider` | `MontySandboxProvider()` | Executes the generated code. Must satisfy the `SandboxProvider` protocol (`async def run(code, *, inputs=None, external_functions=None)`). |
| `discovery_tools` | `[Search(), GetSchemas()]` | Factories producing the synthetic discovery tools. Verified default tool names: `search` and `get_schema`. |
| `execute_tool_name` | `"execute"` | Name of the sandboxed execution tool. |
| `execute_description` | `None` | Override the execute tool's model-facing description. |
| `max_tool_calls` | `50` | Cap on `call_tool` invocations from within one execution. |

Available discovery-tool factories in the same module are `ListTools`, `Search`, `GetSchemas`, and `GetTags`; each takes a `name` and a `default_detail` level (`brief`, `detailed`, `full`; `GetTags` supports `brief` and `full`). `GetToolCatalog`, `SearchFn`, and `DiscoveryToolFactory` are the type aliases for building your own. Discovery tool names must be unique and must not collide with `execute_tool_name` — `CodeMode` raises `ValueError` on either.

### Sandbox Obligations

The `SandboxProvider` protocol docstring is explicit: the `code` parameter contains **untrusted, LLM-generated Python**, and implementations must never run it with plain `exec()`.

`MontySandboxProvider` is backed by `pydantic-monty`. It imports that package lazily inside `run()`, so constructing the provider succeeds without it and raises only on first execution:

```text
ImportError: CodeMode requires pydantic-monty for the Monty sandbox provider.
Install it with `fastmcp[code-mode]` or pass a custom SandboxProvider.
```

**The `code-mode` extra is not installed in this repository** (zero extras are declared), and `import pydantic_monty` raises `ModuleNotFoundError`. Constructor defaults and tool-name behavior above were verified by execution; **no claim about sandbox execution semantics on this page has been executed here.** Treat runtime sandbox behavior as upstream-documented until the extra is added to the owning manifest. See [Version and source routing](version-and-source-routing.md).

When `limits` is omitted, `MontySandboxProvider` applies a conservative baseline of `max_duration_secs=30` and `max_memory=100_000_000` (100 MB) rather than running unbounded. Passing `limits=None` explicitly opts out of all limits; passing a dict overrides. Supported keys are `max_duration_secs`, `max_allocations`, `max_memory`, `max_recursion_depth`, and `gc_interval`.

Treat Code Mode as an execution boundary:

- install and pin the required extra and sandbox provider;
- expose only operations safe for composition;
- default to read-only capabilities;
- bound CPU, memory, time, output, imports, and network — do not rely on the baseline defaults alone;
- keep secrets and host paths outside the sandbox;
- preserve per-operation authorization and audit requirements — `max_tool_calls` is a loop backstop, not an authorization control;
- test attempted escape, excessive output, timeout, and cancellation.

Do not enable local arbitrary Python execution as a shortcut. Use an installed sandbox provider or a deliberately isolated deployment.

## Tool Fingerprinting

FastMCP intentionally does not define one universal contract hash. The owning application decides whether a contract includes only input shape or also descriptions, outputs, annotations, metadata, tags, or version. Record that policy beside the stored manifest.

Use two stable inputs:

- `tool.key` for canonical type, name, and version identity;
- `tool.to_mcp_tool()` for the protocol-facing representation clients receive.

The key distinguishes versions and component kinds, so a versioned tool cannot collide with another version or with a resource that happens to use the same human-readable identifier. With the same inclusion policy, canonicalization, identity, and schema, the fingerprint remains stable across process restarts.

Canonicalize selected fields with JSON aliases, omitted `None` values, sorted keys, and compact separators, then hash with SHA-256.

```python
import hashlib
import json

from fastmcp import FastMCP
from fastmcp.tools import Tool

def fingerprint(tool: Tool) -> str:
    public = tool.to_mcp_tool().model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    payload = {
        "key": tool.key,
        "inputSchema": public["inputSchema"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

async def manifest(server: FastMCP) -> dict[str, str]:
    return {tool.key: fingerprint(tool) for tool in await server.list_tools()}

def changed_keys(
    baseline: dict[str, str],
    current: dict[str, str],
) -> list[str]:
    return sorted(
        key
        for key in baseline.keys() | current.keys()
        if baseline.get(key) != current.get(key)
    )
```

**`by_alias=True` is load-bearing here.** Even though model fields are snake_case, the wire aliases are still camelCase, so this dump yields `inputSchema` and `outputSchema` — the keys a client actually sees. Dropping `by_alias` yields `input_schema` / `output_schema` and silently changes every stored fingerprint. Pick one and record it in the inclusion policy.

Common inclusion policies:

| Field | Include when |
| --- | --- |
| `inputSchema` | Always for an invocation contract |
| `description` | Tool selection/routing depends on documentation |
| `outputSchema` | Consumers validate structured results |
| `annotations` | Read-only/destructive/idempotent hints influence routing or confirmation |
| `_meta` | Bounded metadata drives an owned policy |

Generate the manifest after the transforms whose public behavior is part of the contract. If visibility/auth creates multiple public catalogs, generate a manifest through a representative FastMCP client for each supported role or session instead of pretending one process-level list is universal.

Store manifests as CI artifacts or reviewed baselines. Compare the union of keys so additions, removals, version changes, and schema changes are detected. A fingerprint signals drift; it does not decide compatibility, authorize a deployment, or replace a semantic review.

## Visibility and Contract Evolution

For contract evolution:

- keep stable identities for compatible changes;
- introduce a new version or identity for incompatible schemas or semantics;
- avoid runtime flags that make one name mean unrelated things;
- validate old and new consumers during migration;
- remove compatibility surfaces only under the owner's deprecation policy.

Visibility and search can tailor discovery during migration, but neither is a substitute for versioned contracts or authorization.

For component `version=`, `VersionFilter`, discovery metadata, explicit selection, removal, and migration, use [Versioning](versioning.md).

## Custom Provider Checklist

Before subclassing an installed provider abstraction:

- prove no built-in provider or composition mechanism fits;
- define deterministic list/get behavior for every supported component type;
- define cache invalidation and reload semantics;
- preserve component keys, versions, schemas, annotations, and metadata;
- handle duplicates and unavailable sources explicitly;
- avoid expensive I/O during every catalog call;
- test refresh, collision, lookup, middleware, errors, and shutdown.

Consult the live [providers](https://gofastmcp.com/servers/providers/overview), [composition](https://gofastmcp.com/servers/composition), and [Python SDK](https://gofastmcp.com/python-sdk) references, then verify against the installed release.

## Verification Matrix

Exercise the final surface through `fastmcp.Client` or the owner's configured integration harness:

- snapshot public names, URIs/templates, schemas, descriptions, tags, annotations, metadata, versions, and component keys;
- call transformed names with valid and invalid transformed arguments;
- prove provider/server transform order and reverse direct lookup;
- prove namespace behavior for tools, prompts, resources, and templates;
- test visibility listing and direct access at provider, server, and two independent sessions, pinning `Client(mode=...)` so a session-visibility test cannot silently pass against the sessionless era;
- test search discovery plus direct/proxy invocation, auth rejection, index refresh, and serializer output;
- test both native and bridged resource/prompt consumers, asserting the annotation difference between the two bridges;
- assert snake_case annotation fields on any tool whose hints are part of the contract;
- compare fingerprint manifests, fixing the `by_alias` choice, and classify every detected change;
- for Code Mode, install the extra first, then test sandbox limits, `max_tool_calls`, discovery-tool naming collisions, and escape attempts;
- run the owning repository's lint, type, and test commands and inspect the final diff.
