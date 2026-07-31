# Testing Zod Schemas

Use this reference when schema behavior tests are a primary deliverable. Follow the owning workspace's test runner, fixture style, and commands; do not introduce a generic test harness.

## Contents

- Build the behavior matrix
- Assert parsing results
- Test errors and async behavior
- Exercise the boundary
- Use snapshots, generators, and benchmarks selectively
- Avoid brittle coverage

## Build The Behavior Matrix

Derive cases from the runtime contract and its consumer rather than from the schema's implementation details.

| Contract surface | Representative cases |
| --- | --- |
| Accepted input | Minimal and complete valid values |
| Required fields | Missing keys and explicit `undefined` |
| Constraints | Exact minimum and maximum, just inside, and just outside |
| Absence | Omitted, `undefined`, and `null` according to the contract |
| Unknown keys | Reject, strip, or preserve as selected by the owning boundary |
| Coercion | Accepted source types and misleading values such as string booleans |
| Refinement | Each failure path plus the accepted cross-field combination |
| Transform | Valid input to exact output, plus invalid input before transformation |
| Provider input | Additive provider fields and provider-neutral normalized output |

Use hand-authored deterministic cases by default. Keep fixtures small enough that the relevant contract difference is obvious in review.

## Assert Parsing Results

Prefer table-driven `safeParse()` assertions for synchronous schemas:

```typescript
it.each([
  { input: { email: "person@example.com" }, accepted: true },
  { input: { email: "not-an-email" }, accepted: false },
  { input: {}, accepted: false },
])("parses $input", ({ input, accepted }) => {
  expect(UserInputSchema.safeParse(input).success).toBe(accepted);
});
```

When output matters, narrow the result before asserting it:

```typescript
const result = UserInputSchema.safeParse(input);

expect(result.success).toBe(true);
if (!result.success) {
  throw new Error("Expected UserInputSchema to accept the fixture");
}

expect(result.data).toEqual(expectedOutput);
```

Use `parse()` only when the caller intentionally relies on the thrown `ZodError`. A rejection test that only needs success or failure should not couple itself to exception handling.

## Test Errors And Async Behavior

Assert the narrowest stable surface consumed by the application:

- Use `error.issues` for required issue codes and paths.
- Use `z.flattenError()` for flat form contracts.
- Use `z.treeifyError()` for nested error contracts.
- Assert exact messages only when they are user-facing or externally observable.
- Avoid snapshots of a raw `ZodError`; they include more implementation detail than most consumers own.

For async refinements or transforms, await `safeParseAsync()`:

```typescript
const result = await UniqueEmailSchema.safeParseAsync("taken@example.com");

expect(result.success).toBe(false);
if (!result.success) {
  expect(result.error.issues).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ path: [], message: "Already registered" }),
    ])
  );
}
```

Stub the async dependency at its owner and cover accepted, rejected, and dependency-failure behavior only when the boundary contract distinguishes those outcomes.

## Exercise The Boundary

Schema unit tests prove the schema in isolation. Add a focused boundary test when the requested behavior also depends on wiring:

- Verify the route, action, form, webhook, or adapter actually invokes validation.
- Verify invalid input cannot reach a database write, provider call, or other side effect.
- Verify authorization remains separate from validation.
- Verify the boundary returns its documented status and narrowest validated error DTO.
- Verify provider payloads accept irrelevant additive fields when the provider schema is meant to strip them.
- Verify transforms and normalization produce the consumer-facing output, not the provider wire shape.

Do not duplicate schema logic in integration-test expected values. Assert the observable contract at the boundary.

## Use Snapshots, Generators, And Benchmarks Selectively

Use a JSON Schema snapshot or exported-schema diff only when schema shape is an intentional reviewed contract. Keep the generator and snapshot owner in the same workspace as the schema, and make updates explicit in code review.

Use property-based or schema-generated fixtures only when the workspace already owns the dependency and the test has a deterministic seed, bounded run count, and a property stronger than "generated data parses." Preserve the seed in failure output so the case is reproducible.

Keep performance measurements out of ordinary unit tests. Add a benchmark only for a demonstrated hot path, use the workspace's benchmark harness, and compare against a reviewed baseline instead of a machine-dependent fixed millisecond threshold.

## Avoid Brittle Coverage

- Do not inspect `._def`, internal type names, or other private schema representation.
- Do not repeat the schema's regular expressions, ranges, or branching logic in assertions.
- Do not treat every message string as stable when the consumer only uses a code or path.
- Do not test only happy paths, only middle-of-range values, or only fully populated objects.
- Do not infer that a nullable field is optional or that an optional field accepts `null`.
- Do not use text-search tests to enforce import architecture; use the repository's structural or dependency tooling.
- Do not add broad snapshots, random generators, or fixed performance thresholds as a substitute for contract-derived cases.
