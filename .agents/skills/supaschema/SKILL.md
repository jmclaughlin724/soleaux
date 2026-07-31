---
name: supaschema
description: Supaschema CLI reference for declarative SQL diffs, generated migrations, replay checks, generated contracts, and SUPA_* diagnostics. Use this skill for tool semantics; migration workflow policy lives in the bundled supaschema rule.
metadata:
  keywords: supaschema, schema migration, database migration, declarative SQL, migration drift, SUPA diagnostic
---

# supaschema CLI Reference

## Contract

This skill explains supaschema behavior. Use it to decode CLI commands and diagnostics, not as workflow authority. Migration policy, ordering, ownership, and stop conditions live in the bundled supaschema rule.

For source-kind and introspection boundaries, read `docs/concepts/sources.mdx` first, then the owner briefs in `src/AGENTS.md`, `src/source/AGENTS.md`, and `src/typegen/AGENTS.md`.

Do not infer lifecycle activation from package or skill availability. Inspect the consuming repository's actual `.claude/settings.json` and `.codex/hooks.json`. A registered package hook may automate `diff`, `check`, or an authorized `sync`; without that registration, use the CLI lanes directly. In either case, fix the canonical source named by a `SUPA_*` diagnostic and rerun the failing command.

## Start Here

Before the first schema edit:

1. Inspect `.supaschema/install.json` for an unresolved path handoff.
2. Read `supaschema.config.json`.
3. Resolve the configured `schemaPaths`, `sources.from`, and `migrationsDir`.
4. Inspect existing migrations for operational source intent and generated-lineage proof.

Do not create a parallel schema tree, migration directory, credential owner, or config. Never invent backfill values, secrets, predicates, conversions, or workload facts.

Load [setup-and-config.md](references/setup-and-config.md) for installation, path confirmation, baseline resolution, generated-contract settings, and apply policy. Use the installed offline docs under `node_modules/supaschema/agent-bundle/docs/` for version-specific details.

## Core CLI Sequence

This is command shape, not authorization:

1. Edit only the configured declarative tree and preserve reviewed source intent.
2. Run `supaschema diff`; review generated SQL and resolve any `SUPA_*` diagnostic at the named owner.
3. Run `supaschema check` for replay and security safety.
4. Run `supaschema types` when generated TypeScript or Zod contracts are configured.
5. Run `supaschema stage` only when the workflow calls for the generated migration closure to be staged.
6. Run `supaschema apply` only after target resolution, safety gates, and required authorization.
7. Run `supaschema verify` when a database target is available.

`supaschema sync` composes these lanes according to `workflow.migration_sync` and `sync.targets`; use focused commands when the user asks for a focused operation. Keep the schema edit, generated migration, and generated contracts in one delivery unit.

Load [workflow-and-diagnostics.md](references/workflow-and-diagnostics.md) before generating or applying a migration, resolving lineage or destructive-plan diagnostics, triaging drift, or changing Supaschema's SQL support model.

## Boundaries

- Treat SQL safety, equivalence, destructiveness, and replayability as AST/model questions, never regex questions.
- Use only configured source kinds and path owners.
- Preserve explicit data-transition intent from reviewed artifacts; do not infer it from schema shape.
- Decode diagnostics offline with `supaschema explain <SUPA_CODE>`.
- Do not edit generated migrations to silence model, lineage, or deparser findings.
