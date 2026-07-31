---
name: elegant
description: Consolidate an explicitly accepted clean-slate design into one canonical owner and migrate its consumers. Use when the user authorizes redesign, deduplication, legacy removal, or private contract replacement. Do not use for conservative cleanup, formatting, unused-file review, or behavior-preserving tidying.
---

# Elegant

## Contract

Apply only to an explicitly accepted clean-slate consolidation. Choose one canonical owner, migrate every in-scope consumer, and remove redundant private surfaces. Preserve public or external contracts unless the user explicitly replaces them; private compatibility is not a constraint.

Generic cleanup, formatting, unused-file review, or behavior-preserving tidying does not authorize this contract. Deletion requires evidence that the accepted design retires the surface.

The result must be necessary and sufficient for the requested outcome: clear, idiomatic, and no more abstract than its consumers require.

## Workflow

1. **Freeze the accepted scope.**
   - Resolve the objective, invariants, exclusions, protected contracts, canonical owners, and exact replacement authority from the request and durable task context.
   - Treat named consumers as the migration worklist. Inspect adjacent surfaces only when needed to prove ownership or completeness.
2. **Design from evidence.**
   - Read the owners, consumers, tests, manifests, and generated surfaces that define the current contract.
   - Verify version-sensitive behavior against the installed version and authoritative upstream documentation.
   - Select the narrowest canonical owner that satisfies repository constraints.
3. **Consolidate completely.**
   - Rewrite every in-scope consumer in the same change.
   - Remove redundant wrappers, aliases, schemas, types, dependencies, docs, branches, and compatibility layers after their consumers migrate.
   - Remove duplicate tests only when the canonical owner retains equivalent contract coverage.
   - Do not add a facade, shim, registry, or verification abstraction for the migration.
4. **Verify the result.**
   - Sweep removed names and paths across source, tests, manifests, owner guidance, registrations, and generated targets.
   - Run checks that prove the canonical owner, migrated consumers, and preserved or deliberately replaced contracts.
   - Diagnose and repair only failures caused by the consolidation.

## Completion

Leave the changes uncommitted. Report the canonical owner, migrated and removed surfaces, validation results, and residual risk.
