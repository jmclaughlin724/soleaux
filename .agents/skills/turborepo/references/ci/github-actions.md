# GitHub Actions

Start from the repository's live workflows, package-manager version owner, Node engine, lockfile, and Turbo task graph. Do not copy a generic workflow that installs a second package manager or a different runtime version.

## Job Contract

1. Check out the exact history depth required by affected comparisons.
2. Configure the repository-owned Node and pnpm versions.
3. Run `pnpm install --frozen-lockfile`.
4. Execute an existing root script or an explicit `pnpm exec turbo run` task.
5. Give the job only the secrets and permissions its task requires.
6. Upload artifacts only when another job or a human consumer needs them.

Use Turbo's affected selection only after verifying the base and head refs in pull-request, merge-queue, and default-branch events. A shallow or missing base must fail safely or broaden the run according to the workflow's contract.

For remote caching, inject provider credentials from GitHub secrets and reuse the [remote-cache contract](../caching/remote-cache.md). Keep untrusted fork workflows from receiving write-capable secrets.

## Verification

Validate the workflow syntax, inspect the Turbo dry-run for a representative change and no-op change, and confirm required checks still run for the paths that own the task.
