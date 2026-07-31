# Apps and Integrations

## Source and Version Contract

Use this reference for MCP Apps, Prefab, `FastMCPApp`, generative UI, custom HTML, built-in app providers, app development, and app protocol debugging.

This guidance is an operational playbook, not a verbatim mirror of the documentation. Re-open the live page when wording or current Prefab behavior matters, and resolve exact imports, signatures, metadata, and defaults against the installed FastMCP release. See [Version and source routing](version-and-source-routing.md) for the pinned baseline.

> **The `apps` extra is not installed in this repository.** The manifest declares zero FastMCP extras, so `prefab-ui` is absent. The split between what that does and does not cost you is sharper than "Apps is unavailable", and it decides which statements below you may trust here:
>
> **Present and locally verified** — these ship in the `server` extra, which _is_ installed. `fastmcp.apps` imports, exporting `UI_EXTENSION_ID`, `UI_MIME_TYPE`, `AppConfig`, `FastMCPApp`, `PrefabAppConfig`, `ResourceCSP`, `ResourcePermissions`, `app_config_to_meta_dict`, and `resolve_ui_mime_type`. The `AppConfig`/`PrefabAppConfig`, `ResourceCSP`, and `ResourcePermissions` field tables, the wire serialization, `UI_EXTENSION_ID`, `UI_MIME_TYPE`, `FastMCPApp` and its decorator inventories, the hashed-tool routing, and the `AuthMiddleware` interaction were all confirmed by introspection. The `fastmcp dev apps` CLI command is registered and its `--help` is introspectable without the extra.
>
> **Absent and not re-verifiable** — every Prefab-backed provider module raises on import: `import fastmcp.apps.form` gives `ImportError: FormInput requires prefab-ui. Install with: pip install 'fastmcp[apps]'`, and `generative`, `approval`, `choice`, and `file_upload` fail the same way. Prefab component behavior, renderer semantics, and the provider defect notes are **observed in an audited Apps-capable pairing, not re-confirmed here**; each such claim says so at its point of use. Running `fastmcp dev apps` also needs the extra even though the command introspects without it.
>
> Enabling Apps is a manifest change, not a runtime toggle. This split applies to the whole reference and is not repeated per section.

| Guide | Coverage in this reference |
| --- | --- |
| [Apps overview](https://gofastmcp.com/apps/overview) | Installation, host model, four implementation paths, next-step routing |
| [Quickstart](https://gofastmcp.com/apps/quickstart) | `app=True`, `PrefabApp`, preview, client-side state, `SetState`, `Rx`, `STATE`, `If` |
| [FastMCPApp](https://gofastmcp.com/apps/fastmcp-app) | Entry points, backend tools, actions, results, forms, loading state, composition, standalone running |
| [Interactive Tools](https://gofastmcp.com/apps/prefab) | Tables, charts, dashboards, composition, reactivity, CSP, model-readable results |
| [Generative UI](https://gofastmcp.com/apps/generative) | Provider registration, streaming lifecycle, component search, data injection, configuration, sandbox limits |
| [Custom HTML Apps](https://gofastmcp.com/apps/low-level) | `AppConfig`, UI resources, Apps JS SDK, CSP, permissions, host capability fallback |
| [Approval provider](https://gofastmcp.com/apps/providers/approval) | Registered tool, constructor and call options, advisory-gate semantics |
| [Choice provider](https://gofastmcp.com/apps/providers/choice) | Registered tool, constructor and call options, advisory-choice semantics |
| [File Upload provider](https://gofastmcp.com/apps/providers/file-upload) | Tool surface, limits, transport scoping, custom persistence contract |
| [Form Input provider](https://gofastmcp.com/apps/providers/form) | Pydantic mapping, callbacks, constructor and call options, multiple forms |
| [Development](https://gofastmcp.com/apps/development) | `fastmcp dev apps`, ports, reload, picker, AppBridge, inspector, multiple tools |
| [Architecture](https://gofastmcp.com/apps/architecture) | Registration, serialization, routing, renderer, `postMessage`, dev proxy |

Projects must declare an Apps-capable FastMCP extra, pin the Prefab version they exercise, and update their lockfile. The pinned release's `apps` extra resolves through `fastmcp-slim[apps]`, which requires **`prefab-ui>=0.18.0` with no upper bound** — verified from distribution metadata, which is readable without installing the extra. An unbounded floor means a lockfile refresh can silently move Prefab across a breaking renderer change, so pin the exact version your browser tests exercise. Form defaults need `prefab-ui>=0.19.1`.

Confirm all four gates before selecting an Apps API:

1. The installed FastMCP version provides the feature.
2. The environment installs `fastmcp[apps]` or `fastmcp-slim[server,apps]` and a compatible pinned `prefab-ui`.
3. The target host implements the MCP Apps extension, `io.modelcontextprotocol/ui`.
4. Every non-App client receives a useful text or structured fallback.

## Select the Narrowest App Pattern

| Need | Pattern | Use |
| --- | --- | --- |
| A chart, table, dashboard, or browser-local interaction | Interactive Tool | `@mcp.tool(app=True)` returning a Prefab component or `PrefabApp` |
| A UI that calls server tools for forms, search, CRUD, or backend work | `FastMCPApp` | `@app.ui()` entry points plus `@app.tool()` backend operations |
| A model that writes a data-specific UI at runtime | Generative UI | Add `GenerativeUI()` as a provider |
| A map, 3D viewer, video surface, custom framework, or unsupported Prefab behavior | Custom HTML | Link a tool to a `ui://` resource with `AppConfig` |
| A standard approval, discrete choice, upload, or Pydantic form | Built-in provider | Add `Approval`, `Choice`, `FileUpload`, or `FormInput` |

Start with an Interactive Tool. Move to `FastMCPApp` only when the browser must call server operations, to Generative UI only when runtime UI generation is the requirement, and to custom HTML only when Prefab cannot express the surface.

Use the [runnable Apps examples](https://gofastmcp.com/apps/examples) after choosing a pattern. The FastMCPApp contact-manager guide also points to `examples/apps/contacts/contacts_server.py`. Use the [Prefab component catalog](https://prefab.prefect.io/docs/components), [state guide](https://prefab.prefect.io/docs/concepts/state), and [expression guide](https://prefab.prefect.io/docs/concepts/expressions) for the installed library's 100+ components and component-specific fields. For custom HTML, use the [MCP Apps extension](https://modelcontextprotocol.io/docs/extensions/apps) and [`@modelcontextprotocol/ext-apps` SDK](https://github.com/modelcontextprotocol/ext-apps) as the protocol authorities.

Keep the server operation and presentation contract distinct:

- validate and authorize every server operation independently;
- return bounded structured data and a useful model-facing summary;
- render that data in the UI resource;
- treat every UI message and model-produced value as untrusted;
- do not make critical behavior available only through an App-capable browser host.

## Installation and Quickstart

Install the optional dependencies with the owning package manager, then pin Prefab to the version exercised by tests:

```bash
pip install "fastmcp[apps]" "prefab-ui==<tested-version>"
```

The canonical Interactive Tool shape is a typed Prefab return plus `app=True`:

```python
from fastmcp import FastMCP
from prefab_ui.app import PrefabApp
from prefab_ui.components import Column, DataTable, DataTableColumn

mcp = FastMCP("Directory")


@mcp.tool(app=True)
def team_directory() -> PrefabApp:
    rows = [
        {"name": "Alice", "role": "Engineer"},
        {"name": "Bob", "role": "Designer"},
    ]
    with PrefabApp() as app:
        with Column(gap=4, css_class="p-6"):
            DataTable(
                columns=[
                    DataTableColumn(key="name", header="Name", sortable=True),
                    DataTableColumn(key="role", header="Role", sortable=True),
                ],
                rows=rows,
                search=True,
            )
    return app
```

`PrefabApp()` is the root. Components created inside its context manager form the UI tree; `Column`, `Row`, and `Grid` establish nesting and layout. `app=True` wires the renderer resource, CSP, visibility, and tool metadata. An explicit or union return annotation containing `PrefabApp` or `Component` can also trigger Prefab App inference — observed in the audited pairing, and **not re-verifiable here** because inference requires the Prefab types. Prefer explicit `app=True` regardless when App behavior is part of the public contract.

Preview the tool without an external MCP host:

```bash
fastmcp dev apps server.py
```

Open `http://localhost:8080`, choose the tool, fill in the generated argument form, and launch it. The MCP server uses port 8000 by default.

## Interactive Tools and Prefab

### Tables, Charts, and Composition

Return a bare Prefab component for a single view or a `PrefabApp` for a composed surface.

- `DataTable` accepts row dictionaries and `DataTableColumn` definitions; enable `sortable=True` per column and `search=True` for table search.
- `BarChart`, `LineChart`, `AreaChart`, `PieChart`, `RadarChart`, and `RadialChart` accept lists of dictionaries and key mappings.
- Use `ChartSeries(data_key=..., label=...)` for multi-series charts and the chart-specific x/name/value keys.
- `Column` stacks children, `Row` lays them out horizontally, and `Grid` creates column layouts.
- Components may be nested in table cells. Use badges, progress bars, icons, or buttons when the cell itself needs presentation or interaction.
- Use `Metric`, cards, separators, typography, and layout components to compose dashboards. Consult the installed Prefab component signatures for styling, stacking, curves, colors, and component-specific options.

### Browser-Local Reactivity

Use Prefab state for immediate interactions that do not require server data. State is a client-side key-value store initialized by `PrefabApp(state={...})`.

```python
from prefab_ui.actions import SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import DataTable
from prefab_ui.components.control_flow import If
from prefab_ui.rx import Rx, STATE

with PrefabApp(state={"selected": None}) as app:
    DataTable(
        rows=rows,
        columns=columns,
        on_row_click=SetState("selected", Rx("$event")),
    )
    with If(STATE.selected):
        render_details(Rx("selected"))
```

- `SetState("selected", Rx("$event"))` writes the event payload, such as a clicked row, into state.
- `Rx("selected.name")` is a browser-evaluated reactive reference, not a Python value.
- `STATE.selected` is shorthand for a state reference.
- `If(...)` conditionally renders its children.
- Named inputs such as `Select(name="region")` and `Switch(name="show_target")` write to their matching state keys.
- A component `let={...}` binding can derive local values from state.
- `Rx` supports arithmetic, comparison, ternary `.then()`, and formatting pipes such as `.currency()` and `.percent()`; verify the exact expression surface against the installed Prefab version.

Use this path for filters, tabs, toggles, conditional detail views, local sorting, and other interactions that need no server round-trip. Use `FastMCPApp` when an interaction must read or mutate server state.

In Prefab UI 0.20.2, deep interpolation recreates literal `DataTable.rows` arrays on every parent state update. TanStack Table can then repeatedly auto-reset pagination and raise React's maximum-update-depth error even when pagination is not displayed. For a table that shares a reactive view with switches, row selection, or other state, initialize the rows in `PrefabApp.state` and bind `rows=Rx("rows")` so the renderer receives a stable array identity. Exercise the actual toggle or state change in a browser; a serialized-tree assertion will not expose this loop.

### CSP for Prefab Tools

Use `PrefabAppConfig` when the Prefab renderer needs additional origins. A no-argument `PrefabAppConfig()` is equivalent to `app=True`.

```python
from fastmcp.apps import PrefabAppConfig, ResourceCSP

@mcp.tool(
    app=PrefabAppConfig(
        csp=ResourceCSP(frame_domains=["https://example.com"]),
    )
)
def embedded_dashboard() -> PrefabApp:
    ...
```

Add only the required origins. `PrefabAppConfig` merges them with the renderer's own CSP.

### Give the Model Useful Context

The default model-facing text for a Prefab result is `[Rendered Prefab UI]`. When the model must reason about the displayed data, return `ToolResult` with text content for the model and the Prefab tree in `structured_content`:

```python
from fastmcp.tools import ToolResult

return ToolResult(
    content=f"Total revenue: ${total:,} across {len(rows)} periods",
    structured_content=view,
)
```

Keep the text concise and decision-relevant; do not duplicate an unbounded dataset into model context.

## FastMCPApp for UI-to-Server Workflows

Use `FastMCPApp` when a UI must call backend operations. It is a Provider that owns model-visible entry points, app-visible backend tools, stable app identity, and composition-safe tool resolution.

```python
from fastmcp import FastMCP, FastMCPApp
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp
from prefab_ui.components import Button, Column, Form, Input
from prefab_ui.rx import RESULT, STATE

app = FastMCPApp("Notes")
notes: list[dict[str, str]] = []


@app.tool()
def add_note(title: str, body: str) -> list[dict[str, str]]:
    notes.append({"title": title, "body": body})
    return list(notes)


@app.ui()
def notes_app() -> PrefabApp:
    with Column(gap=4) as view:
        with Form(
            on_submit=CallTool(
                add_note,
                arguments={"title": STATE.title, "body": STATE.body},
                on_success=[
                    SetState("notes", RESULT),
                    ShowToast("Saved", variant="success"),
                ],
                on_error=ShowToast("Save failed", variant="error"),
            )
        ):
            Input(name="title", required=True)
            Input(name="body", required=True)
            Button("Add note")
    return PrefabApp(
        view=view,
        state={"notes": list(notes), "title": "", "body": ""},
    )


mcp = FastMCP("Notes Server", providers=[app])
```

### Registration and Visibility

| Decorator | Default visibility | Options on the pinned release |
| --- | --- | --- |
| `@app.ui()` | `['model']` | `name`, `description`, `title`, `tags`, `icons`, `annotations`, `auth`, `timeout` |
| `@app.tool()` | `['app']` | `name`, `description`, `model`, `auth`, `timeout` |

Both inventories re-verify against the installed signatures; `FastMCPApp` and its decorators are importable without the `apps` extra, so this table is locally confirmed even here. `FastMCPApp.__init__` takes only `name`.

Note how much narrower these are than `@mcp.tool`: neither accepts `version`, `output_schema`, `meta`, `app`, `task`, or `run_in_thread`, and `@app.tool()` additionally has no `title`, `tags`, `icons`, or `annotations`. Backend tools are app-internal, so that is usually right — but a backend operation needing versioning or an explicit output schema belongs on the owning `FastMCP` server, not in the app.

Entry points return a Prefab component or `PrefabApp` and are what the model calls to open the UI. Backend tools perform the actual server work and are hidden from the model by default. Pass `model=True` to a backend tool to expose it to both the app and model.

All decorator forms are supported: `@app.ui`, `@app.ui()`, `@app.ui("custom_name")`, and the equivalent `tool` forms. Preserve unique names within an app; the local provider rejects duplicates.

### Call Backend Tools

Use `CallTool` in event handlers such as `on_click`, `on_submit`, and `on_change`:

```python
from prefab_ui.actions.mcp import CallTool
from prefab_ui.rx import STATE

CallTool(save_contact, arguments={"name": STATE.name})
```

Prefer a direct function reference. FastMCP resolves it to a stable app-aware identifier that survives namespacing and composition. A string such as `CallTool("save_contact")` works locally but is easier to break during renaming or mounting.

Server calls are asynchronous:

- `on_success` receives access to `RESULT`;
- `on_error` receives access to `ERROR`;
- either callback may be one action or an ordered list;
- ordered callbacks short-circuit on error.

Prefab UI 0.20.2 does not expose a `result_key` constructor argument on `CallTool`. Persist a result explicitly with `on_success=SetState("contacts", RESULT)` or include that action in an ordered success list.

### Client-Side Actions and Loading

Use `SetState`, `ToggleState`, `AppendState`, `PopState`, and `ShowToast` for browser-local changes. Attach one action or a list to an event handler; lists execute in order.

Model pending state explicitly around server calls:

```python
saving = Rx("saving")

Button(
    saving.then("Saving...", "Save"),
    disabled=saving,
    on_click=[
        SetState("saving", True),
        CallTool(
            save_data,
            on_success=[SetState("saving", False), SetState("result", RESULT)],
            on_error=[SetState("saving", False), ShowToast("Failed", variant="error")],
        ),
    ],
)
```

Initialize `saving=False`, reset it on both success and error, prevent duplicate submissions, and provide visible progress and failure feedback.

### Forms

Manual forms collect named input values and pass them as tool arguments:

```python
with Form(
    on_submit=CallTool(
        create_ticket,
        arguments={
            "title": STATE.title,
            "priority": STATE.priority,
            "description": STATE.description,
        },
    )
):
    Input(name="title", label="Title", required=True)
    with Select(name="priority", label="Priority"):
        SelectOption("Low", value="low")
        SelectOption("High", value="high")
    Textarea(name="description", label="Description")
    Button("Create ticket")
```

Use `Form.from_model(Model, on_submit=...)` for structured input. Prefab maps strings to text inputs, `Literal` to selects, and booleans to checkboxes; field titles, defaults, and validation flow from Pydantic. The backend tool must still accept and validate the declared model at the server boundary.

In the audited FastMCP / Prefab UI 0.20.2 pairing, the renderer does not automatically merge browser `FormData` into an empty `CallTool.arguments` object. Initialize every named field in App state and bind arguments explicitly with `STATE` as shown above. Verify the AppBridge trace contains the submitted values; a Python-only direct call to the hashed backend does not prove the browser form binding.

### Composition and Running

Add a `FastMCPApp` through `FastMCP(..., providers=[app])` or `mcp.add_provider(app)`. Multiple apps can coexist even when they have identically named backend tools because each receives a distinct app identity and global key.

When a server is mounted under a namespace, normal tool names are prefixed. Function-reference `CallTool` actions remain valid because App calls include the app identity and resolve through the provider tree without namespace or visibility transforms. Authorization still runs.

The stock global `AuthMiddleware` looks up the literal tool name before core dispatch and therefore rejects `<hash>_<local-name>` AppBridge calls on authenticated HTTP servers. **This re-verifies on the pinned release** by reading the two sides:

- `AuthMiddleware.on_call_tool` takes `context.message.name` and resolves it with `fastmcp.get_tool(tool_name, version=...)` — normal-name lookup only — raising `AuthorizationError("... not found or not authorized")` when that returns `None`;
- hashed dispatch lives further in, in the server's `tools/call` handler, which tries `get_tool(name)` first and only then falls back to `parse_hashed_backend_name` plus `get_tool_by_hash`.

Middleware runs before that fallback, so an authenticated HTTP AppBridge call fails at the middleware with an ambiguous not-found error. The bypass path does apply the tool's own component `auth`, which is why component-level checks look correct in isolation. `AuthMiddleware` also skips all checks under stdio, so a stdio protocol test cannot reproduce it; protocol tests without authentication and ordinary public write tools do not expose it either.

Test a real hashed backend call over authenticated HTTP. If the pinned release exhibits the failure, use a narrowly owned middleware adapter or upgrade to an audited fixed release; the adapter must parse only valid hashed names (12 hex characters followed by `_`), resolve with `get_tool_by_hash`, run the backend tool's component checks and each configured global check, retain indistinguishable not-found/unauthorized errors, and leave stdio behavior unchanged. Remove the adapter when the installed middleware owns equivalent routing.

For local development, `app.run()` wraps the Provider in a temporary `FastMCP` server. Use an owning `FastMCP` server for production so transport, auth, middleware, lifespan, and deployment remain explicit.

## Generative UI

Register `GenerativeUI` when the model must author Prefab Python for the current request and data:

```python
from fastmcp import FastMCP
from fastmcp.apps.generative import GenerativeUI

mcp = FastMCP("Prefab Studio")
mcp.add_provider(GenerativeUI())
```

One provider registers:

- `generate_prefab_ui`, which accepts Prefab Python, executes it in a Pyodide sandbox, and returns the rendered app;
- `search_prefab_components`, which introspects the installed Prefab component library;
- a streaming `ui://` renderer that executes complete partial code as the model produces the `code` argument.

The streaming lifecycle is:

1. The host creates the renderer alongside the tool call.
2. Partial arguments arrive through `ontoolinputpartial`.
3. The renderer extracts the growing `code` string.
4. Browser Pyodide executes portions that compile and progressively renders them.
5. When generation ends, server-side Pyodide validates the complete program.
6. The renderer replaces the preview with the server-validated result.

The generated program may use standard Python, loops, f-strings, calculations, helpers, Prefab components, and a `PrefabApp`. It must not import external packages. The sandbox provides only the Python standard library and Prefab; NumPy, pandas, requests, and other packages raise `ImportError`.

Use the component search tool before generation when component names or fields are uncertain. `detail=True` returns field descriptions and docstrings. Because it introspects runtime classes, its results follow the installed Prefab version.

The generation tool accepts optional `data`; dictionary entries become sandbox globals. Use this to pass bounded, already-authorized data rather than embedding it into code.

Constructor options are:

```python
GenerativeUI(
    tool_name="generate_prefab_ui",
    components_tool_name="search_prefab_components",
    include_components_tool=True,
)
```

The server-side sandbox needs Deno installed; its first execution downloads and caches the Pyodide npm package and Python wheels. The browser renderer loads Pyodide from a CDN; the Provider supplies the required CSP. Treat runtime code generation as untrusted execution even though it is sandboxed: bound input size, data volume, runtime, and output, and do not pass secrets or capabilities into the sandbox. Apply one whole-operation deadline to process startup and execution, cap the protocol line before parsing JSON, discard the worker on timeout, cancellation, broken pipe, malformed protocol, or oversized output, and close it during the Provider/server lifespan. A recovery test must prove that a late response from a failed request cannot satisfy the next request.

In the audited Prefab UI 0.20.2 implementation, Deno chooses npm mode automatically. Inside a pnpm workspace this can select manual `node_modules` mode, fail to resolve `npm:pyodide`, and offer to copy pnpm workspace configuration into `package.json`. Reproduce a real server-side generation before shipping. If this conflict occurs, use a version-pinned, project-owned sandbox adapter that preserves Prefab's execution flags while adding `deno run --node-modules-dir=none`; then test the exact command and confirm the repository manifest is unchanged. Do not duplicate `workspaces` or `catalog` into `package.json` as a runtime workaround. Re-audit and remove the adapter when the pinned Prefab implementation exposes equivalent configuration or changes its launch contract.

## Custom HTML Apps

Use custom HTML only when Prefab cannot express the required UI. A custom App has two components:

1. a tool that performs validated, authorized work and returns data;
2. a `ui://` resource containing HTML, CSS, and JavaScript that renders the result.

Link them with `AppConfig`:

```python
from fastmcp import FastMCP
from fastmcp.apps import AppConfig

mcp = FastMCP("Custom App")
VIEW_URI = "ui://custom/view.html"


@mcp.tool(app=AppConfig(resource_uri=VIEW_URI))
def load_view() -> dict[str, object]:
    return {"status": "ready"}


@mcp.resource(VIEW_URI)
def view() -> str:
    return "<html>...</html>"
```

The `ui://` resource is automatically served as `text/html;profile=mcp-app`. The host fetches it, renders it in a sandboxed iframe, pushes the tool result through `postMessage`, and forwards App-originated server tool calls.

### AppConfig and Wire Fields

`app` accepts `True`, an `AppConfig`, or a raw dictionary. Prefer typed Python fields; use camelCase only for a raw wire-format dictionary such as `{"resourceUri": VIEW_URI}`.

The camelCase here is **correct and must survive the SDK v2 snake_case sweep**. `app_config_to_meta_dict(AppConfig(resource_uri=..., prefers_border=True))` returns `{'resourceUri': ..., 'visibility': [...], 'prefersBorder': True}` — verified by execution. This is the MCP Apps extension's own wire format, governed by `io.modelcontextprotocol/ui`, not by the MCP SDK field rename that turned `inputSchema` into `input_schema`. Renaming these to snake_case would break the host handshake. Python-side field names stay snake_case; the serialized `meta['ui']` payload stays camelCase.

| Field | Scope | Meaning |
| --- | --- | --- |
| `resource_uri` | Tool only | UI resource URI; serialized as `resourceUri` |
| `visibility` | Tool only | `['model']`, `['app']`, or both |
| `csp` | Tool or resource | `ResourceCSP` for the iframe |
| `permissions` | Tool or resource | Requested `ResourcePermissions` |
| `domain` | Tool or resource | Stable sandbox origin |
| `prefers_border` | Tool or resource | Border preference; serialized as `prefersBorder` |

Do not set `resource_uri` or `visibility` on a UI resource because the resource is already the presentation surface. Use `visibility=['app']` for UI-only helpers and `['model', 'app']` only when both callers need the operation.

### Host Bridge

Use the `@modelcontextprotocol/ext-apps` JavaScript SDK instead of hand-rolling the handshake. Pin the SDK version and allow its origin in CSP if it is loaded from a CDN.

```javascript
import { App } from "https://unpkg.com/@modelcontextprotocol/ext-apps@0.4.0/app-with-deps";

const app = new App({ name: "My App", version: "1.0.0" });
app.ontoolresult = ({ content }) => render(content);
app.onhostcontextchanged = (context) => applyHostContext(context);
await app.connect();
```

The bridge exposes `ontoolresult`, `callServerTool({name, arguments})`, `onhostcontextchanged`, and `getHostContext()`. Use host context for theme and safe-area changes. Validate data before rendering and validate again inside every server tool called from the UI.

### CSP and Permissions

Apps use a deny-by-default CSP. Inline content is available by default; declare every required external origin:

| `ResourceCSP` field | Controls                                          |
| ------------------- | ------------------------------------------------- |
| `connect_domains`   | `fetch`, XHR, and WebSocket through `connect-src` |
| `resource_domains`  | scripts, images, styles, and fonts                |
| `frame_domains`     | nested iframes through `frame-src`                |
| `base_uri_domains`  | document base URI through `base-uri`              |

Request browser capabilities through `ResourcePermissions`: `camera`, `microphone`, `geolocation`, and `clipboard_write` are the typed capabilities. Hosts may deny any request, so feature-detect and provide a fallback. Grant the narrowest set of origins and permissions; never expose bearer tokens, cookies, environment variables, or server-only data to browser code.

The QR example in the guide demonstrates the complete low-level flow: a tool creates an image `ToolResult`, a `ui://` resource loads the pinned Apps SDK from an allowed origin, `ontoolresult` finds the image content block, and DOM APIs render an accessible image. Follow the same separation for other binary results; keep data creation on the server and rendering in the iframe.

### Host Capability Fallback

Not every client implements Apps. Detect the extension in a Context-aware tool and return useful plain content when absent:

```python
from fastmcp import Context
from fastmcp.apps import AppConfig, UI_EXTENSION_ID

@mcp.tool(app=AppConfig(resource_uri=VIEW_URI))
async def load_view(ctx: Context) -> str:
    if ctx.client_supports_extension(UI_EXTENSION_ID):
        return rich_response()
    return plain_text_response()
```

## Built-In App Providers

Add providers through `providers=[...]` or `mcp.add_provider(...)`. Their UIs improve interaction but do not replace server-side validation, authorization, durable approvals, or storage isolation.

### Approval

```python
from fastmcp.apps.approval import Approval

mcp.add_provider(Approval())
```

This registers model-visible `request_approval`. Its constructor defaults and variants are:

```python
Approval(
    name="Approval",
    title="Approval Required",
    approve_text="Approve",
    reject_text="Reject",
    approve_variant="default",  # default, destructive, success, info
    reject_variant="outline",   # default, outline, destructive, success, info
)
```

Each call accepts `summary`, optional `details`, `title`, `approve_text`, `reject_text`, `approve_variant`, and `reject_variant`. A button runs `SendMessage` with a user-like `I selected:` response and marks the card decided with `SetState`. The tool description tells the model to wait for that response.

This is advisory UX, not an enforcement mechanism: the conversation is not blocked. For consequential actions, create a server-side pending operation, verify an authenticated approval record, bind it to action parameters and actor, and consume it exactly once before executing.

### Choice

```python
from fastmcp.apps.choice import Choice

mcp.add_provider(Choice(
    name="Choice",
    title="Choose an Option",
    variant="outline",  # default, outline, destructive, success, info
))
```

This registers model-visible `choose(prompt, options, title=None)`. It renders one full-width button per option. Selection uses `SendMessage` and then hides the buttons with decided state. Like Approval, it is advisory; validate the chosen value server-side and use a durable state machine when the selection gates an action.

### File Upload

```python
from fastmcp.apps.file_upload import FileUpload

mcp.add_provider(FileUpload(
    name="Files",
    max_file_size=10 * 1024 * 1024,
    title="File Upload",
    description="Drop files to upload them to the server.",
    drop_label="Drop files here",
))
```

The Provider registers:

| Tool           | Visibility | Purpose                           |
| -------------- | ---------- | --------------------------------- |
| `file_manager` | Model      | Open the upload UI                |
| `store_files`  | App only   | Validate and store uploaded files |
| `list_files`   | Model      | List uploaded-file summaries      |
| `read_file`    | Model      | Read a named file                 |

`max_file_size` is enforced by both the DropZone and server. Do not rely on browser validation alone.

The default store is process memory partitioned by MCP session ID. It works across stdio, SSE, and stateful HTTP sessions. Stateless HTTP creates a new session per request, so override `_get_scope_key(ctx)` with a stable authenticated user or tenant identifier. A process-wide constant creates intentionally shared storage and must not be used accidentally.

Resolve the current authenticated token with `fastmcp.server.dependencies.get_access_token()`, whose signature is `() -> AccessToken | None`. **`Context` still exposes no `access_token` property** — `hasattr(Context, "access_token")` is `False` on the pinned release, so reaching for `ctx.access_token` raises `AttributeError` rather than returning `None`. Prefer `token.subject`, then a validated `token.claims["sub"]`, then `token.client_id`, and fall back to `ctx.session_id` only when no authenticated principal exists.

For a synchronous persistent storage backend, subclass `FileUpload` and implement:

- `on_store(files, ctx)`: input files contain `name`, `size`, `type`, and base64 `data`; return summaries;
- `on_list(ctx)`: return summaries with `name`, `type`, `size`, `size_display`, and `uploaded_at`;
- `on_read(name, ctx)`: return metadata plus decoded `content` or a `content_base64` preview;
- optionally `_get_scope_key(ctx)`: return the stable partition key.

Authorize every method, normalize and validate filenames, prevent traversal and overwrite surprises, verify declared type against content when it matters, scan untrusted files where required, bound decoded size, and apply retention and deletion policy. Do not send file bytes through the model unless the use case explicitly needs them.

The `FileUpload` implementation invokes `on_store`, `on_list`, and `on_read` synchronously — observed in the audited pairing, and **not re-verifiable here** because `fastmcp.apps.file_upload` raises `ImportError` without `prefab-ui`. Re-confirm against the installed implementation before relying on it. Do not pass coroutine callbacks to those hooks: their results will not be awaited. When the durable backend is asynchronous, override the Provider's tool registration and preserve the built-in tool names, app/model visibility, Prefab UI contract, size checks, and per-tool authorization while awaiting the repository calls. Re-audit this workaround on upgrade. Prove persistence with two independently constructed repositories or servers against the same namespace, and prove that an authenticated second principal cannot list or read the first principal's records. A shared key-value backend provides persistence and visibility, not atomic read-modify-write across replicas; serialize each principal's writer or use a transactional domain store for high-contention records.

### Form Input

```python
from pydantic import BaseModel
from fastmcp.apps.form import FormInput

class BugReport(BaseModel):
    title: str
    severity: str

mcp.add_provider(FormInput(model=BugReport))
```

The Provider registers model-visible `collect_{modelname}` and app-only `submit_form`. Override the generated name with `tool_name`. The collection call accepts `prompt`, optional `title`, optional `submit_text`, and an optional partial `default` dictionary when the installed Prefab supports form defaults.

Prefab field mapping is:

| Pydantic type   | Form control   |
| --------------- | -------------- |
| `str`           | Text input     |
| `int`, `float`  | Number input   |
| `bool`          | Checkbox       |
| `datetime.date` | Date picker    |
| `Literal[...]`  | Select         |
| `SecretStr`     | Password input |

Use `Field(title=...)` for labels, `description` for helper text or placeholders, constraints such as `min_length`, `max_length`, `ge`, and `le` for validation, and `json_schema_extra={"ui": {"type": "textarea"}}` for multiline text.

Constructor options are:

```python
FormInput(
    model=BugReport,
    name="BugTracker",
    title="File a Bug",
    tool_name="file_bug",
    submit_text="Submit Report",
    on_submit=save_report,
    send_message=True,
)
```

`FormInput` does not accept component `auth` or `tags` options even though it registers two tools — observed in the audited pairing, and **not re-verifiable here** because `fastmcp.apps.form` raises `ImportError` without `prefab-ui`. Check the installed constructor before building around it. When a release-pinned subclass must apply one policy to both, do not traverse `FastMCPApp._local._components`. Override protected `_list_tools()` and `_get_tool()`, call `super()`, and idempotently apply the policy to each returned `Tool`. Cover both paths: catalog enumeration uses `_list_tools()`, while hashed AppBridge dispatch calls `_get_tool()` directly. Test the catalog metadata and an authenticated hashed backend call that permits the required scope and rejects a read-only principal.

Without `on_submit`, the validated model is serialized to JSON. With it, the callback receives the validated model and returns the tool result string. `send_message=True` also pushes the result into the conversation and triggers the model's next turn. Add multiple `FormInput` providers for multiple models, ensuring their tool names remain distinct.

The built-in `FormInput` in the audited FastMCP / Prefab UI 0.20.2 pairing also serializes its internal `submit_form` action with empty arguments. If the AppBridge trace confirms `{}` at runtime, keep the provider but add an owned result adapter that initializes non-secret field state and rewrites the action to `{"data": {"field": "{{ field }}"}}`, or use a `FastMCPApp` form with explicit `STATE` bindings. Never pre-populate a `SecretStr`, and keep protocol and browser regression tests until the pinned dependency is upgraded and re-audited.

## Local Development

`fastmcp dev` is a command **group**, and `apps` is one of its two subcommands (the other is `inspector`). Run:

```bash
fastmcp dev apps server.py:mcp --mcp-port 9000 --dev-port 9090 --no-reload
```

The subcommand takes a required `SERVER-SPEC` plus exactly five options, verified from `fastmcp dev apps --help`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--mcp-port` | `8000` | Port for the user's MCP server |
| `--dev-port` | `8080` | Port for the FastMCP dev UI |
| `--reload` / `--no-reload` | `True` | Auto-reload the MCP server on file changes |
| `--host` | `127.0.0.1` | Host to bind to |
| `--log-panel` / `--no-log-panel` | `True` | Log panel feature in the FastMCP dev UI |

`--reload` and `--log-panel` are boolean pairs that both default to **`True`**; disable either with its `--no-` form (`--no-reload`, `--no-log-panel`). Passing the bare positive form is a no-op.

`--host` defaults to loopback. Changing it exposes an unauthenticated development server carrying your real tool catalog to the network; keep the default unless a specific container or remote-browser setup requires otherwise, and never bind it on a shared host.

The command is registered by the installed `server` extra, so `fastmcp dev apps --help` introspects fine here — but actually running it requires `fastmcp[apps]`, as its help text states. A working `--help` is not evidence that the Apps runtime is available.

The picker connects to the server, lists tools carrying App metadata, and generates input forms from their schemas. Submitting calls the tool over MCP and opens the linked renderer resource in an AppBridge tab. When multiple App tools exist, the picker shows a dropdown using a tool title when available and otherwise its name.

Use the inspector panel to examine requests, responses, AppBridge `postMessage` traffic, direction, method, timing, summaries, and expanded JSON-RPC bodies. Confirm the tool received the expected arguments, returned the intended `content` and `structured_content`, and routed UI tool calls back to the correct app.

The development UI is not production host proof. Exercise every supported target host because extension negotiation, frame sizing, context, permissions, and presentation differ.

Keep a checked-in browser suite for the development host rather than relying on manual inspection alone. Start the MCP and picker on deterministic isolated ports, run a real browser against the iframe, retain traces/screenshots on failure, and own the entire child process group so teardown cannot orphan the MCP subprocess. Cover a compact viewport, page/console errors, named reactive inputs, every AppBridge backend action, validation and error state, theme and permission fallback, and at least one non-mocked Deno generation.

## Architecture and Debugging

Use this model when an App does not render or a UI tool call does not arrive:

```text
Python components -> JSON tree -> structured_content -> renderer iframe -> host UI
```

### Registration

- `@mcp.tool(app=True)` accepts `True`, `AppConfig`, or a raw dict.
- For a qualifying Prefab return annotation, FastMCP can infer the renderer even without an explicit `app` argument.
- Prefab wiring installs CSP/visibility metadata under `meta['ui']` and exposes a deterministic per-tool renderer such as `ui://prefab/tool/<hash>/renderer.html`.
- `FastMCPApp` tags entry points and backend tools with `meta['fastmcp']['app']` and a deterministic `_tool_hash`; entry points default model-visible and backends app-visible.

### Serialization

`PrefabApp.to_json()` emits the Prefab marker, `view`, and `state`. FastMCP supplies a tool resolver that converts function-reference `CallTool` actions into deterministic `<12-hex-hash>_<local-tool-name>` wire names. The hash is `sha256(f"{app_name}\x00{tool_name}")` truncated to 12 hex characters, computed at registration time and stored in `meta["fastmcp"]["_tool_hash"]`, so it is stable across replicas without a registry walk.

There is **no `unwrap_result` symbol anywhere in the installed package** — a grep across the whole distribution returns nothing. If a prior note claimed the resolver carries `unwrap_result` behavior for single-value schema envelopes, that name does not exist to verify against. Treat single-value envelope handling as an unverified Prefab-side detail and assert the actual `RESULT` shape in an AppBridge trace instead.

The final `ToolResult` contains model-facing `content` blocks and renderer-facing `structured_content`, renamed from v3's camelCase `structuredContent`. Inspect both when the UI and model observe different results.

### App Tool Routing

Normal tool calls resolve through Provider transforms such as namespace and visibility. For a hashed UI backend call, the dispatcher parses the hash prefix, walks the Provider tree, and selects the tool whose stored `meta['fastmcp']['_tool_hash']` matches. This preserves function-reference calls across namespaces and composition without exposing app-only tools to normal model discovery, while still applying the tool's auth checks. Global middleware must also recognize this routing form before core dispatch; verify that property against the installed release rather than assuming normal-name lookup covers AppBridge calls.

Aggregate Providers search children; wrapped `FastMCPProvider` instances delegate to the nested server. This supports deep composition but does not remove the need for unique names within each app or explicit authorization.

### Renderer and Bridge

Each Prefab tool exposes a deterministic `ui://prefab/tool/<hash>/renderer.html` resource with MIME type `text/html;profile=mcp-app`; generative UI uses its own `ui://prefab/generative.html` renderer. The resource includes renderer CSP metadata. The sandboxed iframe uses the MCP Apps SDK's AppBridge handshake:

1. the host pushes `structured_content` to the iframe;
2. the renderer builds state and paints the component tree;
3. a user event creates a `callServerTool` message;
4. the host forwards the resolved hashed name as an MCP `tools/call` request;
5. the server result flows host-to-iframe and updates state.

AppBridge also carries safe-area and theme context. Custom HTML uses the bridge directly; Prefab owns it internally.

### Development Proxy

`fastmcp dev apps` runs the MCP server on 8000 and UI on 8080 by default. The UI's `/mcp` reverse proxy forwards to the MCP server so iframe requests stay same-origin. Launching a tool calls it through the proxy, loads its renderer, creates AppBridge, and pushes the result. Reload restarts the MCP server but leaves the UI running; relaunch the tool after code changes.

Debug in pipeline order: catalog metadata, tool call result, UI resource retrieval and MIME type, CSP/permission console errors, AppBridge connection, outgoing `callServerTool`, app metadata, Provider resolution, auth, and returned state update.

## FastAPI and External Integrations

Choose ownership deliberately:

- Mount a FastMCP HTTP app in FastAPI/ASGI when MCP owns the contract and shares a deployment.
- Compose separate Providers when lifecycle or security boundaries should remain distinct.

When mounting, coordinate ASGI lifespan exactly once, keep the MCP path, OAuth metadata, CORS/origin, proxy, and health behavior explicit, avoid JSON middleware that corrupts streaming responses, and test through ASGI or real HTTP.

For OpenAPI generation, ChatGPT, Claude Code, Claude Desktop, OpenAI, Pydantic AI, and MCP JSON, load [Integration hosts and SDKs](integration-hosts-and-sdks.md). That reference owns their complete option inventory, source coverage, installed-version differences, and host-specific verification. Do not duplicate those volatile contracts here.

## App Verification

At minimum:

1. Prove the installed FastMCP, Apps extra, Prefab pin, and host extension support.
2. Inspect tool/resource metadata, visibility, App identity, renderer URI, MIME type, CSP, permissions, and component catalog through a FastMCP Client.
3. Invoke the underlying tool from a non-App client and verify useful fallback content.
4. Render in `fastmcp dev apps`, inspect the JSON-RPC/AppBridge trace, and exercise every event path in a repeatable browser test that fails on page or protocol errors.
5. Render in every target host and test theme, safe area, frame sizing, keyboard, focus, accessible names, loading, empty, error, cancellation, and retry behavior.
6. Test malformed structured data, missing capability, CSP denial, permission denial, untrusted text, duplicate submissions, callback failure, cancellation, and transport loss.
7. Re-authorize every UI-triggered operation; test rejected auth and cross-user or cross-tenant isolation.
8. For uploads, test size enforcement, binary/text behavior, persistence across a reconstructed server, authenticated principal isolation, stateless scope, and hostile names/content.
9. For generative UI, test invalid code, unavailable imports, time/resource/output bounds, cancellation and process recovery, oversized data, renderer/server validation disagreement, and one real Deno execution.
10. Run the owner's lint, type, test, and catalog inspection commands, inspect the diff, and record host behavior not exercised.

Do not treat browser rendering as proof of backend correctness, an advisory App as a security boundary, or local development behavior as proof of every host implementation.
