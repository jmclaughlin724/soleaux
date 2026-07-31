---
name: supabase-server
description: Use for @supabase/server auth, clients, adapters, handlers, and migrations.
---

# Supabase Server

## Contract

Use this skill for server-side code that imports `@supabase/server`, its core primitives, or its framework adapters. Resolve the installed package version, use the registered `supabase-anilize-temp` MCP server for current Supabase documentation, and treat the installed package's bundled docs, exports, and source as the exact-version API authority before changing code. In implementation mode, edit only the authorized consumer and run its focused checks. In answer, review, or diagnosis mode, inspect and report without mutating.

## Use When

- Building or reviewing handlers around `withSupabase`, `createSupabaseContext`, `verifyAuth`, `verifyCredentials`, or the client factories.
- Selecting auth modes, named keys, environment configuration, CORS ownership, or Edge Function JWT settings.
- Using the Hono, H3, Elysia, or NestJS adapters, or composing cookie sessions with `@supabase/ssr`.
- Migrating a pre-v1 `@supabase/server` integration or replacing hand-built server clients with package-owned primitives.

Do not invoke this skill for generic Supabase, SQL, RLS, or schema work that does not use `@supabase/server`; use `$supabase` and, for Postgres design or tuning, `$supabase-postgres-best-practices`.

## Direct Workflow

1. Classify the request as answer, review, diagnosis, or implementation. Identify the exact handler, runtime, framework, caller identities, trust boundary, and completion evidence.
2. Inspect the named consumer first, including its complete applicable instruction chain, imports, package manifest, runtime configuration, generated database types, and focused tests. Do not infer auth intent from a neighboring route.
3. Resolve the actual installed `@supabase/server` version. Read its `package.json`, relevant export, bundled docs, and source under `node_modules/@supabase/server/`. If the package is absent, use current official package docs or source and disclose the version assumption.
4. Query the registered `supabase-anilize-temp` MCP server's `search_docs` tool for current Supabase product and platform guidance on the exact topic. If that MCP server is unavailable or its result is incomplete, disclose the limitation and fall back to targeted official Supabase documentation or version-matched package source; installed package docs and source remain authoritative for the installed API.
5. Choose the narrowest API level: root wrappers for standard Fetch handlers, `createSupabaseContext` for custom responses, core primitives for custom credential or routing flows, and a package adapter for supported frameworks. Confirm every import against the installed exports.
6. Derive `auth` from the identities the endpoint is meant to accept. For arrays, preserve deliberate order and remember that a present invalid credential rejects instead of falling through. Verify named-key selection and treat `auth: 'none'` as an explicit unauthenticated boundary.
7. Trace privilege separately from authentication. `supabase` is RLS-scoped; `supabaseAdmin` bypasses RLS. Keep admin operations minimal and separately authorized. For webhooks, verify the signature against the raw body before parsing or using privileged clients.
8. For Supabase Edge Functions using `publishable`, `secret`, or `none`, verify the function has `verify_jwt = false`; otherwise the platform may reject the request before the handler runs. Preserve platform-level JWT verification when the intended flow requires it.
9. Load the exact adapter or SSR documentation before composing middleware. Adapter ordering and re-authentication differ, and framework adapters own CORS. Let `@supabase/ssr` own cookie refresh and session lifecycle.
10. For migrations, follow the installed migration guide and update config names, auth values, context fields, imports, environment variables, and runtime checks as one contract change. Do not retain local compatibility aliases unless the user explicitly requires them.
11. Verify with the owning package's narrowest typecheck or test plus representative allowed, missing, and invalid credential paths. Exercise adapter ordering or Edge configuration when those behaviors changed. Report any check that cannot run.

## Detail Index

Installed package docs are relative to the repository root after dependency installation.

| Need | Authority |
| --- | --- |
| Current Supabase product and platform guidance | Registered `supabase-anilize-temp` MCP server's `search_docs` tool |
| Setup, wrappers, context, CORS, and Edge configuration | `node_modules/@supabase/server/docs/getting-started.md` |
| Auth modes, arrays, named keys, and v0-to-v1 renames | `node_modules/@supabase/server/docs/auth-modes.md` and `node_modules/@supabase/server/MIGRATION.md` |
| Privilege boundaries and credential verification | `node_modules/@supabase/server/docs/security.md` |
| Core primitives and custom credential flows | `node_modules/@supabase/server/docs/core-primitives.md` |
| Exports, errors, generics, and client options | `node_modules/@supabase/server/docs/api-reference.md`, `error-handling.md`, and `typescript-generics.md` |
| Environment variables and JWKS configuration | `node_modules/@supabase/server/docs/environment-variables.md` |
| Cookie-based frameworks | `node_modules/@supabase/server/docs/ssr-frameworks.md` |
| Hono, H3, Elysia, or NestJS behavior | `node_modules/@supabase/server/docs/adapters/<adapter>.md` |
| Compact fallback and migration checklist | [references/skill-playbook.md](references/skill-playbook.md) |

## Boundaries

- Treat `@supabase/server` v1.x as a versioned public-beta API; never assume the installed minor matches remembered examples.
- Do not expose secret keys, admin clients, or privileged results to untrusted callers.
- `auth: 'none'` performs no authentication and is not authorization.
- Do not let an invalid user JWT downgrade to a publishable, secret, or unauthenticated mode.
- Do not copy middleware ordering assumptions between adapters.
- Do not hand-author Supabase `Database` types; use the repository's generated type owner.
- Do not mutate a remote Supabase project or deploy an Edge Function without explicit in-scope authority and a verified target.
