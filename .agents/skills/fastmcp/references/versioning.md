# Versioning

## Source and Version Contract

Use this reference for the complete workflow represented by the live [FastMCP versioning guide](https://gofastmcp.com/servers/versioning), verified 2026-07-14. Component versioning is available in FastMCP 3.0+ and is distinct from the descriptive `FastMCP(version=...)` server version.

Confirm decorator, provider, transform, server lookup, and client signatures against installed source. See [Version and source routing](version-and-source-routing.md) for the pinned baseline. The versioning surface described here was re-verified intact on the pinned release: `VersionFilter`, `VersionSpec`, the four `FastMCP.get_*` lookups, the four `LocalProvider.remove_*` methods, and the client `version=` arguments all match this document.

## Component Identity and Default Selection

Add `version=` to a tool, resource, resource template, or prompt. FastMCP stores component versions as strings and groups implementations by component identity:

- tool or prompt name;
- resource or resource-template URI identity.

When no version is requested or filtered, clients see and execute the highest version for each identity. Use component versioning for multiple intentionally coexisting contracts, not ordinary compatible implementation changes.

```python
from fastmcp import FastMCP

mcp = FastMCP("service")

@mcp.tool(version="1.0")
def calculate(x: int, y: int) -> int:
    return x + y

@mcp.tool(version="2.0")
def calculate(x: int, y: int, z: int = 0) -> int:
    return x + y + z
```

Both versions are registered; ordinary listing and invocation select 2.0.

For one identity, version every implementation or none. Mixing a versioned and unversioned component with the same identity raises at registration. This invariant applies to tools, resources, templates, and prompts.

## Build Versioned API Surfaces

Define components once on a shared provider, then attach the provider to servers with different `VersionFilter` ranges.

```python
from fastmcp import FastMCP
from fastmcp.server.providers import LocalProvider
from fastmcp.server.transforms import VersionFilter

components = LocalProvider()

@components.tool(version="1.0")
def calculate(x: int, y: int) -> int:
    return x + y

@components.tool(version="2.0")
def calculate(x: int, y: int, z: int = 0) -> int:
    return x + y + z

v1 = FastMCP("API v1", providers=[components])
v1.add_transform(VersionFilter(version_lt="2.0"))

v2 = FastMCP("API v2", providers=[components])
v2.add_transform(VersionFilter(version_gte="2.0"))
```

Installed `VersionFilter` options are:

| Option                     | Meaning                                    |
| -------------------------- | ------------------------------------------ |
| `version_gte`              | Inclusive lower bound                      |
| `version_lt`               | Exclusive upper bound                      |
| `include_unversioned=True` | Keep unversioned components in the surface |

Use one or both bounds. `[2.0, 3.0)` is `version_gte="2.0", version_lt="3.0"`. Set `include_unversioned=False` only when the versioned surface must exclude shared unversioned components.

Do not put incompatible v1 and v2 behavior behind a runtime flag on one schema. Build separately testable surfaces and preserve one clear contract per selected version.

### Mounted Servers

A `VersionFilter` on a parent applies to components from mounted children. Namespacing occurs independently of version comparison, so a parent can enforce one range across its hierarchy. Test the final namespaced catalog because transform order remains observable.

## Version Discovery

List results for a versioned component expose FastMCP metadata:

- `meta.fastmcp.version`: the currently selected version;
- `meta.fastmcp.versions`: all registered versions from highest to lowest.

Unversioned components omit these fields. Clients may use this metadata for diagnostics, negotiated fallbacks, or developer tooling, but the server must still enforce explicit version selection and authorization.

## Request a Specific Version

Installed FastMCP client methods accept `version=` for tools, resources, and prompts:

```python
async with Client(mcp) as client:
    v1_result = await client.call_tool(
        "calculate",
        {"x": 1, "y": 2},
        version="1.0",
    )
    legacy_resource = await client.read_resource("config://app", version="1.0")
    legacy_prompt = await client.get_prompt(
        "summarize",
        {"text": "..."},
        version="1.0",
    )
```

The client sends the selection through request `_meta.fastmcp.version`. For a generic MCP client, place this metadata on the MCP request params, not inside the component's business arguments. Component implementations do not receive the `_meta` selection field as an input argument.

An unavailable explicit version must not silently fall back. FastMCP client execution reports not found. For direct server-side inspection, installed `get_tool`, `get_resource`, and `get_prompt` return an optional component, so check for `None`; do not rely on the live guide's generalized `NotFoundError` statement for those direct methods.

## Version Comparison

FastMCP selects and sorts versions as follows:

- PEP 440-compatible forms use semantic comparison, so `1.10` is greater than `1.9` and final releases sort after their prereleases;
- other forms fall back to lexicographic string comparison, which works for zero-padded ISO dates and deliberately sortable labels;
- a leading `v` is stripped for comparison, so `v1.0` and `1.0` compare equally.

Choose one version scheme per component family. Avoid ambiguous labels, inconsistent zero padding, or two text forms that compare equal but appear distinct. Test the exact order used by the installed release before publishing a non-PEP-440 scheme.

## Server-Side Retrieval

`FastMCP.get_tool`, `get_resource`, `get_resource_template`, and `get_prompt` accept a `VersionSpec` through `version=`. Without one they select the highest compatible version after providers/transforms; with one they select a matching version or return `None`. All four are async and return `Tool | None`, `Resource | None`, `ResourceTemplate | None`, and `Prompt | None` respectively.

```python
from fastmcp.utilities.versions import VersionSpec

legacy = await mcp.get_tool(
    "calculate",
    version=VersionSpec(eq="1.0"),
)
if legacy is None:
    raise RuntimeError("calculate 1.0 is not installed")
```

Do not pass a bare string to these direct server methods; their installed contract is `VersionSpec | None`, where `VersionSpec(gte=None, lt=None, eq=None)`. This differs from the FastMCP client methods, whose public `version=` argument is a `str | None` serialized into request metadata, and from `LocalProvider.remove_*`, whose `version=` is also a plain `str | None`. The provider `remove_*` methods are synchronous; the server `get_*` methods are async.

Use these methods for server-owned inspection or controlled migration checks. Do not bypass normal client execution when validating schemas, authorization, middleware, or transport behavior.

## Remove Versions

The local provider owns removal:

```python
# Remove one version and retain the others.
mcp.local_provider.remove_tool("calculate", version="1.0")

# Remove every version of this identity.
mcp.local_provider.remove_tool("calculate")
```

Use `remove_resource(uri, version=...)`, `remove_template(uri_template, version=...)`, and `remove_prompt(name, version=...)` for the other component families. All four methods remove every version when `version` is omitted. Catalog mutation during an active request can notify connected clients, but clients may cache prior schemas; remove an externally used version only under the owning deprecation and rollout policy.

## Migration Workflow

1. Assign an initial version to the existing implementation; do not leave the old identity unversioned.
2. Register the new implementation under the same identity with a higher version.
3. Build old/new surfaces with `VersionFilter` when separate client cohorts need stable contracts.
4. Assert discovery metadata, schemas, annotations, auth, and results for both versions.
5. Call each version explicitly through `Client` on every configured consumer path.
6. Observe adoption and honor the owner's deprecation window.
7. Remove only the obsolete version through the local provider, then re-check the catalog and direct invocation.

Keep stable identity for compatible changes that do not require simultaneous public contracts. Use a new identity or major version when semantics or schemas are incompatible. Version provider-normalized contracts rather than leaking incompatible provider wire payloads.

## Verification

Cover:

- highest-version default for tools, resources, templates, and prompts;
- registration failure when one identity mixes versioned and unversioned definitions;
- `VersionFilter` lower/upper bounds and `include_unversioned` behavior;
- mounted-server namespacing plus parent filtering;
- correct `meta.fastmcp.version` and ordered `versions` discovery;
- FastMCP client and generic MCP `_meta` selection, including resource versions;
- explicit unknown-version failure without fallback;
- direct server lookup returning the installed optional shape;
- PEP 440, prerelease, ISO-date, custom-string, and `v`-prefix ordering;
- one-version and all-version removal plus catalog notifications;
- old and new consumer compatibility throughout the migration window;
- authorization, middleware, caching, and task behavior for every retained version.
