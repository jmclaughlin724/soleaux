# Authentication, Security, and Deployment

## Purpose and Audited Sources

Use this reference for remote HTTP deployment, authenticated mounts, horizontal scaling, proxy operation, and sandbox-facing servers. It incorporates the live [HTTP deployment](https://gofastmcp.com/deployment/http) and [sandboxed agents](https://gofastmcp.com/deployment/sandboxed-agents) guides as reviewed on 2026-07-14. Apply their design guidance, but resolve imports, signatures, defaults, and extras against the installed version.

See [Version and source routing](version-and-source-routing.md) for the pinned baseline. Two live-page examples need installed-first correction:

- The pinned release defaults `http_host_origin_protection` to `False`, despite the live page saying protection is on by default. Enable `host_origin_protection="auto"` or `True` explicitly and test rejection behavior. The type is `bool | Literal["auto"]`, and the keyword is accepted by `http_app()` and `run_http_async()`; `run()` forwards it through `**transport_kwargs`.
- Runtime settings/CLI default to host `127.0.0.1` and port `8000`; do not rely on a floating guide's example/default table. Set the production host, port, and path explicitly.

## Separate Security Layers

Authentication establishes who controls the connection or token. Authorization decides whether that actor may perform a specific operation on a specific object. Input validation establishes whether the request is structurally acceptable. Confirmation establishes current user intent. Keep all four explicit.

Do not expose a component merely because it is hidden from discovery. Enforce authorization when the component executes and again immediately before a delayed side effect.

### Input Validation Strictness

`strict_input_validation` is a Settings field defaulting to **`False`**, overridable per server through `FastMCP(strict_input_validation=...)`. It selects whether pydantic validates tool arguments in strict mode.

Left off, pydantic applies lax coercion: the JSON string `"10"` is accepted where an `int` is declared, and comparable coercions apply across the argument surface. Turned on, that same input is rejected.

The security consequence is that the type in the published schema stops being a reliable statement about what the tool body receives. A tool that declared `int` and validated a numeric range can be reached with a value that arrived as a string and was coerced afterwards, and any code that branches on the original wire type — a cache key, an audit record, a comparison against a stored value — sees something different from what the schema advertised. Coercion also widens the set of inputs that reach the body at all, so fuzzing and negative tests written against the declared type under-cover the real surface.

Turn it on for any server whose arguments cross a trust boundary, and treat flipping it as a compatibility change: clients that were relying on coercion begin receiving validation errors, so roll it out with the schema, the tests, and the consumers reviewed together.

## Choose Installed Auth Support

Inspect fastmcp.server.auth in the installed release and select the narrowest built-in:

- token verification for an existing OAuth/OIDC issuer;
- a remote auth provider when another authorization server owns discovery and issuance;
- an OAuth proxy/provider when FastMCP must adapt a provider that lacks required MCP behavior;
- a vendor provider when the installed extra supports the project’s issuer;
- custom verification only when no built-in can enforce issuer, audience, signature, expiry, and scopes.

Prefer standards-based discovery and asymmetric signature verification. Pin accepted algorithms. Validate issuer, audience/resource, expiry, not-before, subject, client, and required scopes. Fail closed when discovery or provider metadata is incomplete.

Never log tokens, authorization headers, cookies, PKCE verifiers, client secrets, or full claims.

### Concrete Providers

`fastmcp/server/auth/providers/` ships **19** modules:

`auth0`, `aws`, `azure`, `clerk`, `debug`, `descope`, `discord`, `github`, `google`, `huggingface`, `in_memory`, `introspection`, `jwt`, `keycloak`, `oci`, `propelauth`, `scalekit`, `supabase`, `workos`.

All 19 import successfully on the pinned environment — a provider module being importable is not evidence that every one of its code paths is usable. `azure` is the case to know: `AzureProvider` constructs and verifies tokens normally, but its On-Behalf-Of paths (`get_obo_credential()` and `EntraOBOToken`) call `_require_azure_identity(...)`, which imports `azure.identity` and otherwise raises `ImportError: <feature> requires the 'azure' extra. Install with: pip install 'fastmcp[azure]'`. This repository does not install it, so the failure surfaces at call time rather than import time — an OBO deployment looks healthy until the first exchange. Adding it is a manifest change, not a code change.

Treat `debug` and `in_memory` as development-only. `DebugTokenVerifier` accepts tokens without real verification; reaching production with either is an authentication bypass.

`fastmcp.server.auth.__all__` exposes `AccessToken`, `AuthCheck`, `AuthContext`, `AuthProvider`, `DebugTokenVerifier`, `IdentityAssertion`, `JWTVerifier`, `MultiAuth`, `OAuthProvider`, `OAuthProxy`, `OIDCProxy`, `RemoteAuthProvider`, `StaticTokenVerifier`, `TokenVerifier`, `require_roles`, `require_scopes`, `restrict_tag`, and `run_auth_checks`.

### MultiAuth

`MultiAuth` composes one auth server with additional token verifiers so a single server accepts tokens from more than one source — for example an OAuth proxy for interactive clients alongside a JWT verifier for machine-to-machine callers.

```python
from fastmcp.server.auth import JWTVerifier, MultiAuth, OAuthProxy

auth = MultiAuth(
    server=OAuthProxy(issuer_url="https://login.example.com/..."),
    verifiers=[JWTVerifier(jwks_uri="https://example.com/.well-known/jwks.json")],
)
mcp = FastMCP("service", auth=auth)
```

Its signature is keyword-only: `MultiAuth(*, server=None, verifiers=None, base_url=None, resource_base_url=None, required_scopes=None)`, where `verifiers` accepts one `TokenVerifier` or a list.

Verification tries `server` first, then each verifier **in order, returning the first success**. Order is therefore a security decision, not a formatting one: put the strictest verifier first, and never place a permissive verifier ahead of a strict one covering the same tokens. Routes and OAuth metadata come from `server` only — verifiers contribute token verification and nothing else, so a `MultiAuth` with no `server` advertises no OAuth discovery. `required_scopes` applies across the composition; re-test scope enforcement for every accepted token source, because a token that satisfies one verifier still has to satisfy the shared scope requirement.

### CIMD (Client ID Metadata Documents)

CIMD is a simpler alternative to Dynamic Client Registration: a client hosts a static JSON metadata document at an HTTPS URL, and **that URL becomes its `client_id`**. It implements the IETF draft `draft-parecki-oauth-client-id-metadata-document`, and its own source marks it **beta — the API may change**.

`enable_cimd: bool = True` — it is **on by default** on `OAuthProxy`, `OIDCProxy`, and the provider subclasses that expose it (`azure`, `clerk`, `discord`, `github`, `google`, `huggingface`, `workos`). Set `enable_cimd=False` to turn it off.

The implementation lives in `fastmcp/server/auth/cimd.py`: `CIMDDocument` validates the fetched document, `CIMDFetcher` retrieves it, and `CIMDClientManager` manages the resulting clients. Because the server dereferences a client-supplied URL, fetches route through `fastmcp/server/auth/ssrf.py` (`ssrf_safe_fetch_response`, `validate_url` with `require_path=True`). CIMD clients authenticate with `private_key_jwt` per RFC 7523 rather than a shared secret.

Two consequences for a deployment review. Leaving CIMD enabled means any client that can host an HTTPS document can register itself — appropriate for an open ecosystem, usually not for an internal server with a fixed client set, where `enable_cimd=False` plus explicit registration is the tighter contract. And the SSRF guard is what stands between a client-supplied URL and your internal network, so `ssrf_trust_proxy` (Settings default `False`) must stay accurate for your proxy topology; enabling it while untrusted hops can set forwarding headers undermines the check.

## Application Authorization

- Map the authenticated principal to an application actor through the owning domain.
- Evaluate tenant/resource membership and operation permission server-side.
- Use authoritative current state for mutations.
- Avoid trusting client-supplied organization IDs, roles, or scopes beyond their defined meaning.
- Return the narrowest data allowed for that actor.
- Emit immutable, redacted audit events for sensitive decisions and side effects.

Transport scopes may be necessary but are rarely sufficient application authorization.

For FastMCP `AuthCheck`, `require_scopes`, `restrict_tag`, component `auth=`, `AuthMiddleware`, and token-access APIs, use [Authorization](authorization.md).

## Resource Template Path Screening

Templated resources (`@mcp.resource("file:///{path}")`) extract parameter values straight out of the request URI and hand them to the resource function. When those values reach filesystem or URI construction, a client can smuggle traversal payloads through the template. `ResourceSecurity` screens extracted values **before the handler runs**, and it is **on by default**.

`FastMCP(resource_security=...)` sets the server-wide policy; its default is `ResourceSecurity(reject_path_traversal=True, reject_absolute_paths=True, reject_null_bytes=True, exempt_params=frozenset())`. The three checks are:

| Field | Default | Rejects |
| --- | --- | --- |
| `reject_path_traversal` | `True` | A standalone `..` path component |
| `reject_absolute_paths` | `True` | Values that look like absolute filesystem paths |
| `reject_null_bytes` | `True` | Values containing NUL (`\x00`) |

Null bytes get their own check because they defeat string comparison (`"..\x00" != ".."`) and can truncate in C extensions or subprocess calls.

Screening runs after URI matching, extraction, and percent-decoding, so it catches the payload regardless of encoding (`%2F`, `%5C`, `%2E%2E`). It reuses the SDK's component-based traversal check, so a value that merely contains dots — `HEAD~3..HEAD`, `v1..v2`, `file.tar.gz` — is not rejected; only an actual `..` segment is.

Per-component, `@mcp.resource(..., security=...)` accepts a `ResourceSecurity`, an explicit `None`, or the `INHERIT_SECURITY` sentinel that is the field default. The three are distinct: `INHERIT_SECURITY` means "no per-component policy was set, use the server default", while `None` **disables screening for that component**. Never reach for `None` to fix one over-strict parameter — exempt that parameter instead:

```python
from fastmcp.resources import ResourceSecurity

@mcp.resource(
    "git://diff/{ref}",
    security=ResourceSecurity(exempt_params={"ref"}),
)
def git_diff(ref: str) -> str: ...
```

`exempt_params` accepts either spelling of a hyphenated template variable: `{git-ref}` extracts as `git_ref`, and an exemption written either way matches. An exempted parameter is screened by nothing, so the handler now owns that value's safety — validate it against an allowlist there.

Path screening is not authorization. It stops traversal syntax; it does not decide whether this actor may read this resource. Keep [Authorization](authorization.md) checks in place alongside it.

## Choose the HTTP Deployment Shape

Use Streamable HTTP when a server needs network access, multiple concurrent clients, centralized operation, or cloud/agent connectivity.

| Shape | Use | Entry point |
| --- | --- | --- |
| Direct HTTP server | One standalone MCP service on one port with minimal web customization | `mcp.run(transport="http", host="0.0.0.0", port=8000, ...)` |
| ASGI application | Uvicorn/Gunicorn/Hypercorn, workers, custom middleware, an existing web app, or explicit mount/lifespan control | `app = mcp.http_app(...)` then `uvicorn app:app ...` |
| Local stdio | A desktop/CLI client owns one local process and remote exposure is unnecessary | guarded `mcp.run()` |
| Legacy SSE | Compatibility with an old client only | `transport="sse"`; do not select for new systems |

The direct and ASGI shapes expose the same MCP behavior. The default installed endpoint path is `/mcp`; pass `path=` to `run()` or `http_app()` when another URL is required. Authentication is highly recommended for any remote server and is required by some LLM clients.

## HTTP Request Hardening

- Bind to loopback for local development; expose `0.0.0.0` or another public interface only deliberately.
- Terminate TLS at the application or a trusted proxy and trust forwarded headers only from known proxies.
- Set `host_origin_protection="auto"` for loopback-aware protection or `True` for strict protection. Set `allowed_hosts=["mcp.example.com"]` and, only for direct browser clients, `allowed_origins=["https://app.example.com"]`.
- The equivalent settings are `FASTMCP_HTTP_ALLOWED_HOSTS` and `FASTMCP_HTTP_ALLOWED_ORIGINS`, each containing a JSON list. Do not disable protection unless an ingress performs equivalent validated checks.
- Host/Origin protection is a request guard before MCP session handling. It is not CORS and does not emit browser response headers.
- Keep the MCP path, OAuth operational routes, root discovery routes, callbacks, and health routes explicit.
- Set request, response, header, concurrency, and rate limits. Reject unsupported content types and oversized bodies before expensive parsing.
- Sanitize production errors, disable client-visible tracebacks, bound upstream/subprocess time, and preserve cancellation.

Review `FastMCP.run_http_async()` and `FastMCP.http_app()` in the installed release rather than assuming generic ASGI defaults.

### Health Routes, Middleware, and Browser CORS

`@mcp.custom_route("/health", methods=["GET"])` creates an operational route at the domain root, beside the MCP endpoint. FastMCP's `AuthProvider` **never protects custom routes**. Use them for public health/readiness responses with no credentials, provider metadata, stack traces, internal paths, or dependent service payloads. Mount FastMCP in FastAPI and use the parent framework's authentication for adjacent protected HTTP APIs.

Pass a list of Starlette `Middleware` objects to `http_app(middleware=...)` for owned ASGI middleware. When browser JavaScript connects directly to MCP:

- add the exact browser origin to FastMCP `allowed_origins` so the request guard accepts it;
- configure `CORSMiddleware` with exact production origins, methods `GET`, `POST`, `DELETE`, and `OPTIONS`;
- allow `mcp-protocol-version`, `mcp-session-id`, `Authorization`, and `Content-Type` headers;
- expose `mcp-session-id` so JavaScript can read and return the session identifier;
- never use `allow_origins=["*"]` in production.

Browser-hosted ChatGPT/Claude normally do not need CORS because their service, not page JavaScript, calls MCP. MCP Inspector and custom browser MCP clients do. Avoid stacking application-wide CORS over FastMCP OAuth routes; separate sub-apps and middleware when route ownership differs.

### Long Operations and SSE Polling

Streamable HTTP can use SEP-1699-style SSE polling to survive idle proxy/load-balancer timeouts. This is unrelated to legacy `transport="sse"`.

1. Construct `EventStore(storage=None, max_events_per_stream=100, ttl=3600)` or an owner-configured store.
2. Pass it to `mcp.http_app(event_store=store, retry_interval=<milliseconds>)`.
3. During a long tool, emit progress and call `await ctx.close_sse_stream()` at safe points.
4. The client reconnects with `Last-Event-ID`; the store replays missed progress/results. Without an EventStore, `close_sse_stream()` is a no-op.

In-memory EventStore storage is one-process only. For multiple replicas, pass a shared `AsyncKeyValue` backend such as `RedisStore`; bound `max_events_per_stream`, choose a retention `ttl`, and test replay, duplicate delivery, expiry, and backend loss.

## Mounting in Web Frameworks

For Starlette, create the MCP app and mount it. The final URL is the outer mount prefix plus the `http_app(path=...)` path; nested mounts compose in the same way. Pass `lifespan=mcp_app.lifespan` to the outer Starlette app because nested lifespans are not automatically run.

For FastAPI, a common shape is `mcp_app = mcp.http_app(path="/")`, `api = FastAPI(lifespan=mcp_app.lifespan)`, and `api.mount("/mcp", mcp_app)`. Without the MCP lifespan, the Streamable HTTP session manager is not initialized and requests fail.

### Mounting an OAuth-Protected Server

OAuth operational routes move under an application mount, but RFC 8414/RFC 9728 discovery remains rooted under `/.well-known`. Treat these URL parts separately:

- `base_url`: externally visible root for the provider's operational endpoints, including the application mount prefix;
- `mcp_path`: FastMCP's internal endpoint path only;
- `issuer_url`: optional authorization-server identity; defaults to `base_url` and creates path-aware discovery when it contains a path;
- invariant: `base_url + mcp_path == externally visible MCP URL`.

Do not repeat the mount prefix in `mcp_path`. For `ROOT_URL=https://example.com`, `MOUNT_PREFIX=/api`, and `MCP_PATH=/mcp`, configure provider `base_url=https://example.com/api`, build `mcp.http_app(path="/mcp")`, call `auth.get_well_known_routes(mcp_path="/mcp")`, place those discovery routes at the root, then `Mount("/api", app=mcp_app)` and propagate its lifespan.

That shape produces operational routes such as `/api/authorize`, `/api/token`, `/api/auth/callback`, and `/api/mcp`, plus path-aware discovery such as `/.well-known/oauth-authorization-server/api` and `/.well-known/oauth-protected-resource/api/mcp`. Exercise every advertised URL through the external proxy hostname.

## Sessions and Horizontal Scaling

Stateful Streamable HTTP keeps sessions in process. Elicitation and sampling depend on context that can span requests, so a client routed to a replica without its session fails. Cookie-based sticky sessions are not a reliable general solution because many MCP clients do not retain/forward load-balancer cookies.

For multiple Uvicorn workers or replicas, enable `stateless_http=True` on `http_app()`/`run()`, or set `FASTMCP_STATELESS_HTTP=true`, only after confirming the application does not require stateful MCP behavior. Each stateless request gets a fresh transport context. Test elicitation, sampling, notifications, progress, and any session state explicitly rather than assuming parity.

Use shared durable storage for EventStore replay, tasks, OAuth state/tokens, or application/session data that must survive process replacement. Do not rely on in-memory registries across replicas. Coordinate storage migrations, make background work idempotent under retry, drain connections/tasks during shutdown, and simulate replica/state loss.

## OAuth Keys, Tokens, and Runtime Secrets

Read secrets from a managed runtime source, never source code or committed `fastmcp.json`. A `StaticTokenVerifier` can be useful for local development, but standards-based OAuth/JWT with issuer, audience, expiry, and scopes is the production default.

OAuth Proxy/provider development key behavior is platform-specific: macOS/Windows may persist generated keys in a system keyring, while Linux may use ephemeral startup material that invalidates tokens on restart. Do not depend on either in production.

Production OAuth proxy deployments need both:

1. an explicit `jwt_signing_key` shared by the replicas that issue/verify FastMCP client tokens; and
2. persistent network-accessible `client_storage` for upstream tokens, wrapped in `FernetEncryptionWrapper` so those tokens are encrypted at rest.

Use a separately managed Fernet encryption key, HTTPS `base_url`, planned rotation, and a shared backend such as Redis. Without explicit signing material, unrelated secret rotation can invalidate issued tokens; without persistent encrypted storage, hosts disagree about tokens or store upstream credentials in plaintext.

## Sandboxed Agents

Use this pattern when agents run in ephemeral containers, subprocesses, or remote workers and need per-run, per-tenant, or per-job access to privileged internal systems. The sandbox is part of the trust boundary: its filesystem can be inspected, inherited environment may be broader than intended, and many differently scoped sandboxes may run concurrently.

Default architecture:

`sandboxed agent -- short-lived scoped bearer token --> remote FastMCP server --> internal APIs/databases/upstream MCP servers`

The sandbox receives only the MCP URL and a short-lived credential. The server keeps long-lived GitHub keys, database passwords, cloud credentials, OAuth client secrets, and upstream MCP credentials; verifies the token on every request; authorizes the claims; exposes only allowed capabilities; and centralizes revocation/audit.

### Sandbox Connection and Credential Design

- Prefer HTTP so authentication, lifecycle, auditing, and revocation are independent of the sandbox. STDIO remains appropriate for trusted local desktop development, not privileged production inheritance.
- Connect with `Client("https://sandbox-tools.example.com/mcp", auth=BearerAuth(token))`.
- Verify job tokens with an installed verifier such as `JWTVerifier(jwks_uri=..., issuer=..., audience="sandbox-mcp")`.
- Include a short expiry and the minimum useful run/sandbox ID, tenant or installation ID, applicable actor ID, and capability scopes. Never share one static token across sandboxes.
- Authentication establishes the sandbox identity. Enforce per-component authorization with claims, middleware, or an installed check such as `auth=require_scopes("write:summary")`.

### Capability Surface

Expose narrow structured capabilities such as `get_recent_updates`, `fetch_repo_context`, `write_summary`, or `publish_review_comment`. Avoid raw `run_sql`, arbitrary internal HTTP calls, broad filesystem/shell access, or a catch-all `mutate_state(kind, payload)` escape hatch. Narrow tools are easier to authorize, audit, retry, and use correctly.

When upstream systems are more privileged, put a sandbox-safe FastMCP facade/proxy in front of internal HTTP or MCP services. The facade authenticates the sandbox and uses its own upstream credentials only for server-authorized operations.

An `mcp.json` entry for a sandbox should contain only the remote URL and `transport: "http"`. Inject authentication through the trusted launcher/runtime; do not bake long-lived credentials into generated config files.

Also isolate actual code execution: use a supported sandbox/container/microVM, non-root user, read-only base filesystem, fresh bounded scratch space, no host sockets/cloud metadata/package install/arbitrary network/secrets by default, strict CPU/memory/process/file/output/time limits, and teardown after each run. Validation or restricted imports alone are not a sandbox.

Common failures are distributing upstream keys, treating sandbox helper scripts as enforcement, exposing broad mutation tools, reusing a token across jobs/tenants, or relying on inherited STDIO configuration. Test revocation and audit at the server boundary.

## Outbound Update Check

**`check_for_updates` is the one default that reaches the network.** It defaults to `"stable"`, and the check runs from `log_server_banner()` — so it fires on **every server start that prints the banner**, not only from the CLI. `show_server_banner` also defaults to `True`. [Settings and packaging](settings-and-packaging.md#update-check-egress) owns the endpoint, timeout, cache behavior, and full field contract; do not restate them here.

What this reference owns is the deployment consequence. It matters for three shapes. In an **air-gapped or egress-filtered** environment the call cannot succeed and buys a startup delay for nothing. In a **sandboxed** deployment it is unexpected traffic from a process whose whole point is a constrained egress surface — and it is the one call that will show up in an egress audit that the deployment did not ask for. On a **read-only or ephemeral filesystem** the cache never persists, so the request repeats every run rather than once per cache window.

Set `FASTMCP_CHECK_FOR_UPDATES=off` for those deployments (verified to resolve to `'off'`). Suppressing the banner through `--no-banner` or `show_server_banner=False` removes the server-start trigger too, but set the setting explicitly rather than relying on banner suppression — the two are separate switches and a future code path could check for updates without the banner. It is a convenience feature, not a security control, and disabling it changes nothing about how the server serves. Confirm the value through the environment that will actually run the process, since a setting present only in a developer shell will not follow the image.

## Process, Reverse Proxy, and Hosting

Pin Python/FastMCP/extras, run as a non-root service user, expose one intended port/path, and provide readiness/liveness behavior. Keep auto-reload and Inspector out of production.

Run one ASGI process with `uvicorn app:app --host 0.0.0.0 --port 8000`; add `--workers N` only with an approved stateless/shared-state design. For a Linux `systemd`/Uvicorn service, configure an explicit user/group, working directory, virtual-environment `ExecStart`, `Restart=always`, a bounded `RestartSec`, environment/PATH, and multi-user boot target; run daemon-reload, enable, and start through `systemctl`. For nginx or an equivalent proxy:

- redirect HTTP to HTTPS and terminate TLS with managed certificates;
- proxy to a loopback-bound Uvicorn service;
- use HTTP/1.1 and clear the upstream `Connection` header for keep-alive/SSE;
- forward `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto` from a trusted proxy;
- set `proxy_buffering off` and `proxy_cache off` so SSE events are delivered immediately;
- raise read/send timeouts above normal tool duration (the guide's nginx baseline is `300s` for each), or use EventStore polling for longer work;
- when stripping a prefix, keep trailing slashes consistent on both nginx `location /api/` and `proxy_pass .../`, then recompute OAuth discovery/operational URLs.

FastMCP can run on virtual machines, container platforms, PaaS offerings, compatible edge runtimes, or self-managed/managed Kubernetes. Confirm Python 3.10+ support for the live guide, the project's stricter Python pin, an exposed HTTP port, streaming/timeout behavior, state/storage topology, and provider-specific requirements/Docker packaging. Use managed FastMCP hosting only when the owner explicitly selects it.

## Security Verification

Test:

- missing, malformed, expired, wrong-issuer, wrong-audience, and insufficient-scope credentials;
- for `MultiAuth`, each accepted token source in isolation plus verifier order and shared `required_scopes`;
- CIMD enabled and disabled, including SSRF rejection of an internal-address metadata URL;
- resource-template traversal payloads in literal and percent-encoded form, exempted parameters, and `security=None` versus `INHERIT_SECURITY`;
- argument coercion under `strict_input_validation` both off and on;
- authenticated but unauthorized tenant/resource access;
- replay or double-submit of mutations;
- origin/host rejection and proxy-header handling;
- response and rate limits;
- sanitized exceptions and payload-free logs;
- state loss or replica change;
- exact outer/mounted MCP and OAuth discovery URLs plus lifespan startup;
- SSE polling reconnect/replay, EventStore expiry, and proxy buffering/timeouts;
- production signing-key consistency and encrypted shared OAuth token storage;
- sandbox timeout, escape attempts, filesystem/network denial, and cleanup;
- no unexpected egress at server start, including the PyPI update check, under the deployment's real network policy.

For a sandbox-facing deployment, also prove that tokens are short-lived/scoped, long-lived secrets remain server-side, tool catalogs match the token's capabilities, proxy calls cannot escape policy, and revocation/audit still work after replica changes.

Use [authentication](https://gofastmcp.com/servers/auth/authentication), [Authorization](authorization.md), [HTTP deployment](https://gofastmcp.com/deployment/http), [sandboxed agents](https://gofastmcp.com/deployment/sandboxed-agents), and [llms.txt](https://gofastmcp.com/llms.txt). Confirm provider, storage, and app APIs in installed source.
