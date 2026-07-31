# Zod Enforcement and CI

Add deterministic enforcement only for a repository invariant with an established executable owner. This checkout uses its configured Biome, ast-grep, TypeScript, and focused tests; do not introduce ESLint, `tsx`, Knip, ts-prune, Madge, or another scanner from this reference.

## Choose the Owner

- Syntax-only prohibited or required Zod forms: an owning ast-grep rule and its focused valid/invalid fixtures.
- Import, type, or unused-symbol behavior: the configured compiler or linter.
- Acceptance, rejection, transforms, and error shape: focused schema tests.
- Boundary ownership and public exports: the applicable `AGENTS.md`, package exports, and consumer tests.
- Generated schema snapshots: the existing generator, its authoritative input, and a check mode that proves the committed output is current.

Do not ban `parse()` globally: throwing parse can be correct for trusted configuration that must fail closed. Enforce the narrower boundary invariant and document intentional exceptions in the selected executable owner.

## Schema Change Gate

For a material schema change, test:

1. representative accepted input;
2. every changed rejection boundary;
3. output transforms, defaults, and stripping/strictness;
4. stable caller-visible error behavior;
5. the explicit API, provider, or persistence consumer; and
6. generated output only when a real generator owns it.

Run the narrow owner checks first, then the directly affected package typecheck and consumer test. Do not create a repository-wide snapshot or dependency graph tool merely to validate one schema.
