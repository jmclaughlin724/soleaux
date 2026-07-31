# Database: Declarative Database Schema

Load this rule for Supabase declarative schema implementation or review. Current official Supabase documentation, the installed CLI, the live project configuration, and the complete applicable owner instruction chain remain authoritative.

## Exclusive Declarative Ownership

- Follow the repository's [Supabase Schema Ownership](../../../../supabase/AGENTS.md) owner for database schema and generated-type locations.
- Update the corresponding existing declarative owner for each table, view, function, policy, grant, or other database entity. Create a new ordered schema file only when no existing file owns it.
- Keep every schema file accurate to the desired final state.
- Migration file creation is CLI-only. Never invent a timestamp or create a path under `supabase/migrations/` with an editor, patch, shell redirection, or direct filesystem write.
- Do not hand-edit migrations for ordinary schema changes. Generate them through the CLI from the declared state. A CLI-created versioned migration for a known diff-engine caveat is the only content-authoring exception, and a committed migration is immutable.

## Schema Ordering

- Schema files execute in lexicographic order unless the live project configuration declares a more specific order. Place dependencies before their consumers.
- When adding columns, append them to the end of the table definition to avoid unnecessary or order-sensitive diffs.

## Migration Generation

Stop the local Supabase development environment before generating a migration:

```bash
supabase stop
```

Generate the migration by diffing the migration history against the declared final state:

```bash
supabase db diff -f <migration_name>
```

Use a descriptive lowercase `snake_case` migration name. Review the generated SQL and confirm it contains only the intended incremental change before applying or committing it.

## Rollback

To roll back a schema change:

1. Restore the desired final state in the owning files under `supabase/schemas/`.
2. Stop local Supabase.
3. Generate a new forward migration:

   ```bash
   supabase db diff -f <rollback_migration_name>
   ```

4. Review the generated migration carefully for destructive operations and data-loss risk.

Never amend a deployed migration.

## Diff-Engine Caveats

The schema diff does not reliably capture every PostgreSQL change. Only for the cases below, create a new versioned migration with `supabase migration new <migration_name>` and edit that new file instead of relying on `supabase db diff`:

- DML statements, including `insert`, `update`, and `delete`.
- View owners and grants, `security_invoker` changes, materialized views, and view recreation after an underlying column type changes.
- `alter policy` statements and column privileges.
- Schema privileges, because schemas are diffed separately.
- Comments and partitions.
- `alter publication ... add table ...` statements.
- `create domain` statements.
- Grants that the diff duplicates from default privileges.

Use exactly the path printed by `supabase migration new`; never preselect or recreate its timestamped filename. Prefer an existing declarative configuration or seed owner when the product already provides one, such as `[storage.buckets]` plus `supabase seed buckets` for Storage bucket configuration.

## Verification

Run the project-owned local database reset, schema-drift check, migration check, type generation, database advisors, and database tests that exercise the change. Resolve each command from the live repository instead of inventing a generic root command.
