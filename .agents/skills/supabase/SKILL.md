---
name: supabase
description: Use for Supabase apps, schemas, Auth, CLI, MCP, debugging, and security.
---

# Supabase

## Contract

Use this skill for Supabase product, client, CLI, MCP, schema, migration, and security work. Establish the exact product boundary, local project state, installed versions, canonical owner, and live consumer; use the registered `supabase-anilize-temp` MCP server for current Supabase documentation and verify changeable claims against the current Supabase changelog. In implementation mode, make only the authorized local change and complete focused verification. In answer, review, or diagnosis mode, inspect and report without mutating.

## Use When

- Working with Supabase Database, Auth, Edge Functions, Realtime, Storage, Vectors, Cron, Queues, or supported extensions.
- Using `supabase-js`, `@supabase/ssr`, the Supabase CLI, or the Supabase MCP server.
- Debugging sessions, JWTs, cookies, API keys, Data API access, RLS, views, or privileged database functions.
- Changing declarative schemas, imperative migrations, database configuration, or project security.

For generic Postgres design or performance work, also invoke `$supabase-postgres-best-practices` and load only its scenario-matched references.

## Direct Workflow

1. Classify the request as answer, review, diagnosis, or implementation. Name the exact Supabase product, target environment, closed file or system scope, consumer-visible outcome, approval boundary, and completion evidence.
2. Inspect the named target first. Then inspect only the owning project configuration, installed package and CLI versions, schema workflow, directly affected consumers, and focused checks needed to understand it.
3. Query the registered `supabase-anilize-temp` MCP server's `search_docs` tool for the exact current Supabase topic, then fetch `https://supabase.com/changelog.md` and scan only for relevant breaking changes. If that MCP server is unavailable or its result is incomplete, disclose the limitation and fall back to the targeted official `.md` docs page, then official Supabase web search. Do not implement from model memory alone.
4. Load [rules/nextjs-auth-ssr.md](rules/nextjs-auth-ssr.md) for Next.js App Router Supabase Auth SSR implementation or review. Load [rules/declarative-database-schema.md](rules/declarative-database-schema.md) for declarative schema implementation or review. Load [rules/edge-functions.md](rules/edge-functions.md) for Edge Function implementation or review, and also invoke `$supabase-server` when the function uses `@supabase/server`. Load [references/realtime.md](references/realtime.md) for Realtime design, implementation, review, migration, or debugging. Also load [references/skill-playbook.md](references/skill-playbook.md) when the task needs Supabase-specific security, Data API, CLI, MCP, documentation, or imperative migration guidance. Load [references/skill-feedback.md](references/skill-feedback.md) only when the user reports a problem with this skill.
5. Before a schema change, determine whether `supabase/schemas/` or `schema_paths` makes the project declarative. For a declarative project, follow the declarative database rule; otherwise create and edit an imperative migration through the installed CLI surface.
6. Before any remote mutation, resolve the target project and environment from live configuration or tool output and obtain any authority the action requires. Keep local iteration separate from remote migration history.
7. Apply security controls at the actual boundary: validate input, authorize separately, keep secrets server-side, distinguish Data API privileges from RLS, and review policies or privileged functions against the intended access model.
8. Verify with the narrowest behavior-owning check. Use a test query only when database behavior changed; use the relevant app test for client or Auth behavior; run advisors when available for schema or security changes. Report unavailable checks precisely.
9. If an approach fails two or three times, stop retrying it. Reinspect the error, current docs, versions, and logs, choose a materially different route, or report the blocker.

## Detail Index

| Need | Reference |
| --- | --- |
| Next.js App Router Auth SSR clients, cookie adapters, Proxy refresh, caching, and authorization | [rules/nextjs-auth-ssr.md](rules/nextjs-auth-ssr.md) |
| Declarative schema ownership, migration generation, rollback, ordering, and diff caveats | [rules/declarative-database-schema.md](rules/declarative-database-schema.md) |
| Edge Function runtime, imports, routing, auth selection, secrets, and background work | [rules/edge-functions.md](rules/edge-functions.md) |
| Realtime channels, Broadcast, Presence, authorization, and lifecycle | [references/realtime.md](references/realtime.md) |
| Security, Data API, CLI, MCP, docs, and schema workflows | [references/skill-playbook.md](references/skill-playbook.md) |
| User-reported skill defect or missing guidance | [references/skill-feedback.md](references/skill-feedback.md) |

## Boundaries

- Discover the installed CLI surface with `--help`; do not guess commands or rely on fixed version thresholds.
- Never expose a secret or `service_role` key in a public client.
- Do not treat role grants and RLS as interchangeable. Grants control API reachability; RLS controls which rows an allowed role can access.
- Never invent a migration timestamp or create a migration file with an editor, patch, redirection, or direct filesystem write. In a declarative project, generate ordinary migrations with `supabase db diff -f <name>`. For a documented diff-engine caveat only, let `supabase migration new <name>` create the file before editing that returned path.
- Do not use `apply_migration` for iterative local schema work or hand-edit an ordinary generated migration when a declarative schema owner exists.
- Do not mutate a linked or production project without explicit in-scope authority and verified project identity.
- Creating a GitHub feedback issue is an external write and requires the user's permission.
