# @supabase/server Playbook

Load this reference when the installed package docs are unavailable, when a migration spans several API surfaces, or when a security-sensitive behavior needs a compact cross-check. It is navigation guidance, not a substitute for the version-matched package docs and source.

## Authority and Version Routing

Use this order:

1. The user's named target, repository instructions, and runtime configuration.
2. The installed `@supabase/server/package.json`, exports, bundled docs, and implementation.
3. The registered `supabase-anilize-temp` MCP server's `search_docs` tool for current Supabase product and platform guidance.
4. Version-matched official package documentation and source when the MCP result is incomplete or the exact package version requires it.
5. This playbook for routing and cross-checks.

Record the installed version before coding. The v1 line is public beta, so confirm signatures, exports, adapter behavior, environment resolution, and deprecated aliases rather than relying on a remembered minor release.

The v1 package can expose:

- Root wrappers: `withSupabase` and `createSupabaseContext`.
- Core primitives: `resolveEnv`, `extractCredentials`, `verifyCredentials`, `verifyAuth`, `createContextClient`, and `createAdminClient`.
- Framework adapters for Hono, H3, Elysia, and NestJS.
- Peer integration for Supabase clients and database generics.

Check the installed `exports` map before using any of them.

## API Level Selection

| Requirement | Prefer |
| --- | --- |
| Standard Fetch handler with package-managed auth, clients, errors, and CORS | Root `withSupabase` |
| Fetch handler with custom error response or response flow | `createSupabaseContext` |
| Nonstandard credentials, multiple route policies, or individual pipeline stages | Exports from `@supabase/server/core` |
| Supported web framework | Its package adapter |
| Cookie session and refresh-token lifecycle | `@supabase/ssr`, composed with core primitives |

The context normally contains:

- `supabase`: a caller-scoped client on which RLS applies.
- `supabaseAdmin`: a secret-key client that bypasses RLS.
- `userClaims`: normalized user claims when user auth matched.
- `jwtClaims`: verified JWT payload when user auth matched.
- `authMode`: the mode that matched.
- `authKeyName`: the named API key that matched, when applicable.

Do not return the whole context to a caller. Return the narrowest validated DTO required by the route.

## Authentication Model

| Mode | Expected credential | Typical boundary |
| --- | --- | --- |
| `user` | Bearer user JWT | Signed-in user request scoped by RLS |
| `publishable` | Publishable key in `apikey` | Identified public client or low-trust service |
| `secret` | Secret key in `apikey` | Trusted server-to-server request |
| `none` | Nothing | Truly public route or route with separate custom verification |

Named modes such as `publishable:web` or `secret:automation` select a named key. Wildcard forms may be available in the installed version. Verify their exact type and resolution rules before use.

An auth array is an ordered acceptance policy. The first valid match wins. Missing credentials may allow the next mode to be considered, but a credential that is present and invalid rejects immediately. Never add a weaker fallback merely to make a failing request pass.

Treat `none` as a high-risk decision: the handler runs for every request, while `supabaseAdmin` may still be present. If custom authentication is the reason for `none`, verify it before parsing an untrusted webhook body or performing privileged work.

## Privilege and Client Rules

- Authentication answers who or what presented a credential; authorization must still decide whether that identity may perform the operation.
- Use `supabase` for user-scoped work and rely on reviewed RLS policies.
- Use `supabaseAdmin` only for a narrowly authorized server operation. It bypasses RLS regardless of the matched auth mode.
- Keep secret keys and admin clients on trusted server surfaces.
- Pass the generated repository `Database` type to client-creating APIs where the installed signature supports it.
- Do not override verified `Authorization` or `apikey` headers through forwarded client options.
- Treat thrown client-factory configuration errors differently from result-tuple auth errors; follow the installed error-handling guide.

## Edge Functions

Supabase's platform-level JWT check runs before the function handler. A handler that intentionally accepts `publishable`, `secret`, or `none` cannot implement that policy if the platform first requires a JWT. Confirm the owning function's entry in `supabase/config.toml` uses `verify_jwt = false` for those modes.

This setting broadens what can reach the handler; it does not authenticate the request. The package auth mode or a separately verified webhook signature must enforce the intended boundary. Keep the configuration and handler policy in the same review scope.

## Adapter Ordering and CORS

Read the installed adapter page before changing middleware order.

- Hono skips when a context is already set. App-wide middleware runs before inline route middleware, so an inline middleware cannot tighten an already established app-wide context.
- H3 also skips when a context is already set, but route-level and app-wide execution order permits the route-level middleware pattern documented by the package.
- Elysia skips an already resolved context; use documented scoped groups for routes with different auth.
- NestJS guards re-run authentication. Global, controller, and handler guards execute in order, and the innermost guard can replace or tighten the context.

Framework adapters do not own CORS. Use the framework's CORS mechanism and test preflight behavior. The root Fetch wrapper has its own CORS behavior; use `createSupabaseContext` when the response must be fully custom.

## Cookie-Based Frameworks

Let `@supabase/ssr` own session cookies, refresh-token rotation, and the current access token. Feed the refreshed access token into `verifyCredentials`, then create the package clients using the verified result. Do not add a second cookie/session implementation inside `@supabase/server` middleware.

Review caching and JWKS behavior against the installed SSR guide and the deployment runtime. Process-local caches do not have identical lifetimes in long-running and serverless environments.

## Environment Cross-Check

Inspect installed environment resolution rather than assuming host behavior. Common inputs include:

- `SUPABASE_URL`.
- Singular or named plural publishable and secret key variables.
- Local JWKS JSON or a configured JWKS URL, where supported by the installed version.

Named-key configuration, singular fallbacks, malformed JSON behavior, and automatically provisioned Edge variables are version-sensitive. Confirm them in `docs/environment-variables.md` and the installed resolver source. Never log secret values while diagnosing configuration.

## v0-to-v1 Migration Checklist

Use the installed `MIGRATION.md` as the complete map. Common contract renames include:

| Previous surface           | v1 surface                           |
| -------------------------- | ------------------------------------ |
| `allow`                    | `auth`                               |
| `always`                   | `none`                               |
| `public` / `public:<name>` | `publishable` / `publishable:<name>` |
| `authType`                 | `authMode`                           |
| `claims`                   | `jwtClaims`                          |

Update both configuration and runtime checks. Also inspect imports, context destructuring, environment names, response error handling, CORS ownership, Edge `verify_jwt`, and adapter-specific context access. Deprecated aliases are a temporary bridge, not a reason to leave a partial migration.

When replacing hand-built Supabase clients:

1. Preserve the route's current caller and authorization contract.
2. Map user-scoped operations to `supabase` and explicitly justified privileged operations to `supabaseAdmin`.
3. Preserve generated database typing and required client options.
4. Remove credential parsing only after the package owns the same verified boundary.
5. Test missing, valid, malformed, expired, and wrong-mode credentials as relevant.

## Verification Checklist

- The import exists in the installed package export map.
- The handler accepts only the intended identities and rejects missing or invalid credentials correctly.
- A present invalid credential cannot fall through to a weaker mode.
- Admin operations have explicit authorization and never leak privileged clients or secrets.
- RLS-scoped operations use `supabase` and expected policies.
- Edge `verify_jwt` agrees with the handler's auth modes.
- Adapter ordering produces the intended route-specific policy.
- CORS and preflight behavior are owned by the correct layer.
- Cookie-based flows use a fresh SSR-managed token.
- Generated `Database` types flow through the client API.
- The owning typecheck or tests pass, including representative allowed and denied requests.
