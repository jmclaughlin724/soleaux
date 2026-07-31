# Setup and Configuration

## Installation and Activation

Install the npm package through the consuming project's package manager, then run `supaschema init`. The initializer writes or repairs `supaschema.config.json`, configured schema and migration directories, and safe focused package scripts (`supaschema:diff`, `supaschema:stage`, `supaschema:types`, and `supaschema:check`). It may install package-owned `.agents`, `.claude`, and `.codex` surfaces, but package availability alone does not register a lifecycle hook: inspect the consuming repository's actual registration files.

The initializer preserves non-identical existing files and reports hook configuration it cannot merge. It does not write `AGENTS.md`, `CLAUDE.md`, backup directories, maintainer tooling, or apply-capable package scripts. Supabase inventory layouts or `_bootstrap` schema directories default schema diff and migration sync to manual, seed Supabase-managed schemas into `schemas.exclude`, and exclude bootstrap sources.

Installing the standalone Agent Skill through `npx skills` supplies portable workflow context only. It does not create project config, schema or migration directories, hooks, registrations, or passive rules. The npm package plus `supaschema init` is the project activation path.

The installed npm package includes an offline copy of the public docs under `node_modules/supaschema/agent-bundle/docs/`. Start at `agent-bundle/docs/index.md` when hosted documentation is unavailable.

## Path Confirmation

Before the first schema edit, inspect `.supaschema/install.json`:

- A normal resolved install does not leave this directory.
- When `pathConfirmationNeeded` is true, read `agentInstructions`, choose from the candidate `schemaPaths` and `migrationsDirs`, and make those paths explicit in `supaschema.config.json`.
- When no handoff manifest exists, inspect `supaschema.config.json` directly.

Do not generate from guessed paths. Use configured `schemaPaths`, `sources`, and `migrationsDir`; do not create a parallel schema tree, migration directory, credential owner, or config.

Before concluding that a migration cannot be modeled, inspect:

- `schemaPaths` for the declarative end state;
- `sources.from` for the before-state baseline;
- `migrationsDir` for existing source intent and generated-lineage proof.

Existing migrations preserve operational intent that schema shape cannot express, including data transitions, explicit DML or `DO` blocks, enum rewrites, Vault placeholders, workload-proven indexes, routine drops, and provider bootstrap constraints. Never invent missing values, secrets, predicates, conversions, or workload facts; require the canonical migration, config, hint, or workload artifact to declare them.

## Configuration Decisions

Read `supaschema.config.json` before editing. Four decisions own agent behavior:

### Schema Tree

`schemaPaths` and `migrationsDir` are the only source and output roots. Nested SQL files merge into one model. Zero-source-flag `diff`, `plan`, and `verify` target `dir:<schemaPaths[0]>`; pass `--to` for another target.

### Diff Baseline

`sources.from` owns the before state and `schemaPaths` the after state. The installed `"auto"` baseline first considers a proven staged closure at `git:INDEX`, then `git:HEAD`, and allows `empty:` only for an initial migration without an existing corpus. Any Git snapshot must agree with generated migration lineage. Explicit source kinds are `dump:`, `dir:`, `git:`, `catalog:`, `empty:`, and `database:`.

### Generated Contracts

`typesFile`, `zodFile`, `zodTypesImportPath`, `workflow.type_generation`, `workflow.zod_generation`, and `workflow.type_usage` own generated TypeScript and Zod behavior. `supaschema types` models configured tables, views, materialized views, view dependencies, functions, enums, and composites.

When a modeled relation, function, extension, or expression unexpectedly resolves to `unknown`, fix its model owner. Preserve the intentional `unknown` fallback for unsupported PostgreSQL scalars. Do not hide missing model coverage with application casts, aliases, copied contracts, or local DTOs.

### Apply Policy

`workflow.migration_sync` and `sync.targets` own target selection:

- `"auto"` permits bare `sync` to select exactly one target with `mode: "auto"`;
- `"manual"` requires `--target <name>`;
- `"disabled"` blocks apply while preserving non-mutating lanes.

Multiple automatic targets are refused because cross-target apply is not atomic. A remote target must declare and satisfy `requireApprovalEnv`. `managedSchemas`, `schemas.exclude`, `transactionMode`, optional `$ENV_NAME` references under `environments`, and the provider-neutral `adapter: "auto"` refine these decisions without granting workflow consent.
