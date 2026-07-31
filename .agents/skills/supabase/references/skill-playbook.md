# Supabase Development Playbook

Load this reference for Supabase-specific product procedures. Current official documentation and the live project remain authoritative.

## Current Sources And Verification

1. Retrieve the exact topic with the registered `supabase-anilize-temp` MCP server's `search_docs` tool.
2. Fetch `https://supabase.com/changelog.md` and inspect relevant `breaking-change` entries. If the MCP server is unavailable or its result is incomplete, disclose the limitation and fall back to the targeted official `.md` docs page, then official Supabase web search.
3. Inspect the installed package or CLI version and the live command surface before choosing an API or command.
4. Verify the changed behavior at its owning boundary. Database changes need a focused query or schema check; client, Auth, Storage, Realtime, and Edge Function changes need the relevant application test or request flow.

## Security And Data API

### Data API Access

New tables are not necessarily exposed through the Data API. Check the project's Data API settings and explicit privileges for `anon` or `authenticated`. Privileges determine whether a role can reach a table; RLS determines which rows that role can read or change. When granting a public client role access to a table, enable RLS and add policies matching the real access model.

Enable RLS on every table in an exposed schema, including `public` by default. Prefer RLS as defense in depth for private schemas. Do not copy one ownership predicate to every table without checking its domain model.

### Auth And Sessions

- Never authorize from `raw_user_meta_data` or user-editable `user_metadata`. Put authorization claims in `raw_app_meta_data` or `app_metadata`.
- Claims read through `auth.jwt()` can remain stale until the token refreshes.
- Deleting a user does not by itself invalidate every issued access token. Revoke or sign out sessions first when immediate invalidation matters, keep expiry proportionate to risk, and validate session state for the most sensitive operations.
- Keep secret and `service_role` keys out of browsers and public clients. Any `NEXT_PUBLIC_` variable is browser-visible in Next.js.

### RLS, Views, And Privileged Code

- Views can bypass underlying RLS. On Postgres 15 or newer, prefer `security_invoker = true`; otherwise revoke public client access or keep the view in an unexposed schema.
- An `UPDATE` policy also needs rows to be visible through a `SELECT` policy. A missing `SELECT` policy can look like a successful update that affected zero rows.
- Prefer policy `TO` clauses over `auth.role()`. `TO authenticated` authenticates a role but does not authorize access to a user's rows; pair it with the required ownership or tenancy predicate.
- For ownership-preserving updates, define both `USING` and `WITH CHECK` so a caller cannot move a row outside the authorized ownership boundary.
- `SECURITY DEFINER` bypasses RLS under the function owner's privileges. Do not add it merely to make a permission error disappear. When it is genuinely required, keep the function outside exposed schemas, set a safe `search_path`, perform explicit authorization inside the function, and revoke default `PUBLIC` execute privileges before granting the narrowest required role.
- Storage upsert needs `INSERT`, `SELECT`, and `UPDATE` access.
- Pin installed Supabase package versions through the repository's package manager and commit the lockfile.

After schema or policy changes, run the installed CLI's database advisors or the available MCP advisor tool when present, then exercise both allowed and denied cases.

## Supabase CLI

Discover commands from the installed CLI:

```bash
supabase --version
supabase --help
supabase <group> --help
supabase <group> <command> --help
```

Do not keep fixed minimum-version claims in the workflow. If a documented command is absent, use the installed help and current CLI docs to choose a supported alternative such as the available MCP operation or `psql`.

For imperative migration projects, create the migration through `supabase migration new <name>` before editing the exact returned path. Do not invent a timestamped filename, create a migration with a patch or redirection, or recreate a CLI-generated path manually.

## Supabase MCP Server

Use the current Supabase MCP setup guide for the supported server URL and authentication flow. When tools are missing:

1. Inspect the active client's MCP registry rather than assuming a configuration filename.
2. Confirm the configured server URL against the current setup guide.
3. Check reachability without sending secrets. An unauthenticated `401` can establish that an HTTP endpoint is reachable; a timeout or refused connection cannot.
4. Complete the client's OAuth flow and restart or reload only when that client requires it.

Do not add a hard MCP dependency to `agents/openai.yaml` unless the same dependency name is registered in the repository's live MCP registry.

## Schema Workflows

### Declarative Schemas

When `supabase/schemas/` exists or `supabase/config.toml` defines `schema_paths`, load and follow [Declarative database schema](../rules/declarative-database-schema.md). That rule owns the declarative workflow, ordering, migration generation, rollback, review, and diff-engine caveats.

### Imperative Migrations

Use this route when no declarative schema owner exists:

1. Create a migration with `supabase migration new <name>` and edit that file, or iterate against a disposable local database and generate the final migration through the installed CLI's supported diff or pull workflow.
2. Do not use `apply_migration` for local iteration; it records migration history and can make later diffs empty or conflicting.
3. Run advisors when available, review security-sensitive SQL, and verify local migration history through the installed command surface.

### Remote Safety

Before any linked or remote action, resolve the project reference, environment, and database identity from live configuration or tool output. Prefer dry-run or local validation first. Never infer production-write authority from permission to edit local migration files.
