---
name: turborepo
description: Configure and debug Turborepo task graphs, caching, and affected runs.
---

# Turborepo

## Contract

Use this skill for Turborepo task graphs, caching, filters, affected runs, environment inputs, package boundaries, and CI. Confirm the installed Turbo version, root `turbo.json`, workspace manifests, and live scripts before applying reference examples.

## Use When

- Configuring `turbo.json`, tasks, `dependsOn`, outputs, filters, or affected runs.
- Diagnosing cache misses, remote caching, environment hashing, or CI behavior.
- Creating packages or enforcing monorepo dependency boundaries.

## Direct Workflow

1. Identify the exact task, package, dependency edge, cache input, and owner files.
2. Confirm the installed Turbo version and inspect current task definitions and package scripts.
3. Read only the matching focused owner from the Detail Index: configuration, filtering, caching, environment, packages, watch, boundaries, CLI, or CI.
4. Prefer package-owned tasks and let the root manifest delegate through `turbo run`.
5. Make the narrowest change in `turbo.json` or the owning package manifest.
6. Use `turbo --dry=json`, a bounded filter, or the narrowest affected command to inspect the graph before expensive work.
7. Validate the changed task and one directly affected consumer, then report graph or cache behavior and remaining blockers.

## Detail Index

- Configuration and task graph: [configuration](references/configuration/RULE.md)
- Caching and environment inputs: [caching](references/caching/RULE.md) and [environment](references/environment/RULE.md)
- Filters, CI, and CLI: [filtering](references/filtering/RULE.md), [CI](references/ci/RULE.md), and [CLI](references/cli/RULE.md)
- Packages, watch mode, and boundaries: [best practices](references/best-practices/RULE.md), [watch](references/watch/RULE.md), and [boundaries](references/boundaries/RULE.md)

## Boundaries

- Do not hard-code an upstream Turbo version; use the installed version and its documentation.
- Do not put package-capable task logic in the root manifest.
- Do not use `turbo` shorthand in committed scripts or CI when `turbo run` is the stable form.
- Do not infer cache correctness from a successful task alone; inspect inputs, outputs, and hashes.
- Treat paths and package names in references as examples, not current repository owners.
