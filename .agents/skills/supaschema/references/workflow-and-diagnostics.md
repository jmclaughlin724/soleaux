# Workflow and Diagnostics

This reference describes CLI semantics. Follow the consuming repository's bundled Supaschema rule for policy, ordering, ownership, authorization, and stop conditions.

## Generate a Migration

Edit the configured declarative schema tree, preserving explicit data movement, secret, and workload intent from the existing migration corpus or another reviewed owner. Then run:

```bash
supaschema diff
```

Zero-source-flag defaults print the resolved `sources.from` and `dir:<schemaPaths[0]>` targets to stderr. A generated file is written without clobbering as `<UTC timestamp>_<derived name>.sql` under `migrationsDir`; use `--name <snake_case>` only when the human requests a name. Named or file-output empty plans fail with `SUPA_DIFF_EMPTY_PLAN`.

Important blocking diagnostics:

- `SUPA_PLAN_DESTRUCTIVE_HINT_REQUIRED`, `SUPA_PLAN_COLUMN_ALTER_HINT_REQUIRED`, `SUPA_PLAN_VIEW_REPLACE_INCOMPATIBLE`, `SUPA_PLAN_ROUTINE_RETURN_TYPE_CHANGED`: review the rendered blocked section, add only the exact object key to `hints.destructive`, and regenerate. Never commit `"*"`.
- `SUPA_ROUTINE_DEPENDENCY_PROOF_REQUIRED`, `SUPA_PLAN_COLUMN_DEPENDENT_REWRITE_REQUIRED`: rewrite the dependent routine, view, policy, or trigger so dependency proof is structural; otherwise split or explicitly author the reviewed migration.
- `SUPA_PLAN_DATA_TRANSITION_REQUIRED`: a destructive hint is not backfill intent. Add reviewed DML or a `DO` transition to the migration corpus, or use an explicit migration that passes `check` and `verify`.
- `SUPA_DIFF_LINEAGE_BROKEN`: resolve a source-backed post-migration baseline.
- `SUPA_DIFF_LINEAGE_DUPLICATE`: apply or remove the pending transition instead of regenerating it.
- `SUPA_DIFF_REPLACE_*`: replacement is limited to an unapplied generated migration under `migrationsDir` whose lineage baseline matches `--from`; otherwise create a forward migration.
- `SUPA_DIFF_GENERATED_CONTRACT_DIRTY`, `SUPA_DIFF_MIGRATIONS_DIRTY`: close the proven staged closure or repair files dirty against another baseline.
- `SUPA_DIFF_CONFIG_DIRTY`, `SUPA_DIFF_SCOPED_DIRTY_SCHEMA`: close the global migration unit or run an unscoped diff that owns those changes.
- `SUPA_MIGRATION_BASELINE_FORMAT_DRIFT`: review the SQL normally; the next versioned lineage re-establishes comparable proof. Do not bypass or edit generated SQL to silence it.
- `SUPA_MIGRATIONS_STALE_BASELINE`: after review, prune through `supaschema migrations --prune-stale` with a resolved target; use `--force` only after explicit review.

Declare renames as exact `{ "from": "<key>", "to": "<key>" }` records under `hints.renames`; Supaschema never infers them.

## Check, Generate Contracts, Stage, Apply, and Verify

1. `supaschema check` validates every SQL file under `migrationsDir`, including generated and hand-authored migrations. `--changed`, `--staged`, `--base <ref>`, and `--since <ref>` intentionally select Git subsets. Checks cover replay safety, same-file forward references, `SECURITY DEFINER` search paths, and public-schema function execution exposure.
2. `supaschema types` refreshes configured TypeScript and Zod contracts. Fix missing modeled facts at their source and preserve intentional unsupported-scalar `unknown` output.
3. `supaschema stage` stages changed migration files carrying the Supaschema lineage marker and leaves unrelated files untouched.
4. `supaschema apply` applies already-generated pending migrations through the configured runner. When a selected Supabase CLI target has no database URL, that CLI owns historical pending selection; Supaschema replay-checks lineage files rather than treating every disk migration as pending.
5. `supaschema verify` executes the newest pending migration by default and compares the result to the configured target model. Use `--migration <file>` for a specific migration, `--ensure-roles` for absent provider roles, and `--ensure-environment` for required provider surfaces.

Database URL precedence is `--database-url` (including `$ENV`) > the named environment selected by global `--env` > `SUPASCHEMA_DATABASE_URL` > the nearest `supabase/config.toml`.

Keep a schema edit, generated migration, and generated contracts in one delivery unit. Do not wait for deployment or use introspection-based type generation as a substitute.

## Operational Sync

`supaschema sync` composes target policy, history reconciliation, source resolution, diff generation, replay checks, generated contracts, schema-closure staging, source deploy-safety gates, runner application, and final reconciliation. It refreshes contracts and stages the closure even when no migration is pending.

Use focused commands only when the user asks for an explicit lane. Bare `sync` can select one automatic target only. Manual and disabled workflow modes retain their configured restrictions, and remote automatic targets require their runtime approval variable.

## Drift and Coverage

```bash
supaschema diff --fail-on-diff --quiet
```

Exit 0 means parity; exit 3 means drift. Decode a blocker with `supaschema explain <SUPA_CODE>`. For broader triage:

- `supaschema diff --summary` groups operations and diagnostics by kind and schema;
- `supaschema diff --write-hints <file>` writes a no-clobber destructive-hint skeleton;
- `supaschema audit --from <source> [--json]` reports model coverage and out-of-contract statements;
- `supaschema selfcheck` proves cross-lane identity parity by re-extracting rendered live-catalog SQL;
- `supaschema migrations` classifies disk migrations as applied, pending, ghost, or out of order.

## SQL Model Boundaries

Treat DDL meaning as an AST/model problem. Supaschema implementation work must use PostgreSQL parse trees and structured model helpers, not regex, to decide safety, equivalence, destructiveness, or replayability. Regex is limited to outer transport such as markers, payload headers, and redaction.

Routine bodies contribute structural dependency proof where supported. Dynamic SQL, partial PL/pgSQL, and unsupported languages block related relation or type changes until the routine is rewritten or the migration is explicitly reviewed. A support claim must be represented in `src/sql/support.ts`, extraction, live catalog extraction when relevant, planning, rendering, checking, audit reporting, and focused tests. Unsupported boundaries belong in `unsupportedStatementSupport`.

Known `pgsql-deparser` gaps are fidelity contracts in `src/sql/support.ts`. Fix new `SUPA_CHECK_DEPARSE_*` or `SUPA_NORMALIZE_*` findings in the model/render/deparser owner or document a genuine unsupported boundary; do not edit generated migrations. Unsupported or ambiguous DDL must produce a `SUPA_*` diagnostic.

Declarative sources are `dir:`, `git:`, `database:`, `dump:`, and `catalog:` plus reviewed `empty:`. Data statements, backfills, enum rewrite recipes, Vault references, and workload-derived indexes live outside schema shape but inside the source-intent corpus. Mine reviewed evidence before blocking or author an explicit migration that passes `check` and `verify`.

With `transactionMode: "per-migration"`, `CREATE INDEX CONCURRENTLY` is blocked. It can split into a `.concurrent.sql` companion only in an explicit `per-statement` lane.
