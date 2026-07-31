---
name: zod
description: Design, test, and debug Zod v4 schemas, parsing, errors, and inference.
---

# Zod

## Contract

Use this skill for Zod v4 schema design, parsing, inference, refinements, transforms, error handling, migrations, and focused schema behavior tests. Confirm the workspace's installed version, boundary owner, and owning test conventions first. Load only the rule or reference needed for the active decision; project source and current upstream Zod documentation win over remembered v3 behavior.

## Use When

- Validating untrusted data at an API, form, environment, database, or external service boundary.
- Debugging parsing, inference, coercion, refinement, transform, or error output.
- Migrating schema code from an older Zod API.
- Adding, reviewing, or characterizing schema acceptance, rejection, errors, or transforms.

Use plain TypeScript when no runtime validation boundary exists.

## Direct Workflow

1. Identify the untrusted boundary, schema owner, input and output types, consumer-visible error contract, installed Zod version, and directly affected consumers.
2. Classify the task as schema design, debugging, migration, or behavior testing. Read the narrowest applicable file under `rules/`; load a file under `references/` only for broader API, architecture, or testing detail.
3. Choose the simplest schema that expresses the runtime contract. Derive schema-owned TypeScript types from the schema instead of duplicating them.
4. Match synchronous or asynchronous parsing to the schema's refinements and transforms. Keep validation at the boundary and pass typed data inward.
5. For behavior tests, read the boundary consumer, nearest representative test, and owning command. Cover accepted, rejected, boundary, optional, nullable, unknown-field, and transformed-output behavior required by the contract.
6. Prefer table-driven `safeParse()` or `safeParseAsync()` assertions. Assert only the stable error code, path, message, or formatted shape the consumer observes; test thrown errors only when throwing is the intentional contract.
7. Preserve the consumer's error shape and sensitive-data boundary. For migrations, change one API family at a time and verify behavior against the installed version and current official documentation.
8. Run the narrowest owning test target and typecheck, then inspect the diff for unintended contract changes.

## Detail Index

- `references/schema-types.md`: primitives, formats, enums, and dates.
- `references/parsing-and-inference.md`: parsing, coercion, and inferred types.
- `references/objects-and-composition.md`: objects, unions, and composition.
- `references/refinements-and-transforms.md`: refinements, transforms, pipes, and defaults.
- `references/error-handling.md`: error customization and formatting.
- `references/testing.md`: schema behavior cases, assertions, boundary tests, and drift checks.
- `references/advanced-features.md`: codecs, brands, registries, and JSON Schema.
- `references/boundary-architecture.md`: framework boundary patterns.
- `references/linter-and-ci.md`: static checks and drift detection.
- `references/anti-patterns.md`: common failure modes.
- `rules/*.md`: narrow decision rules with examples.

## Boundaries

- Do not treat a skill reference as proof of the installed API surface.
- Do not duplicate database-generated contracts with parallel handwritten schemas or types unless the active boundary requires a distinct runtime shape.
- Do not expose raw input values through validation errors or telemetry.
- Test observable parsing and boundary behavior, not private schema internals such as `._def`.
- Keep schema tests in the owning workspace; do not add fixture, generator, snapshot, or property-testing dependencies unless the workspace already owns the pattern or the task explicitly requires it.
