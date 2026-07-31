# Validation & Error Testing

Patterns for testing validation errors, `returnValidationErrors`, and server errors in next-safe-action.

## Testing Validation Errors on Actions

```ts
import { describe, it, expect } from "vitest";
import { createUser } from "@/app/actions";

describe("createUser validation", () => {
  it("returns validation errors on invalid input", async () => {
    const result = await createUser({ name: "", email: "not-an-email" });

    expect(result.data).toBeUndefined();
    expect(result.validationErrors).toBeDefined();
    expect(result.validationErrors?.email?._errors).toContain("Invalid email");
  });

  it("returns server error on duplicate email", async () => {
    // Setup: create first user
    await createUser({ name: "Alice", email: "alice@example.com" });

    // Attempt duplicate
    const result = await createUser({
      name: "Bob",
      email: "alice@example.com",
    });

    // If using returnValidationErrors:
    expect(result.validationErrors?.email?._errors).toContain(
      "Email already in use"
    );

    // OR if using throw + handleServerError:
    // expect(result.serverError).toBe("Email already in use");
  });
});
```

## Validation Error Utilities

```ts
import {
  flattenValidationErrors,
  formatValidationErrors,
} from "next-safe-action";

describe("validation error utilities", () => {
  const formatted = {
    _errors: ["Form error"],
    email: { _errors: ["Invalid email"] },
    name: { _errors: ["Too short", "Must start with uppercase"] },
  };

  it("flattenValidationErrors", () => {
    const flattened = flattenValidationErrors(formatted);

    expect(flattened.formErrors).toEqual(["Form error"]);
    expect(flattened.fieldErrors.email).toEqual(["Invalid email"]);
    expect(flattened.fieldErrors.name).toEqual([
      "Too short",
      "Must start with uppercase",
    ]);
  });

  it("formatValidationErrors is identity", () => {
    expect(formatValidationErrors(formatted)).toBe(formatted);
  });
});
```

## Key Assertions

| Scenario | What to check |
| --- | --- |
| Valid input | `result.data` defined, `serverError` and `validationErrors` undefined |
| Invalid input | `result.data` undefined, `result.validationErrors` has field-level `_errors` |
| `returnValidationErrors` | Same shape as Zod validation errors -- check `validationErrors?.field?._errors` |
| Server error | `result.serverError` is a string (formatted by `handleServerError`) |
| Bind args invalid | Action throws `ActionBindArgsValidationError` |
