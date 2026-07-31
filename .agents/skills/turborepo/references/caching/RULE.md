# Turbo Cache Contract

A cache hit is correct only when the task's declared inputs, environment, and dependency outputs fully determine its result.

## Inspect First

Use the installed Turbo CLI and root `turbo.json` to inspect the exact task, package, inputs, outputs, dependencies, and environment. Compare a hit and miss with the same bounded package filter before changing configuration.

## Declare the Contract

- `dependsOn` expresses task ordering and upstream package work.
- `inputs` narrows or extends the default source and manifest inputs.
- `outputs` lists reproducible files or directories restored by the cache.
- `env` lists environment values that change the output.
- `passThroughEnv` exposes values without hashing them and therefore requires a deliberate correctness decision.
- Persistent development tasks are not cacheable.

The pnpm lockfile and owning package manifest are dependency inputs. Do not generalize another package manager's lockfile behavior into this repository.

## Diagnose

1. Reproduce with one task and one package.
2. Inspect the dry-run/task summary and input hashes.
3. Identify the missing or unstable input instead of disabling caching broadly.
4. Confirm outputs exist on a miss and are restored on a hit.
5. Verify one direct consumer when an upstream package output is involved.

Use [gotchas.md](gotchas.md) for focused failure patterns and [remote-cache.md](remote-cache.md) only when remote caching is actually in scope.
