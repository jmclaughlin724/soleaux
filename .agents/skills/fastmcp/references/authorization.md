# Authorization

## Source and Version Contract

Use this reference for the complete workflow represented by the live [FastMCP authorization guide](https://gofastmcp.com/servers/authorization), verified 2026-07-14. Authentication verifies the token/connection identity. Authorization decides whether that authenticated actor may discover or execute one component. Application authorization must additionally decide whether the actor may perform the operation on the selected tenant or object.

Confirm imports, callable types, error behavior, and token fields against installed source. See [Version and source routing](version-and-source-routing.md) for the pinned baseline.

The checks live in `fastmcp.utilities.authorization` (`AuthCheck`, `AuthContext`, `AuthorizationError`, `require_roles`, `require_scopes`, `restrict_tag`, `run_auth_checks`, `run_auth_checks_with_shortfall`, `scope_requirements`) and are re-exported from `fastmcp.server.auth`, whose surface is wider and adds the provider and verifier types. There is no `fastmcp.server.auth.authorization` module on the pinned release; import from `fastmcp.server.auth`.

## Transport and Authentication Preconditions

FastMCP's OAuth access-token authorization is available on HTTP transports such as SSE and Streamable HTTP. STDIO has no OAuth token mechanism: `get_access_token()` returns `None`, and FastMCP skips callable auth checks. Do not treat component `auth=` or `AuthMiddleware` as a protection boundary for an untrusted STDIO process. Secure STDIO through process ownership, OS permissions, sandboxing, and the launcher contract.

When a server has an `AuthProvider`, HTTP requests to the MCP endpoint must first carry a valid token. Transport authentication rejects invalid or missing credentials before component authorization. Callable auth checks then distinguish authenticated principals by scopes, claims, component metadata, or authoritative external policy.

Read [Auth, security, and deployment](auth-security-and-deployment.md) to select and harden the authentication provider.

## AuthCheck Model

An `AuthCheck` is a synchronous or async callable that receives `AuthContext` and returns `True` to allow or `False` to deny. Multiple checks combine with AND semantics; every check must pass.

```python
from fastmcp.server.auth import AuthContext

async def can_manage_account(ctx: AuthContext) -> bool:
    if ctx.token is None:
        return False
    subject = ctx.token.subject or ctx.token.claims.get("sub")
    return await policy.can_manage(subject, ctx.component.name)
```

Keep checks deterministic for the request, bounded, fail-closed, and free of side effects. Cache an external decision only under an explicit actor/resource/policy-version contract. Re-check authoritative mutable state immediately before a delayed mutation.

## Built-In Checks

### require_scopes

`require_scopes(*scopes)` requires every named OAuth scope.

```python
from fastmcp.server.auth import require_scopes

@mcp.tool(auth=require_scopes("read", "write"))
async def update_record(record_id: str) -> dict[str, str]:
    return await records.update(record_id)
```

Scopes are coarse transport claims. They do not prove tenant membership, object ownership, current role, or user intent.

### require_roles

`require_roles(*roles, extract=...)` requires every named role, read from the token's claims. Roles and groups are not part of OIDC, so the provider-specific claim location stays at the call site: `extract` receives the token's claims and returns the caller's roles (`realm_access.roles` on Keycloak, `roles` on Microsoft Entra, `cognito:groups` on AWS Cognito, `permissions` or a namespaced custom claim on Auth0).

```python
from fastmcp.server.auth import require_roles

@mcp.tool(auth=require_roles("admin", extract=lambda claims: claims.get("roles", [])))
async def purge_records() -> dict[str, int]:
    return await records.purge()
```

A role check is still a claim check: it proves what the token asserts, not current tenant membership or intent. Re-verify authoritative role state out-of-band for high-impact mutations.

### restrict_tag

`restrict_tag(tag: str, *, scopes: list[str]) -> AuthCheck` applies required scopes only when a component has the named tag. Components without the tag pass that specific check.

`scopes` is **required and keyword-only — it has no default**. `restrict_tag("admin")` raises `TypeError`; there is no implicit "any scope" or empty-list fallback. Name the scopes the tag demands every time.

Use it with `AuthMiddleware` for tag-based global policy:

```python
from fastmcp.server.auth import restrict_tag
from fastmcp.server.middleware import AuthMiddleware

mcp = FastMCP(
    "service",
    middleware=[
        AuthMiddleware(auth=restrict_tag("admin", scopes=["admin"])),
        AuthMiddleware(auth=restrict_tag("write", scopes=["write"])),
    ],
)
```

Tags are server-owned metadata, not user input. Define their meaning in one owner and test that transforms, providers, and versioned components preserve them.

### Combine Checks

Pass a list to component `auth=` or `AuthMiddleware(auth=...)`; FastMCP evaluates all checks with AND semantics. Sync and async checks can be mixed.

```python
@mcp.tool(
    auth=[
        require_scopes("write"),
        can_manage_account,
    ]
)
async def close_account(account_id: str) -> str:
    ...
```

Use `run_auth_checks(checks, ctx)` only when an installed extension path needs to invoke the same check contract. Do not duplicate its semantics in application code.

## Custom Checks and Errors

Factories can parameterize claim, level, tenant, or policy requirements. Return `False` for a normal denial. Raise `AuthorizationError` when a reviewed client-safe denial message is useful. FastMCP masks other exceptions, logs them internally, and treats them as denial.

```python
from fastmcp.exceptions import AuthorizationError

def require_verified_email(ctx: AuthContext) -> bool:
    if ctx.token is None:
        raise AuthorizationError("Authentication required")
    if ctx.token.claims.get("email_verified") is not True:
        raise AuthorizationError("Email verification required")
    return True
```

Do not expose database errors, policy internals, existing-object status, or sensitive claims in denial messages. Apply timeouts and circuit behavior to external async policy calls; default to denial when the policy source is unavailable unless the owner explicitly accepts fail-open risk.

### Wire-Code Translation

`fastmcp.exceptions.to_mcp_error(exc: Exception, *, default_code: int = INTERNAL_ERROR) -> MCPError` is the single owner of exception-to-wire-code mapping — in upstream's own words, the "central mapping from FastMCP's public exception types to the JSON-RPC error codes defined by the MCP spec". Request-handler adapters call it instead of hand-rolling `MCPError(code=..., ...)` per call site, so wire codes stay spec-correct and consistent across resources, prompts, and tools.

| Exception             | Wire code                                      |
| --------------------- | ---------------------------------------------- |
| Already an `MCPError` | **returned unchanged**                         |
| `NotFoundError`       | `INVALID_PARAMS` (-32602)                      |
| `DisabledError`       | `INVALID_PARAMS` (-32602)                      |
| `ValidationError`     | `INVALID_PARAMS` (-32602)                      |
| anything else         | `default_code`, i.e. `INTERNAL_ERROR` (-32603) |

The not-found mapping looks wrong until you know why: per **SEP-2164**, a request naming a component that does not exist — or that exists but is disabled — is an _invalid-params_ error, not a distinct not-found error. It matches the SDK's own `ResourceNotFoundError -> INVALID_PARAMS` mapping in `mcp.server.mcpserver`. Do not "fix" it to a not-found code.

That row matters directly for authorization, because component-level denial is expressed as not-found (see [Component-Level Authorization](#component-level-authorization)). A denied component and a genuinely absent one therefore reach the client as the same `-32602`, which is what keeps a denial from confirming that the component exists. Preserve that: a check that reports denial with its own distinguishable code re-opens the catalog-disclosure hole the not-found behavior closes.

Because an existing `MCPError` passes through unchanged, the way to choose a specific code is to **raise an exception that already carries it** rather than rewriting the mapping at the call site. Route denials and unexpected failures through this function; duplicating the mapping is how a denial in one layer starts reporting a different code than the same denial in another, and how an internal detail reaches a client through the one path that forgot to mask.

## Component-Level Authorization

The `auth` decorator option applies to tools, resources, resource templates, and prompts. Failed checks:

- filter the component from list results;
- make direct access appear not found.

```python
@mcp.resource("secret://report", auth=require_scopes("reports:read"))
async def secret_report() -> str:
    return await reports.render()

@mcp.prompt(auth=require_scopes("admin"))
def admin_prompt() -> str:
    return "Review the administrative change."
```

Not-found behavior reduces catalog disclosure. It is not sufficient application authorization if the component accepts an object or tenant identifier; validate that object against the mapped actor inside the use case.

## Server-Level AuthMiddleware

`AuthMiddleware(auth=check_or_list)` applies checks across the server. It filters list results and blocks unauthorized execution with an explicit authorization error.

```python
mcp = FastMCP(
    "service",
    middleware=[AuthMiddleware(auth=require_scopes("api"))],
)
```

Use server-level middleware for requirements shared by the complete surface. Use component `auth=` for component-specific additions. When both apply, both layers must pass.

A common layering pattern is:

1. transport `AuthProvider` validates every HTTP request;
2. one global `AuthMiddleware` requires the baseline API scope;
3. tag restrictions add coarse component classes;
4. component checks add operation-specific requirements;
5. the application use case enforces tenant/object authorization from current authoritative state.

Authorization middleware order still matters. Run it before caching or protected work. If response caching derives actor-specific results from token/context rather than explicit validated arguments, disable that cache path; middleware cache keys do not include identity by default.

## Access Tokens Inside Components

Use `CurrentAccessToken()` for a required token or `get_access_token()` for an explicitly optional path.

Installed `AccessToken` fields are:

| Field | Installed type | Guidance |
| --- | --- | --- |
| `token` | `str` | Raw bearer material; never log or return |
| `client_id` | `str` | OAuth client identity; not necessarily the end user |
| `scopes` | `list[str]` | Granted coarse permissions |
| `expires_at` | `int | None` | Installed expiry representation; the live guide currently labels it as a datetime |
| `resource` | `str | None` | Intended protected resource when available |
| `subject` | `str | None` | Authenticated subject when available |
| `claims` | `dict[str, Any]` | Validated provider claims; return only selected safe values |

Avoid returning an access-token diagnostic object to clients. Extract the narrowest claim required, map it to an application actor, and return the narrowest authorized DTO.

## AuthContext Reference

`AuthContext(token: AccessToken | None, component: FastMCPComponent)` contains:

- `token: AccessToken | None`;
- `component: FastMCPComponent` — the common base shared by tools, resources, templates, and prompts.

Component access enables name-, tag-, and metadata-aware policy. Prefer stable server-owned tags or explicit policy metadata over name-prefix parsing. Versioned components may carry different metadata; test every version.

Useful installed imports are:

```python
from fastmcp.server.auth import (
    AccessToken,
    AuthCheck,
    AuthContext,
    require_scopes,
    restrict_tag,
    run_auth_checks,
)
from fastmcp.server.middleware import AuthMiddleware
```

## Application Authorization Rules

- Map the token subject/client to an application actor through the owning identity domain.
- Ignore client-supplied organization, tenant, role, or ownership assertions unless independently validated.
- Authorize the concrete operation and object against current state.
- Bind background task create/status/result/cancel to the actor; a task ID is not authority.
- Re-authorize cross-component resource or prompt reads.
- Keep list visibility and direct execution consistent.
- Return only fields allowed for the actor and emit redacted immutable audit events for sensitive actions.
- Require confirmation separately for destructive work; authorization does not establish current intent.

## Verification

Test over an HTTP transport with an explicit auth provider and through `fastmcp.Client`. Cover:

- missing, malformed, expired, wrong-issuer, wrong-audience/resource, and insufficient-scope tokens;
- STDIO behavior showing token absence and skipped callable checks, with the external trust boundary documented;
- `require_scopes` single and multi-scope AND behavior;
- `restrict_tag` matching and non-matching components;
- sync, async, combined, external-policy, exception, timeout, and fail-closed checks;
- `AuthorizationError` client-safe messages and masking of unexpected exceptions;
- component list filtering plus direct not-found behavior;
- global middleware filtering plus explicit execution denial;
- combined global, tag, component, and application-object authorization;
- tool, resource, template, prompt, mounted, transformed, and versioned surfaces;
- actor/tenant isolation, stale membership, task access, cache isolation, and delayed side effects;
- no token, full claims, secrets, or sensitive policy detail in results, logs, metrics, caches, or errors.
