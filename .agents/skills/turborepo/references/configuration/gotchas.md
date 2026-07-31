# Configuration Failure Patterns

## Root Script Bypasses Turbo

A root script that directly runs package-capable build or test logic bypasses the graph. Delegate through `turbo run <task>` and keep the implementation in the owning package script.

## Task Has No Package Script

Turbo can schedule only the task scripts that participating packages expose. Confirm the script name and filter before adding a graph edge.

## Dependency Edge Is Wrong

- `^build` means the task in dependency packages.
- `build` means the task in the same package.

Choose from the real data or artifact dependency; do not add both defensively.

## Outputs Are Missing or Too Broad

Declare only reproducible task output. Missing outputs make a cache hit useless; broad outputs restore unrelated or unstable files. Exclude framework caches that are not part of the delivered artifact.

## Environment Is Not Hashed

If an environment value changes task output, include it in the owning task's hashed environment contract. Do not put secrets in configuration or assume pass-through values affect cache keys.

## Persistent Task Is Cacheable

Long-running development tasks are persistent and non-cacheable. Their dependents must use the installed Turbo contract for persistent task relationships rather than treating the process as a completed artifact.

## Validation

Use a bounded JSON dry run, execute one miss and one hit, inspect outputs and hash inputs, and exercise one direct consumer. Fix the exact contract defect instead of disabling caching or weakening the graph globally.
