# Supabase Edge Functions

Load this rule for Supabase Edge Function implementation and review. Current official Supabase documentation, the installed CLI, and installed package exports remain authoritative. When the function uses `@supabase/server`, also invoke `$supabase-server` and verify its installed version, bundled docs, exports, and adapter behavior.

## Runtime And Imports

- Prefer Web APIs and Deno core APIs. Use Node built-ins only to fill a real gap and import them with `node:` specifiers.
- Keep each function independently deployable with its own `deno.json`. Put shared utilities in `supabase/functions/_shared/`, import them by relative path, and never import one Edge Function from another.
- Use relative paths for local code. Every external dependency import must use an explicit versioned `npm:` or `jsr:` specifier; never use a bare package specifier. Prefer these registries over `deno.land/x`, `esm.sh`, or `unpkg.com`.
- Export a module-worker `fetch` handler. Do not use `Deno.serve`, import `serve` from the Deno standard library, call `listen`, or start a Node server.
- Write ephemeral files only under `/tmp`.

Use this standard handler shape:

```ts
import { withSupabase } from "npm:@supabase/server@^1";

export default {
  fetch: withSupabase({ auth: "user" }, async (_request, ctx) => {
    const { data, error } = await ctx.supabase.from("countries").select();
    if (error) throw error;
    return Response.json({ data });
  }),
};
```

## Authentication And Privilege

Wrap standard handlers with `withSupabase`. Derive the auth mode from the actual caller:

| Caller | `auth` | Platform `verify_jwt` | Default client |
| --- | --- | --- | --- |
| Signed-in user with a bearer JWT | `'user'` | `true` (default) | `ctx.supabase` |
| Cron, worker, `pg_net`, or another trusted function | `'secret:<name>'` when a named key exists; otherwise `'secret'` | `false` | `ctx.supabaseAdmin` only for the authorized operation |
| Public application carrying a publishable key | `'publishable:<name>'` when a named key exists; otherwise `'publishable'` | `false` | `ctx.supabase` |
| Truly public endpoint or externally authenticated webhook | `'none'` | `false` | `ctx.supabaseAdmin` only after the endpoint's own authorization check |

- When a function accepts any mode other than `'user'`, add its exact `[functions.<function-name>]` entry to `supabase/config.toml` with `verify_jwt = false`; the wrapper remains responsible for the declared auth mode.
- Treat authentication and authorization as separate decisions. `ctx.supabase` is caller- or anonymous-RLS scoped; `ctx.supabaseAdmin` bypasses RLS and must be limited to the narrowest separately authorized operation.
- Use `ctx.userClaims` for verified user identity. A publishable key identifies a client application, not a user.
- Prefer named publishable and secret keys when only one caller identity should be accepted. With deliberate auth arrays, a present invalid credential rejects the request and must not downgrade to a weaker mode.
- For webhooks, verify the provider signature against the raw request body before parsing it or using an admin client.

## Routing And CORS

- A single function may own multiple routes. Prefix every route with `/<function-name>` so Supabase routing reaches it correctly.
- Prefer Hono for multiple routes. Import its Supabase middleware from the versioned `npm:@supabase/server@^1/adapters/hono` subpath and export `{ fetch: app.fetch }`; never call `app.listen`.
- Use per-route Supabase middleware when routes have different auth modes. Do not combine broad app-level Supabase middleware with stricter route middleware because the first populated Supabase context wins.
- The root `withSupabase` wrapper owns CORS and preflight responses. The Hono adapter does not; use Hono's versioned CORS middleware for Hono applications.

## Environment And Background Work

- Supabase provisions `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEYS`, `SUPABASE_SECRET_KEYS`, `SUPABASE_JWKS`, and `SUPABASE_DB_URL` locally and in hosted Edge Functions. Let `withSupabase` read the values it owns instead of rebuilding clients or reading keys by hand.
- When direct key access is unavoidable, parse the plural key map and select the required named entry. Never log keys or return them to callers.
- Set additional secrets with `supabase secrets set --env-file <path>` and keep secret env files out of version control.
- Schedule response-independent work with the static `EdgeRuntime.waitUntil(promise)` API. Handle background errors inside the scheduled promise, and do not assume `waitUntil` is present on the request or handler context.

## Boundary And Verification

- Treat every request and response as an API contract. Validate untrusted input, authorize separately, and return the narrowest validated DTO.
- Run the changed function's narrowest owned typecheck and tests, then exercise the allowed, missing-credential, and invalid-credential paths.
- Verify `verify_jwt`, RLS-versus-admin behavior, CORS preflight ownership, webhook signature failure when applicable, and background task handoff before completion.
