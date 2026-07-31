---
name: schema-contract-change
description: Migrate domain, provider, Next.js, Supabase, or generated schema contracts.
---

# Schema Contract Change

## Contract

Classify, implement, migrate, and validate a schema change in its domain-owned boundary. Apply the root `## Schema Contracts` policy and the complete applicable owner instruction chain before editing. Treat a runtime schema as a public contract only when an explicit boundary consumes or returns it, and preserve existing field semantics unless the request deliberately changes them.

Load the `zod` skill for Zod API, schema-design, and focused schema-behavior test decisions.

## Use When

- Moving a schema into its canonical domain, use-case, provider, app-local, or database owner.
- Changing an explicit package contract or export subpath.
- Updating a provider wire schema and its provider-neutral normalization.
- Adding or changing an App Router Route Handler, Server Function, webhook, or its validated DTO.
- Changing declarative Supabase schema files or regenerating database types.

Do not use this skill for an internal type refactor with no runtime or exported boundary.

## Classify The Owner

| Meaning | Canonical location |
| --- | --- |
| Domain invariant | `packages/<domain>/src/domain/<concept>.schema.ts` |
| Use-case boundary | `packages/<domain>/src/contracts/<operation>.contract.ts` |
| Provider wire shape | `packages/<domain>/src/integrations/<provider>/<operation>.schema.ts` |
| App-local form or route | Co-located with the owning feature or route |
| Database schema | Ordered files under `supabase/schemas/` |
| Generated database types | `@anilize/db/types`, refreshed with `pnpm db:types` |
| Authorized partner API | `packages/<domain>/src/public/v<version>/` |

## Direct Workflow

1. Read the current schema, every explicit boundary consumer, the complete applicable `AGENTS.md` chain, the owning package manifest, and representative tests. Invoke `$ast-grep` for bounded source-level declarations, imports, exports, calls, and schema consumers.
2. Decide whether the change affects runtime validation, an internal use-case contract, a provider payload, a package export, a public API, or generated database types. State uncertainty instead of promoting an internal schema to a contract by inference.
3. Edit the single canonical owner. Infer schema-owned input and output types from the schema and keep authorization separate from validation.
4. For provider payloads, accept only fields the adapter consumes and normalize them to provider-neutral domain or use-case output before returning.
5. For cross-workspace use, expose an explicit package subpath and migrate real consumers. Do not add a schema barrel, package-root compatibility export, or cross-package relative import.
6. For a Next.js boundary, parse untrusted input before use, authenticate protected work, authorize mutations separately, validate the returned DTO, and preserve raw-body signature verification before provider parsing for webhooks.
7. For database changes, edit the explicitly ordered declarative owner, place reference or bucket-row DML in a new immutable migration, and regenerate owned types through `pnpm db:types`; never hand-author the generated `Database` surface.
8. Remove the replaced owner only after `$ast-grep` proves no structural consumer, compatibility alias, barrel, or transitional export remains and compiler-backed validation confirms semantic consumers are migrated.

## Validation

Test the observable contract with:

- minimal and complete valid values;
- missing, invalid, boundary, optional, and nullable values;
- expected unknown-field behavior;
- input-to-output transforms and structured errors;
- additive provider fields plus provider-neutral normalization, when relevant.

Run the owning test target and typecheck first. For a database change, reset local Supabase, run `pnpm db:schema:check`, regenerate with `pnpm db:types`, run `pnpm db:types:check`, run `pnpm db:lint`, execute pgTAP, and inspect advisors and the generated diff.

## Boundaries

- Do not create a generic schema package or a second contract owner.
- Do not expose a provider wire schema from a package manifest.
- Do not create partner-facing contracts without explicit authorization and a versioned public owner.
- Verify webhook signatures against the raw body before parsing.
- Return the narrowest validated DTO required by the boundary.
