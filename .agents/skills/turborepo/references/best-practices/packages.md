# Internal Package Contract

Create a package only when a cohesive contract has a real cross-workspace consumer or an established repository owner requires the boundary.

## Required Decisions

- Package name and existing namespace.
- Source owner and direct consumers.
- Public subpaths and whether source or compiled artifacts are exported.
- Internal and external dependency ownership.
- Package scripts, Turbo task edges, cache inputs, and outputs.
- Focused tests and the consumer check that proves delivery.

## Minimal Shape

Follow a neighboring package with the same runtime role. Reuse its TypeScript configuration, module format, script names, and export style. A typical internal dependency uses the workspace protocol:

```json
{
  "dependencies": {
    "@anilize/example": "workspace:*"
  }
}
```

Expose deliberate subpaths rather than a broad schema, utility, or component barrel:

```json
{
  "exports": {
    "./contract": "./src/contract.ts"
  }
}
```

Compiled packages must own their build output and declare it in the matching Turbo task. Source-exported packages must be supported by every real consumer's toolchain.

## Verification

Inspect the Turbo graph, run the package's narrow typecheck or test, and run one direct consumer through its normal configuration. Confirm no consumer reaches into `src/` or relies on an undeclared transitive dependency.
