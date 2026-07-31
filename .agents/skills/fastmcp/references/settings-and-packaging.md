# Settings and Packaging

## Purpose

This reference owns three things a FastMCP task keeps needing and no other reference carries: the complete `Settings` surface with its defaults, the distribution and extras layout that decides what is importable, and the small SDK modules (`fastmcp.types`, `fastmcp.decorators`, `fastmcp.mcp_config`) that have no natural home elsewhere.

Read [Version and source routing](version-and-source-routing.md) first for the pinned release and the meta-package split. Everything here is verified against installed source at that pin.

**This repository declares zero extras.** The install is `fastmcp-slim[client,server]` only, so optional integration packages and the Docket runtime are absent. Some core configuration modules and guarded entry points remain importable, but the optional operation is unavailable until its extra is declared in `tools/soleaux/pyproject.toml`.

## Settings

`fastmcp.settings` is a module-level `Settings` instance (a `pydantic_settings.BaseSettings`) constructed at import.

```python
model_config = SettingsConfigDict(
    env_prefix="FASTMCP_",
    env_file=ENV_FILE,
    extra="ignore",
    env_nested_delimiter="__",
    nested_model_default_partial_update=True,
    validate_assignment=True,
)
```

Consequences worth knowing:

- **`env_prefix="FASTMCP_"`** — every field maps to `FASTMCP_<FIELD_NAME>` uppercased.
- **`env_nested_delimiter="__"`** — double underscore descends into a nested model.
- **`extra="ignore"`** — a misspelled `FASTMCP_*` variable is silently discarded. There is no startup error for a typo; assert the parsed value instead.
- **`validate_assignment=True`** — runtime mutation such as `fastmcp.settings.port = "abc"` is validated and raises, so programmatic overrides fail loudly.

### `ENV_FILE` is resolved once, at import

```python
ENV_FILE = os.getenv("FASTMCP_ENV_FILE", ".env")
```

This is module-level in `fastmcp/settings.py`. `FASTMCP_ENV_FILE` must be set **before** `import fastmcp`; setting it afterwards has no effect, because the path was already baked into `model_config`. The default `.env` is resolved relative to the **process working directory**, not the module or project root — a server launched from a different directory reads a different file, or none.

### Reading and writing nested values

`get_setting` and `set_setting` are **instance methods on `Settings`**, not module-level functions:

```python
Settings.get_setting(self, attr: str) -> Any
Settings.set_setting(self, attr: str, value: Any) -> None
```

Both split `attr` on `__` and walk into nested settings, mirroring the env-var syntax; a missing intermediate raises `AttributeError: Setting <name> does not exist.` For a flat field they are equivalent to plain attribute access.

### All 33 fields

| Field | Type | Default |
| --- | --- | --- |
| `home` | `Path` | platform user-data dir for `fastmcp` |
| `test_mode` | `bool` | `False` |
| `log_enabled` | `bool` | `True` |
| `log_level` | `Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"]` | `"INFO"` |
| `enable_rich_logging` | `bool` | `True` |
| `enable_rich_tracebacks` | `bool` | `True` |
| `enable_telemetry` | `bool` | `True` |
| `deprecation_warnings` | `bool` | `True` |
| `mcp_camelcase_compat` | `bool` | `True` |
| `client_raise_first_exceptiongroup_error` | `bool` | `True` |
| `client_init_timeout` | `float \| None` | `None` |
| `client_disconnect_timeout` | `float` | `5.0` |
| `transport` | `Literal["stdio","http","sse","streamable-http"]` | `"stdio"` |
| `host` | `str` | `"127.0.0.1"` |
| `port` | `int` | `8000` |
| `sse_path` | `str` | `"/sse"` |
| `message_path` | `str` | `"/messages/"` |
| `streamable_http_path` | `str` | `"/mcp"` |
| `debug` | `bool` | `False` |
| `mask_error_details` | `bool` | `False` |
| `client_log_level` | `Literal["debug",…,"emergency"] \| None` | `None` |
| `strict_input_validation` | `bool` | `False` |
| `ssrf_trust_proxy` | `bool` | `False` |
| `server_dependencies` | `list[str]` | `[]` (factory) |
| `json_response` | `bool` | `False` |
| `stateless_http` | `bool` | `False` |
| `http_host_origin_protection` | `bool \| Literal["auto"]` | `False` |
| `http_allowed_hosts` | `list[str] \| None` | `None` |
| `http_allowed_origins` | `list[str] \| None` | `None` |
| `http_session_idle_timeout` | `float \| None` | `None` |
| `mounted_components_raise_on_load_error` | `bool` | `False` |
| `show_server_banner` | `bool` | `True` |
| `check_for_updates` | `Literal["stable","prerelease","off"]` | `"stable"` |

`log_level` has a `before` validator that normalizes case, so `FASTMCP_LOG_LEVEL=debug` is accepted.

### Defaults that carry risk

These are the defaults to review before a deployment. Each is safe for local development and questionable in production.

| Setting | Default | Why it matters |
| --- | --- | --- |
| `mask_error_details` | `False` | **All** error details from tool, resource, and prompt functions are returned to clients, not only explicitly raised `ToolError` / `ResourceError` / `PromptError`. Exception text, and whatever it interpolates, reaches the caller. Set `True` in production. |
| `strict_input_validation` | `False` | Inputs are **coerced** rather than rejected — `"10"` becomes `10` for an integer field. Chosen for client compatibility; it also means a tool can receive a value its schema would have refused. |
| `mcp_camelcase_compat` | `True` | Warn-once shims keep legacy camelCase reads (`tool.inputSchema`, `result.isError`) working after the SDK rename. A **migration aid, not a contract** — stale code keeps running instead of failing. Verify under `FASTMCP_MCP_CAMELCASE_COMPAT=false`. |
| `check_for_updates` | `"stable"` | Enables an outbound PyPI request on the banner path. See [Update-check egress](#update-check-egress). |
| `enable_telemetry` | `True` | Enables OpenTelemetry **API** instrumentation. See below — this is not an egress default. |
| `ssrf_trust_proxy` | `False` | The safe default. FastMCP resolves OAuth-metadata and JWKS hostnames itself and refuses private, loopback, link-local, or reserved IPs. Setting `True` shifts SSRF trust to a proxy and ignores `NO_PROXY`; with no proxy configured the fetch raises `SSRFError` rather than going direct with the blocklist off. |
| `client_raise_first_exceptiongroup_error` | `True` | Clients raise the **first** error from an anyio `ExceptionGroup` instead of the group. Good for debugging, and it **masks the other errors**. |

`enable_telemetry=True` is frequently misread as a phone-home. It is not. FastMCP uses only the OpenTelemetry **API**, so span creation is a no-op unless an OTel SDK and exporter are configured in the host process. Setting it `False` returns a non-attaching pass-through tracer that creates no FastMCP spans and leaves the surrounding OTel context untouched even when an SDK is present — which is the reason to set it: suppressing FastMCP's spans in someone else's trace, not preventing egress. Verified at `fastmcp/telemetry.py`.

### Update-check egress

This is the one default that reaches the network. Relevant for sandboxed, air-gapped, and egress-audited deployments.

| Property          | Value                                             |
| ----------------- | ------------------------------------------------- |
| Endpoint          | `https://pypi.org/pypi/fastmcp/json`              |
| Request timeout   | `2.0` seconds                                     |
| Cache TTL         | `43200` seconds — `60 * 60 * 12`, twelve hours    |
| Cache file        | `settings.home / "version_cache.json"`            |
| Pre-release cache | `settings.home / "version_cache_prerelease.json"` |

`settings.home` is the platformdirs user-data directory for `fastmcp` (on macOS, `~/Library/Application Support/fastmcp`). Cache read and write failures are swallowed; a failed fetch falls back to a stale cache entry rather than erroring.

**When it fires.** `check_for_newer_version()` has exactly two call sites: the `fastmcp version` command, and `log_server_banner()` in `fastmcp/utilities/cli.py`. The banner runs from `FastMCP.run_async` when `show_server_banner` is `True` (the default), so **starting a server normally does make the request**. Importing `fastmcp`, constructing a `FastMCP`, and running an in-memory `Client` session make no outbound request at all — verified by patching `httpx2.get` and observing zero calls.

Three ways to suppress it, in decreasing order of scope:

```bash
FASTMCP_CHECK_FOR_UPDATES=off     # no request on any path
FASTMCP_SHOW_SERVER_BANNER=false  # no banner, so no request on server start
fastmcp run server.py --no-banner # per-invocation; overrides the setting
```

Verified with an isolated `settings.home` to force a cache miss: `"stable"` and `"prerelease"` each produce one request to that URL with a 2.0s timeout, and `"off"` produces none.

## Packaging

`fastmcp` is a **meta-package**. It ships metadata and no Python modules; the code lives in `fastmcp-slim`. Its base requirement is `fastmcp-slim[client,server]` pinned to the same exact version. See [Version and source routing](version-and-source-routing.md) for how that affects file listings and version pinning.

### Extras matrix

The two distributions declare **different** extra sets.

| Extra | On `fastmcp` | On `fastmcp-slim` | Resolves to |
| --- | --- | --- | --- |
| `client` | — | yes | `fastmcp-slim[client]` (installed here) |
| `server` | — | yes | `fastmcp-slim[server]` (installed here) |
| `mcp` | — | yes | `fastmcp-slim[mcp]` |
| `anthropic` | yes | yes | `fastmcp-slim[anthropic]` |
| `apps` | yes | yes | `fastmcp-slim[apps]` |
| `azure` | yes | yes | `fastmcp-slim[azure]` |
| `code-mode` | yes | yes | `fastmcp-slim[code-mode]` |
| `gemini` | yes | yes | `fastmcp-slim[gemini]` |
| `openai` | yes | yes | `fastmcp-slim[openai]` |
| `tasks` | yes | **no** | **`fastmcp-tasks`** (a separate distribution) |

Two asymmetries in that table are easy to miss and both bite in practice.

`client`, `server`, and `mcp` are declared only on `fastmcp-slim`; request them from that distribution, not from `fastmcp`.

`tasks` is the sole extra that does **not** resolve to `fastmcp-slim`. `fastmcp-slim` declares no `tasks` extra at all — its `Provides-Extra` list is `anthropic, apps, azure, client, code-mode, gemini, mcp, openai, server`. `fastmcp[tasks]` pulls the separate **`fastmcp-tasks`** distribution at the same exact version. Requesting `fastmcp-slim[tasks]` therefore resolves to nothing rather than failing loudly. See [Background tasks](tasks.md).

### Dependency floors

Unconditional requirements of `fastmcp-slim`:

| Package             | Constraint   |
| ------------------- | ------------ |
| `mcp-types`         | `>=2.0.0,<3` |
| `platformdirs`      | `>=4.0.0`    |
| `pydantic-settings` | `>=2.0.0`    |
| `pydantic[email]`   | `>=2.12.0`   |
| `python-dotenv`     | `>=1.1.0`    |
| `rich`              | `>=13.9.4`   |
| `typing-extensions` | `>=4.0.0`    |

The two extras installed here:

| Extra | Requirements |
| --- | --- |
| `client` | `authlib>=1.6.11`, `exceptiongroup>=1.2.2`, `httpx2>=2.5.0`, `mcp>=2.0.0,<3`, `opentelemetry-api>=1.28.0`, `py-key-value-aio[filetree,keyring,memory]>=0.4.4,<0.5.0`, `starlette>=1.0.1` |
| `server` | everything in `client` plus `cyclopts>=4.0.0`, `griffelib>=2.0.0`, `joserfc>=1.1.0`, `jsonref>=1.1.0`, `jsonschema-path>=0.3.4`, `openapi-pydantic>=0.5.1`, `packaging>=24.0`, `pyperclip>=1.9.0`, `python-multipart>=0.0.26`, `pyyaml>=6.0,<7.0`, `uncalled-for>=0.2.0`, `uvicorn>=0.35`, `watchfiles>=1.0.0`, `websockets>=15.0.1` |

The `mcp` extra is the `client` set exactly, minus `authlib` and `py-key-value-aio`.

Uninstalled extras, for completeness: `anthropic` → `anthropic>=0.48.0`; `apps` → `prefab-ui>=0.18.0`; `azure` → `azure-identity>=1.16.0`, `pyjwt>=2.12.0`; `code-mode` → `pydantic-monty==0.0.17`; `gemini` → `google-genai>=1.18.0`, `jsonref>=1.1.0`; `openai` → `openai>=1.102.0`.

Pins to note. `mcp` and `mcp-types` moved from exact beta pins to the **stable GA range** (`>=2.0.0,<3`) — the MCP SDK now moves independently of FastMCP within the 2.x line, so a lockfile refresh can pull a new stable SDK without a FastMCP release. `code-mode`'s `pydantic-monty==0.0.17` is exact. `py-key-value-aio` carries an upper bound (`<0.5.0`).

**`pydocket` is not installed, and its floor cannot be read from anything installed here.** It appears in neither `uv.lock`, `tools/soleaux/pyproject.toml`, nor `fastmcp-slim` metadata — because the task engine lives in `fastmcp-tasks`, which this repository does not install. Resolve its floor from `fastmcp-tasks` metadata if the extra is ever declared; do not carry a remembered version number here, and do not add an independent direct pin.

`uncalled-for` (the dependency-resolution system behind `Depends`) ships with the **`server`** extra, not `tasks`, which is why [Dependency injection](dependency-injection.md) works here without the tasks extra.

## Small Module Surfaces

### `fastmcp` top level

```python
fastmcp.__all__ == ["Client", "Context", "FastMCP", "FastMCPApp", "FastMCPDeprecationWarning", "settings"]
```

Six names. Anything else is reached through a submodule path.

### `fastmcp.types`

```python
fastmcp.types.__all__ == ["Textarea"]
```

One name. `Textarea` is an annotation, not a class:

```python
Textarea = Annotated[str, Field(json_schema_extra={"format": "textarea"})]
```

Annotate a string parameter with it to hint a multi-line input widget. It carries no validation — it only adds `"format": "textarea"` to the published JSON schema, and a client is free to ignore it.

### `fastmcp.decorators`

Three public names.

| Name | Signature | Role |
| --- | --- | --- |
| `HasFastMCPMeta` | runtime-checkable `Protocol` | Structural test for a function carrying FastMCP decorator metadata |
| `get_fastmcp_meta` | `(fn: Any) -> Any \| None` | Read that metadata off a function; `None` when absent |
| `resolve_task_config` | `(task: bool \| TaskConfig \| None) -> bool \| TaskConfig` | Normalize the `task=` argument to a concrete value |

`HasFastMCPMeta` is decorated `@runtime_checkable`, so `isinstance(fn, HasFastMCPMeta)` works. `resolve_task_config` is the resolution helper behind `@mcp.tool(task=...)`; `TaskConfig` itself lives in `fastmcp.utilities.tasks`.

### `fastmcp.mcp_config`

Models for the `mcpServers` config format that MCP hosts share. The module declares no `__all__`; these are the public names.

| Name | Kind | Shape |
| --- | --- | --- |
| `StdioMCPServer` | model | `command`, `args`, `env`, `transport`, `type`, `cwd`, `timeout`, `keep_alive`, `description`, `icon`, `authentication` |
| `RemoteMCPServer` | model | `url`, `transport`, `headers`, `auth`, `sse_read_timeout`, `timeout`, `description`, `icon`, `authentication` |
| `TransformingStdioMCPServer` | model | `StdioMCPServer` fields plus `tools`, `include_tags`, `exclude_tags` |
| `TransformingRemoteMCPServer` | model | `RemoteMCPServer` fields plus `tools`, `include_tags`, `exclude_tags` |
| `MCPConfig` | model | `mcpServers` — accepts transforming and canonical servers |
| `CanonicalMCPConfig` | model | `mcpServers` — canonical servers only |
| `MCPServerTypes` | alias | `TransformingStdioMCPServer \| TransformingRemoteMCPServer \| StdioMCPServer \| RemoteMCPServer` |
| `CanonicalMCPServerTypes` | alias | `StdioMCPServer \| RemoteMCPServer` |
| `TransformingMCPServerTypes` | alias | `TransformingStdioMCPServer \| TransformingRemoteMCPServer` |

The canonical/transforming split is the useful distinction: **canonical** models are what a host config file can hold, and **transforming** models add FastMCP's client-side filtering (`tools`, `include_tags`, `exclude_tags`) on top. Write a canonical model when producing a config another tool will read.

Two functions:

```python
update_config_file(
    file_path: Path,
    server_name: str,
    server_config: CanonicalMCPServerTypes,
) -> None

infer_transport_type_from_url(url: str | AnyUrl) -> Literal["http", "sse"]
```

`update_config_file` takes a **canonical** server only — a transforming model is not writable to a host config. `infer_transport_type_from_url` returns `"http"` or `"sse"`; it never returns `"stdio"`, since a URL implies a network transport.

Note `RemoteMCPServer.sse_read_timeout` still exists as a **config field**, even though `StreamableHttpTransport(sse_read_timeout=)` was removed as a constructor argument. The config schema and the transport constructor are not the same surface.

## Implementation Rules

- Set `FASTMCP_ENV_FILE` before `import fastmcp`, or not at all.
- Do not rely on `.env` discovery for a server whose working directory you do not control; pass configuration explicitly.
- Assert parsed settings values in tests. `extra="ignore"` means a misspelled env var is silently dropped.
- Set `mask_error_details=True` for any server whose errors reach an untrusted client.
- Decide `strict_input_validation` deliberately; the default trades schema fidelity for client compatibility.
- Set `check_for_updates="off"` or `show_server_banner=False` for sandboxed, air-gapped, or egress-audited deployments.
- Do not disable `enable_telemetry` to prevent egress; it does not cause any. Disable it to stay out of a host's traces.
- Keep `ssrf_trust_proxy=False` unless a trusted corporate proxy is the mandated egress path.
- Resolve extras from distribution metadata, never from the base version. Never document a floor for a package you cannot introspect.
- Emit `CanonicalMCPConfig` / canonical server models when writing a config another host reads.

## Verification

```bash
python -c "from fastmcp.settings import Settings; print(len(Settings.model_fields))"
python -c "import fastmcp; print(fastmcp.settings.model_dump())"
python -c "from importlib.metadata import distribution as d; print(*(d('fastmcp-slim').requires or []), sep='\n')"
python -c "from importlib.metadata import distribution as d; print(d('fastmcp').metadata.get_all('Provides-Extra'))"
```

Cover:

- every non-default setting your deployment relies on, read back after import;
- a misspelled `FASTMCP_*` variable being ignored rather than raising;
- `FASTMCP_ENV_FILE` set after import having no effect;
- `validate_assignment` rejecting an invalid runtime override;
- masked and unmasked error paths, asserting no exception text leaks when masked;
- coerced versus rejected inputs under both `strict_input_validation` values;
- the server starting with no outbound request under `check_for_updates="off"`;
- an import-only and in-memory-client path making no outbound request;
- the declared extras matching what is importable, with the uninstalled ones raising `ModuleNotFoundError`;
- `update_config_file` round-tripping through a canonical model and rejecting a transforming one;
- `infer_transport_type_from_url` on both `http(s)://` and SSE endpoints.
