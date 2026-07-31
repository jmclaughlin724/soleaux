# Monorepo Ownership

The live `pnpm-workspace.yaml`, root `turbo.json`, package manifests, and complete applicable owner instruction chain define this repository's topology. Treat examples here as a decision aid, never as permission to recreate a generic starter layout.

## Choose the Boundary

- An app is a deployable endpoint and must not become a dependency of another workspace.
- A package owns a cohesive reusable contract with at least one real consumer.
- Repository tooling belongs at the root or under the established tool owner; application dependencies belong where they are imported.
- App-local code stays with the app until concrete cross-workspace reuse justifies a package.

Before creating or moving a workspace, identify its authoritative source, direct consumers, public subpaths, task graph, cache outputs, and focused tests.

## Package Contract

- Use the repository namespace and `workspace:*` for internal dependencies.
- Export intentional subpaths. Do not add a catch-all barrel or deep-import implementation files across a workspace boundary.
- Reuse the established TypeScript configuration and package-script names.
- Choose source exports or compiled output from actual consumer/runtime needs; declare generated output in the owning Turbo task when compilation exists.
- Keep environment and secrets at the consumer boundary unless the package explicitly owns a runtime integration.

## Apply and Verify

Make the narrowest manifest or `turbo.json` change, inspect the graph with the installed Turbo CLI, and exercise the changed package plus one direct consumer. Prove that cache inputs and outputs match the real task rather than inferring correctness from a successful command.

Load only the needed detail:

- [structure.md](structure.md) for topology decisions;
- [packages.md](packages.md) for a new workspace contract; or
- [dependencies.md](dependencies.md) for dependency ownership and catalogs.
