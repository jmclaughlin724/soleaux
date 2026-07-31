# Dependency Ownership

Dependencies belong in the workspace that imports them. Root dependencies are reserved for repository-wide tools executed from the root.

## Before Changing a Manifest

1. Confirm the importing workspace and whether the package is runtime, peer, optional, or development-only.
2. Search the live manifests and catalog for an existing version owner.
3. Reuse `catalog:` when the repository intentionally centralizes that external dependency; do not create a competing version literal.
4. Use `workspace:*` for an internal workspace and confirm its public export.
5. Treat an absent dependency as a package change requiring the same scope and review as the feature that needs it.

When authorized, use the repository's pnpm workflow with an exact workspace filter. Preview the target and review both manifest and lockfile changes. Do not use npm, Yarn, Bun, an ephemeral installer, or a root install to work around ownership.

## Version and Peer Contracts

- A library declares a peer only when the consumer must provide that runtime.
- Keep a development copy only when the package's own tests or build require it.
- Multiple versions are a deliberate compatibility decision, not a default.
- Update a catalog entry only after identifying every consumer affected by the shared version.

## Verification

Confirm the lockfile resolves the intended version, the importing workspace can resolve the public subpath, the package's focused task passes, and one direct consumer builds or typechecks through its normal configuration.
