---
name: supabase-postgres-best-practices
description: Use for Postgres query, schema, RLS, connection design, review, and tuning.
---

# Supabase Postgres Best Practices

## Contract

Use this skill for Postgres design, review, diagnosis, and performance work, including Supabase-backed applications. Inspect the live schema, query, configuration, version, data shape, and owning migration workflow before recommending a change. Use the registered `supabase-anilize-temp` MCP server for current Supabase Postgres documentation, load only the scenario-matched references, preserve correctness and security before performance, and verify implementation work with focused evidence from the actual workload.

## Use When

- Scaffolding a new Postgres or Supabase database, schema, or application data layer.
- Writing or reviewing queries, tables, constraints, indexes, RLS policies, or privileges.
- Diagnosing slow queries, connection exhaustion, blocking, deadlocks, vacuum behavior, or database configuration.
- Designing pooling, pagination, batch operations, JSONB access, or full-text search.

Do not invoke this skill solely because an application uses the word “optimize” when no Postgres boundary is involved.

## Direct Workflow

1. Classify the request as review, diagnosis, or implementation. Name the exact query, schema, policy, connection, configuration, or workload boundary and the evidence that will prove completion.
2. Inspect the named target first, then the installed Postgres and Supabase versions, relevant DDL, query parameters, cardinality and selectivity, current plan or metrics, owning migration workflow, consumers, and focused checks.
3. Query the registered `supabase-anilize-temp` MCP server's `search_docs` tool for current Supabase Postgres guidance on the exact topic. If that MCP server is unavailable or its result is incomplete, disclose the limitation and fall back to official Supabase documentation; use version-matched official PostgreSQL documentation for engine behavior that Supabase documentation does not cover.
4. For SQL authoring or review, load [rules/sql-style.md](rules/sql-style.md). Then select the narrowest row from the Reference Loading Guide that covers the task. Read every listed rule that could materially affect the decision; do not load the entire reference tree.
5. Establish a baseline before optimizing. Use plain `EXPLAIN` when execution is unsafe. Use `EXPLAIN (ANALYZE, BUFFERS)` only for a safe `SELECT` or an isolated test workload because `ANALYZE` executes the statement.
6. Choose the narrowest correct change. Consider data integrity, tenant isolation, write amplification, index size, locks, rollout order, pooler mode, and operational reversibility before expected speedup.
7. In implementation mode, edit the canonical schema or migration owner through the project's established workflow. Do not mutate a linked or production database without explicit authority and verified target identity.
8. Verify with the narrowest applicable plan comparison, database test, migration check, advisor, or application test. Measure representative data when making performance claims and report any unavailable evidence.
9. Return the outcome, references loaded, files changed when authorized, before-and-after evidence, material caveats, and remaining risk.

## Detail Index

### SQL Authoring — Baseline

[Postgres SQL style](rules/sql-style.md) owns reusable formatting, naming, table, column, query, alias, join, and CTE conventions. Local schema, key, and migration contracts remain authoritative.

### Reference Loading Guide

- Greenfield schema or Supabase backend: [schema-primary-keys.md](references/schema-primary-keys.md), [schema-data-types.md](references/schema-data-types.md), [schema-constraints.md](references/schema-constraints.md), [schema-lowercase-identifiers.md](references/schema-lowercase-identifiers.md), [security-rls-basics.md](references/security-rls-basics.md), and [security-privileges.md](references/security-privileges.md)
- Slow query or index review: [monitor-explain-analyze.md](references/monitor-explain-analyze.md), [query-missing-indexes.md](references/query-missing-indexes.md), [query-composite-indexes.md](references/query-composite-indexes.md), [query-covering-indexes.md](references/query-covering-indexes.md), [query-partial-indexes.md](references/query-partial-indexes.md), and [query-index-types.md](references/query-index-types.md)
- RLS or tenant security: [create-rls-policies.md](rules/create-rls-policies.md), [security-rls-basics.md](references/security-rls-basics.md), [security-rls-performance.md](references/security-rls-performance.md), and [security-privileges.md](references/security-privileges.md)
- Connections, poolers, or serverless: [conn-pooling.md](references/conn-pooling.md), [conn-limits.md](references/conn-limits.md), [conn-idle-timeout.md](references/conn-idle-timeout.md), and [conn-prepared-statements.md](references/conn-prepared-statements.md)
- Blocking, deadlocks, or work queues: [lock-short-transactions.md](references/lock-short-transactions.md), [lock-deadlock-prevention.md](references/lock-deadlock-prevention.md), [lock-skip-locked.md](references/lock-skip-locked.md), and [lock-advisory.md](references/lock-advisory.md)
- N+1, writes, or pagination: [data-n-plus-one.md](references/data-n-plus-one.md), [data-batch-inserts.md](references/data-batch-inserts.md), [data-upsert.md](references/data-upsert.md), and [data-pagination.md](references/data-pagination.md)
- Vacuum, query history, or live diagnostics: [monitor-explain-analyze.md](references/monitor-explain-analyze.md), [monitor-pg-stat-statements.md](references/monitor-pg-stat-statements.md), and [monitor-vacuum-analyze.md](references/monitor-vacuum-analyze.md)
- JSONB or search: [advanced-jsonb-indexing.md](references/advanced-jsonb-indexing.md) and [advanced-full-text-search.md](references/advanced-full-text-search.md)

Maintainers only: [_sections.md](references/_sections.md), [_template.md](references/_template.md), and [_contributing.md](references/_contributing.md).

## Boundaries

- Treat reference impact numbers as hypotheses, not guarantees. Require representative plans or measurements before claiming a speedup.
- Do not add an index to every filtered or joined column; account for selectivity, existing index prefixes, write cost, storage, and workload frequency.
- Do not change hosted Postgres settings with self-managed commands. Use current Supabase-supported configuration surfaces and the required project privileges.
- `EXPLAIN ANALYZE` executes its statement; never run it on unsafe DML or an unapproved production workload.
- Do not weaken RLS, privileges, constraints, or durability to improve a benchmark.
- Do not hand-edit generated schema output when a declarative owner exists or perform remote writes without explicit authority.
